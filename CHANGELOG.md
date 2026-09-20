# Changelog

## 0.2.0 — 2026-09-20

First functional release (0.0.1 was a name placeholder).

### Ranking
- Snapshot now ships a stemmed full-text index (SQLite FTS5, porter): "images" finds Pillow's
  "Imaging", "plot" finds matplotlib's "plotting". Queries take ~25 ms.
- Topic classifiers and a cleaned README excerpt are indexed alongside name, summary and
  keywords. README-only evidence is heavily discounted (boto3's README mentions "unit tests").
- Stopword removal, a curated synonym list, a term-coverage gate, and a relevance /
  popularity / recency blend tuned on real data.
- Golden-query suite: 40 everyday queries with well-known answers. 40/40 pass on the full
  snapshot. Runs offline in tests and as a publish gate in the monthly workflow.

### Robustness
- `refresh` downloads a prebuilt monthly snapshot from a fixed `snapshot-latest` release
  (gzipped, schema-versioned file name) instead of crawling PyPI; it never crawls unless
  `--build-locally` is passed.
- A crawl that fetched under 95% of packages is discarded; a corrupt or incompatible download
  never replaces a good snapshot.
- Missing, corrupt or wrong-version snapshots and network failures give an actionable message
  instead of a traceback.
- Fixed: opening the snapshot through a read-only URI made a concurrent refresh fail with
  `SQLITE_IOERR_LOCK`.
- `status` command, stale-snapshot warning (45 days), `--json` output, `findmypylibrary.search()`
  Python API.

### Hardening after an independent audit
- Fixed a crash (`ValueError`) when a multi-term query had candidates but none covered half its
  terms, e.g. a query with two typos.
- One unexpected upstream response (403, HTML body, non-object JSON, list-valued keywords) now
  costs one package instead of aborting the whole crawl; redirects are followed; a changed
  top-package-list format gives an actionable error.
- `--limit` and `-n` must be at least 1 (`--limit 0` used to wipe the snapshot); an empty crawl
  is never saved.
- Damage beyond a snapshot's first page is detected (`PRAGMA quick_check`) before a download is
  swapped in, and reported as an actionable error if it appears at query time.
- Non-ASCII queries are tokenised correctly ("résumé" is one word and matches "resume").
- `findmypylibrary status bar widget` and `-n 2 parse pdf` are searches, not usage errors.
- Searching no longer imports the HTTP stack (~140 ms faster start-up).
- CLI tests now fail on any uncaught exception (they previously could not detect a crash); the
  golden fixture also contains popular competitors and a random corpus sample, not only
  packages the ranker already favoured.
- Publishing requires the tag to be on master and the matching snapshot asset to exist.

### Process
- CI on Linux/macOS/Windows × Python 3.10–3.13: tests with a 90% coverage gate, ruff, mypy.
- Monthly refresh workflow with crawl and ranking-quality gates before publishing.
- Tag-driven PyPI release via Trusted Publishing.
