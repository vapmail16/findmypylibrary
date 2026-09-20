# findmypylibrary

Describe a use case in plain English, get a ranked shortlist of real PyPI packages — grounded in real download and maintenance data, not an LLM's memory. Works offline once the local snapshot is built; no API key required.

## Install

```bash
pip install findmypylibrary
```

## Use

```bash
findmypylibrary refresh              # one-time (few minutes): builds a local snapshot of the
                                      # top 15,000 most-downloaded PyPI packages
findmypylibrary "parse messy pdfs"   # instant, offline, ranked results
```

`refresh` pulls the current top-downloaded package list (from the public
[top-pypi-packages](https://github.com/hugovk/top-pypi-packages) dataset) plus each
package's summary and latest release date from the PyPI JSON API, and caches it locally
in `~/.cache/findmypylibrary/snapshot.sqlite` (or `$XDG_CACHE_HOME/findmypylibrary/`).

Every query after that runs fully offline against the cached snapshot. Ranking is two-stage,
not a flat blend:

1. **Relevance gate** — BM25 lexical match against each package's name (weighted above
   summary/keywords), with a small built-in synonym list (e.g. excel↔xlsx↔spreadsheet).
   Candidates far below the best match for the query are dropped as noise.
2. **Popularity rank** — among the packages that clear the gate, rank by 30-day download
   count, with recency of the last release as a tiebreaker.

(A flat relevance+popularity+maintenance blend has a known failure mode: BM25 favors short,
keyword-dense summaries over a longer, more informative match, and that gap can swamp a
100x-larger popularity signal. Gating on relevance first, then ranking by popularity, fixes it.)

Re-run `findmypylibrary refresh` any time you want an up-to-date snapshot. Use
`findmypylibrary refresh --limit 2000` for a much faster (but narrower) refresh.

## License

MIT
