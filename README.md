# findmypylibrary

Describe a use case in plain English, get a ranked shortlist of real PyPI packages — grounded in real download and maintenance data, not an LLM's memory. Works offline once the snapshot is downloaded; no API key required.

## Install

```bash
pip install findmypylibrary
```

## Use

```bash
findmypylibrary refresh              # one-time, a few seconds: downloads this month's snapshot (~11 MB)
findmypylibrary "resize images"      # instant, offline, ranked results
findmypylibrary "parse pdf files" -n 5 --json   # machine-readable output
findmypylibrary status               # how many packages, how old is the snapshot
```

From Python:

```python
import findmypylibrary

for package, score in findmypylibrary.search("parse pdf files", top_n=5):
    print(package["name"], package["download_count"], round(score, 2))
```

## Where the data comes from

The snapshot covers the 15,000 most-downloaded PyPI packages. It is rebuilt on the 1st of every
month by [this repo's GitHub Actions workflow](.github/workflows/monthly-refresh.yml) from two
public sources, no API key needed:

1. the top-downloaded package list from
   [top-pypi-packages](https://github.com/hugovk/top-pypi-packages) (name + 30-day downloads), and
2. each package's summary, keywords, topic classifiers, README excerpt and latest release date
   from the PyPI JSON API.

The workflow publishes it to a fixed GitHub Release (`snapshot-latest`) only if at least 95% of
packages were fetched **and** the ranking quality check passes. `findmypylibrary refresh`
downloads that file, validates it, and only then replaces your local copy in
`~/.cache/findmypylibrary/` (or `$XDG_CACHE_HOME/findmypylibrary/`).

`findmypylibrary refresh --build-locally` does the same crawl on your machine instead (a few
minutes, ~15,000 requests to PyPI). `refresh` never falls back to that crawl on its own. You are
warned when your snapshot is more than 45 days old.

## How ranking works

Fully offline and lexical — no model, no embeddings.

1. **Recall and gate.** Stopwords are dropped from your query, a small synonym list is applied
   (postgres↔postgresql, excel↔xlsx, chart↔plot, …), and the terms are matched against a stemmed
   full-text index ("images" finds "Imaging", "plot" finds "plotting"). Name, summary, keywords
   and topic classifiers count in full; README text is heavily discounted because READMEs are
   noisy. A package must match at least half of your terms and not be far below the best match.
2. **Rank.** Survivors are ordered by a blend of relevance, 30-day downloads and how recently
   the package was released.

Known limits: matching is lexical, so a package is only found if its own metadata or README
intro uses your words (or a stem/synonym of them). Results for very short queries lean towards
popular packages.

## Development

```bash
pip install -e ".[dev]"
pytest                      # tests + coverage gate (>= 90%)
ruff check src tests scripts && ruff format --check src tests scripts
mypy src
findmypylibrary golden      # ranking quality check against your local snapshot
```

`src/findmypylibrary/golden_queries.json` holds everyday queries with well-known right answers.
The test suite runs them offline against a committed slice of the real corpus
(`tests/fixtures/golden_corpus.json.gz`, rebuilt with `python scripts/build_golden_fixture.py`);
the monthly workflow runs them against the full snapshot before publishing. Add a query there
first whenever you change ranking.

If you change the snapshot tables, bump `SCHEMA_VERSION` in `cache.py`: the snapshot file name
carries it, so older installs keep downloading a layout they understand.

## Releasing

Bump `__version__` in `src/findmypylibrary/__init__.py`, update `CHANGELOG.md`, then:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

[The publish workflow](.github/workflows/publish.yml) runs the full gate suite, checks the tag
matches the version, and uploads to PyPI via Trusted Publishing (no token stored anywhere).

## License

MIT
