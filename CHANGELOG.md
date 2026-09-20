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

### Process
- CI on Linux/macOS/Windows × Python 3.10–3.13: tests with a 90% coverage gate, ruff, mypy.
- Monthly refresh workflow with crawl and ranking-quality gates before publishing.
- Tag-driven PyPI release via Trusted Publishing.
