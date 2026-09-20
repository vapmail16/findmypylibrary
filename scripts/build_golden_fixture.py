"""Rebuild tests/fixtures/golden_corpus.json.gz from the local snapshot + PyPI.

The fixture is a slice of the real corpus chosen so the offline golden test is not
circular (i.e. not just "packages the current ranker already likes"). Per golden
query it takes the expected answers, the ranker's top results, AND the most
downloaded packages matching any query term regardless of how the ranker scores
them (a popular package wrongly pulled in is the main ranking risk). A seeded
random sample of the whole corpus keeps term statistics realistic. Descriptions are not stored in
the snapshot, so this re-fetches those packages from PyPI (~1,000 requests).

    findmypylibrary refresh
    python scripts/build_golden_fixture.py
"""

from __future__ import annotations

import gzip
import json
import random
import sqlite3
from pathlib import Path

from findmypylibrary import cache, fetch, golden, rank

COMPETITORS_PER_QUERY = 25
POPULAR_MATCHES_PER_QUERY = 40
RANDOM_SAMPLE = 600
SEED = 20260920
FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "golden_corpus.json.gz"


def main() -> None:
    conn = sqlite3.connect(cache.db_path())
    downloads = {
        golden.normalize(name): (name, count)
        for name, count in conn.execute("SELECT name, download_count FROM packages")
    }
    conn.close()

    wanted: set[str] = set()
    for entry in golden.load_queries():
        wanted.update(golden.normalize(n) for n in entry["expect_any"])
        results = rank.search(entry["query"], top_n=COMPETITORS_PER_QUERY)
        wanted.update(golden.normalize(pkg["name"]) for pkg, _ in results)

        terms = rank.content_terms(entry["query"])
        match_expr = " OR ".join(f'"{t}"' for term in terms for t in rank.term_group(term))
        matches = cache.search_candidates(match_expr, limit=100_000)
        matches.sort(key=lambda pkg: pkg["download_count"] or 0, reverse=True)
        wanted.update(golden.normalize(p["name"]) for p in matches[:POPULAR_MATCHES_PER_QUERY])

    wanted.update(random.Random(SEED).sample(sorted(downloads), RANDOM_SAMPLE))

    rows = [
        {"project": downloads[key][0], "download_count": downloads[key][1]}
        for key in sorted(wanted)
        if key in downloads
    ]
    print(f"Fetching {len(rows)} packages from PyPI...")
    packages = fetch.run_refresh_sync(rows)
    with gzip.open(FIXTURE, "wt", encoding="utf-8") as f:
        json.dump(packages, f, ensure_ascii=False, sort_keys=True)
    print(f"Wrote {len(packages)} packages to {FIXTURE} ({FIXTURE.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
