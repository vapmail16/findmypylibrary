# findmypylibrary

Describe a use case in plain English, get a ranked shortlist of real PyPI packages — grounded in real download and maintenance data, not an LLM's memory. Works offline once the snapshot is downloaded; no API key required.

## Install

```bash
pip install findmypylibrary
```

## Use

```bash
findmypylibrary refresh              # first run, then monthly: downloads the latest snapshot (~11 MB)
findmypylibrary "resize images"      # instant, offline, ranked results
findmypylibrary "parse pdf files" -n 5 --json   # machine-readable output
findmypylibrary status               # how many packages, how old is the snapshot
```

A bare query is a search, so `findmypylibrary status bar widget` searches too. To search for
exactly a command word, be explicit: `findmypylibrary search status`.

From Python:

```python
import findmypylibrary

for package, score in findmypylibrary.search("parse pdf files", top_n=5):
    print(package["name"], package["downloads_30d"], round(score, 2))
```

`search()` returns `(package, score)` pairs, best first. `package` is a dict with `name`,
`summary`, `keywords`, `topics`, `homepage`, `version`, `last_release` and `downloads_30d`;
`score` is 0–1 and only comparable within one query. It raises `findmypylibrary.SnapshotError`
when there is no usable snapshot (run `findmypylibrary refresh`) and `ValueError` for `top_n < 1`.

## Where the data comes from

The snapshot covers the 15,000 most-downloaded PyPI packages. It is rebuilt on the 1st of every
month by [this repo's GitHub Actions workflow](https://github.com/vapmail16/findmypylibrary/blob/master/.github/workflows/monthly-refresh.yml) from two
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

How good is it? Measured on the real snapshot with 95 everyday queries that each have a
well-known right answer: 90 put one in the top 5 (95%). That number is flattering, because 40 of
the queries were used to tune the ranking. On the 55 queries written afterwards, with the
expected answers fixed before looking at any result, it was 49 of 55 (89%). Expect roughly nine
searches in ten to show a package you would recognise as right.

Known limits:

- Matching is lexical. A package is found only if its own metadata or README intro uses your
  words, a stem of them, or a listed synonym. pandas never says "dataframe" in its metadata, so
  "dataframes" returns polars and narwhals, while "data analysis" returns pandas first. numpy
  does not appear for "linear algebra", nor boto3 for "upload files to s3".
- README-only matches are discounted on purpose. Raising them enough to surface pandas for
  "dataframes" also surfaces boto3 for "unit testing" (its README says "run the unit tests");
  the two are indistinguishable lexically, so precision wins.
- A package earns popularity credit only for the rare, informative words of your query that
  it matches. Without that, idna and platformdirs (over a billion downloads each) top
  "gui application" by matching only "application".

## Development

```bash
pip install -e ".[dev]"
pytest --cov-fail-under=90  # full suite with the coverage gate (CI runs this)
pytest tests/test_rank.py   # a single file runs without tripping the gate
ruff check src tests scripts && ruff format --check src tests scripts
mypy src
findmypylibrary golden      # ranking quality check against your local snapshot
```

`src/findmypylibrary/golden_queries.json` holds 95 everyday queries with well-known right answers.
The test suite runs them offline against a committed slice of the real corpus
(`tests/fixtures/golden_corpus.json.gz`, rebuilt with `python scripts/build_golden_fixture.py`);
the monthly workflow runs them against the full snapshot before publishing. Add a query there
first whenever you change ranking, and judge a ranking change on *new* queries whose expected
answers you wrote down before running them: every query already in the file has been tuned on.

If you change the snapshot tables, bump `SCHEMA_VERSION` in `cache.py`: the snapshot file name
carries it, so older installs keep downloading a layout they understand.

## Releasing

Bump `__version__` in `src/findmypylibrary/__init__.py`, update `CHANGELOG.md`, then:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

[The publish workflow](https://github.com/vapmail16/findmypylibrary/blob/master/.github/workflows/publish.yml) runs the full gate suite, checks that
the tag is on master and matches the version, checks that the snapshot this version downloads
is already published, and uploads to PyPI via Trusted Publishing (no token stored anywhere).

After a `SCHEMA_VERSION` bump: merge to master, run the "Monthly snapshot refresh" workflow
(Actions tab, or `gh workflow run monthly-refresh.yml`), and only then push the tag.

## License

MIT
