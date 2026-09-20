# findmypylibrary

Describe a use case in plain English, get a ranked shortlist of real PyPI packages — grounded in real download and maintenance data, not an LLM's memory. Works offline once the snapshot is downloaded; no API key required.

## Install

```bash
pip install findmypylibrary
```

Needs Python 3.10 or newer on Linux, macOS or Windows. Nothing else to set up: no account, no
API key. (It uses SQLite's FTS5 full-text engine, which standard CPython builds include.)

## Quick start

```bash
findmypylibrary refresh                    # once, then about monthly: downloads the snapshot (~11 MB)
findmypylibrary "fuzzy string matching"    # instant and offline from here on
```

```text
1. RapidFuzz  (score 0.90)
   rapid fuzzy string matching
   downloads/30d: 163,835,611  last release: 2026-08-30
   https://pypi.org/project/RapidFuzz/
2. pfzy  (score 0.81)
   Python port of the fzy fuzzy string matching algorithm
   downloads/30d: 27,024,226  last release: 2022-01-28
   https://pypi.org/project/pfzy/
3. fuzzywuzzy  (score 0.73)
   Fuzzy string matching in python
   downloads/30d: 14,789,165  last release: 2020-02-13
   https://pypi.org/project/fuzzywuzzy/
```

Each result shows the package's own one-line summary, its downloads over the last 30 days and
the date of its latest release, so you can judge popularity and maintenance at a glance. The
score (0–1) only ranks results within one query; it is not comparable between queries.

Your queries never leave your machine. The only network access is `refresh`.

## Commands

| Command | What it does |
|---|---|
| `findmypylibrary "<query>"` | Search. Same as `findmypylibrary search "<query>"`; quotes are optional. |
| `findmypylibrary refresh` | Download the latest prebuilt snapshot (one request, a few seconds). |
| `findmypylibrary refresh --build-locally [--limit N]` | Build the snapshot yourself by crawling PyPI (~15,000 requests, a few minutes). `--limit` keeps only the top N packages. |
| `findmypylibrary status` | How many packages the snapshot holds, when it was built, where it is stored. |
| `findmypylibrary golden` | Run the built-in ranking quality check against your snapshot. |
| `findmypylibrary --version`, `-h` / `--help` | Version and help (each command has its own `--help`). |

Search options: `-n, --num N` limits the number of results (default 10); `--json` prints JSON.
Options can go before or after the query: `findmypylibrary -n 3 "parse pdf"` works.

A bare query is always a search, so `findmypylibrary status bar widget` searches too. To search
for exactly a command word, be explicit: `findmypylibrary search status`.

## Writing a good query

Matching is on words, not meaning, so describe the task the way a package author would describe
their library:

- Name the thing and the action: `parse pdf files`, `resize images`, `retry failed function calls`.
- Two to four meaningful words work best. Filler such as "I need a python library to" is ignored.
- Word forms do not matter (`images` finds "Imaging", `plotting` finds "plot"), and a few common
  synonyms are built in (`postgres`/`postgresql`, `excel`/`xlsx`, `chart`/`plot`).
- If the famous package you expected is missing, try the words its own description uses:
  `dataframes` finds polars, while `data analysis` finds pandas.

## JSON output

```bash
findmypylibrary "fuzzy string matching" -n 1 --json
```

```json
[
  {
    "name": "RapidFuzz",
    "summary": "rapid fuzzy string matching",
    "score": 0.9047,
    "downloads_30d": 163835611,
    "last_release": "2026-08-30T21:41:54.195801Z",
    "version": "3.14.6",
    "homepage": "https://github.com/rapidfuzz/RapidFuzz",
    "url": "https://pypi.org/project/RapidFuzz/"
  }
]
```

Only JSON goes to stdout (`[]` when nothing matches); warnings and errors go to stderr. Exit
codes: `0` success (including no match), `1` a problem such as a missing snapshot, `2` wrong
usage.

## From Python

```python
import findmypylibrary

try:
    for package, score in findmypylibrary.search("parse pdf files", top_n=5):
        print(package["name"], package["downloads_30d"], round(score, 2))
except findmypylibrary.SnapshotError as exc:
    print(exc)  # no usable snapshot yet: run `findmypylibrary refresh`
```

`search()` returns `(package, score)` pairs, best first. `package` is a dict with `name`,
`summary`, `keywords`, `topics`, `homepage`, `version`, `last_release` and `downloads_30d`;
`score` is 0–1 and only comparable within one query. It raises `findmypylibrary.SnapshotError`
when there is no usable snapshot and `ValueError` for `top_n < 1`.

## Troubleshooting

| You see | What it means and what to do |
|---|---|
| `No local snapshot yet.` | First use. Run `findmypylibrary refresh`. |
| `The local snapshot was built for a different version.` | You upgraded findmypylibrary. Run `findmypylibrary refresh`. |
| `The local snapshot is unreadable / damaged.` | The file is corrupt (disk problem, interrupted copy). Run `findmypylibrary refresh`; it replaces the file. |
| `Note: your snapshot is N days old.` | Still works, results are just dated. Run `findmypylibrary refresh`. If it stays old, upgrade with `pip install -U findmypylibrary`. |
| `Could not download the prebuilt snapshot` / `No prebuilt snapshot available` | GitHub was unreachable or the release is being replaced (it takes a few seconds once a month). Your existing snapshot is untouched. Retry, or use `refresh --build-locally`. Behind a proxy, set `HTTPS_PROXY`. |
| `Only X of Y packages could be fetched` | A local build lost its connection. The partial crawl was discarded and your existing snapshot kept. Retry. |
| `Cannot use the cache directory` | The cache location is not writable. Fix its permissions or set `XDG_CACHE_HOME` to a writable directory. |
| `No packages matched that description.` | No package uses those words. See "Writing a good query". |

The snapshot lives in `~/.cache/findmypylibrary/snapshot.sqlite` on every platform (on Windows
that is `C:\Users\<you>\.cache\findmypylibrary\`), or under `$XDG_CACHE_HOME/findmypylibrary/` if
that variable is set. To remove everything the tool stored, delete that folder.

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
