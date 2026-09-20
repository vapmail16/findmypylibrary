"""Golden-query evaluation: does the current snapshot + ranking still return a
well-known right answer for everyday queries?

One source of truth (golden_queries.json) serves three users: the test suite
(against a committed fixture corpus), the monthly workflow (a quality gate
before a new snapshot is published) and anyone tuning the ranking.
"""

from __future__ import annotations

import json
import re
from importlib import resources

from . import rank

TOP_N = 5
MIN_PASS_RATE = 0.85


def normalize(name: str) -> str:
    """PyPI name normalisation: case-insensitive, runs of - _ . are equivalent."""
    return re.sub(r"[-_.]+", "-", name).lower()


def load_queries() -> list[dict]:
    text = resources.files("findmypylibrary").joinpath("golden_queries.json").read_text("utf-8")
    queries: list[dict] = json.loads(text)
    return queries


def evaluate(top_n: int = TOP_N) -> dict:
    """Run every golden query against the local snapshot and report the pass rate."""
    failures = []
    queries = load_queries()
    for entry in queries:
        got = [normalize(pkg["name"]) for pkg, _ in rank.search(entry["query"], top_n=top_n)]
        expected = {normalize(name) for name in entry["expect_any"]}
        if not expected.intersection(got):
            failures.append({"query": entry["query"], "got": got})
    passed = len(queries) - len(failures)
    return {
        "passed": passed,
        "total": len(queries),
        "pass_rate": passed / len(queries),
        "failures": failures,
    }
