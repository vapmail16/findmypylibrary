"""Ranks cached packages against a free-text query.

Two-stage design, not a flat weighted blend:

1. **Recall** — BM25 lexical match (query expanded with a small synonym list)
   over each package's name (weighted higher than body text) and
   summary/keywords. This is a *gate*: candidates scoring below
   ``RELEVANCE_GATE_RATIO`` of the top relevance score are dropped.
2. **Rank** — among the candidates that cleared the gate, rank primarily by
   download popularity (with recency and leftover relevance as tiebreakers).

A flat linear blend of relevance/popularity/maintenance has a known failure
mode: BM25 favors short, keyword-dense summaries (e.g. a niche package whose
whole summary is "Read and write Apple Numbers spreadsheets") over a longer,
more informative match (openpyxl's actual Excel summary), and that relevance
gap can swamp a 100x-larger popularity signal under min-max normalization.
Splitting relevance into a gate, then ranking survivors by popularity, fixes
that without adding a model or an external dependency.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

TOKEN_RE = re.compile(r"[a-z0-9]+")

NAME_FIELD_BOOST = 3
RELEVANCE_GATE_RATIO = 0.5

POPULARITY_WEIGHT = 0.6
RELEVANCE_TIEBREAK_WEIGHT = 0.3
MAINTENANCE_WEIGHT = 0.1

# Small, curated set of domain synonyms so a query using one common term still
# finds packages whose summary uses a close relative of it. Not exhaustive by
# design — this is meant to fix common recall misses, not be a thesaurus.
SYNONYMS: dict[str, list[str]] = {
    "excel": ["excel", "xlsx", "xls", "spreadsheet", "spreadsheets"],
    "spreadsheet": ["spreadsheet", "spreadsheets", "excel", "xlsx"],
    "spreadsheets": ["spreadsheet", "spreadsheets", "excel", "xlsx"],
    "pdf": ["pdf", "pdfs", "document", "documents"],
    "pdfs": ["pdf", "pdfs", "document", "documents"],
    "image": ["image", "images", "photo", "photos", "picture", "pictures"],
    "images": ["image", "images", "photo", "photos", "picture", "pictures"],
    "picture": ["picture", "pictures", "image", "images", "photo"],
    "cron": ["cron", "schedule", "scheduler", "scheduling"],
    "schedule": ["schedule", "scheduler", "scheduling", "cron"],
    "database": ["database", "db", "sql"],
    "db": ["database", "db", "sql"],
    "async": ["async", "asyncio", "concurrent", "concurrency"],
}


def tokenize(text: str | None) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())


def expand_query(tokens: list[str]) -> list[str]:
    """Add synonym tokens for common domain terms found in the query."""
    expanded = list(tokens)
    for token in tokens:
        expanded.extend(SYNONYMS.get(token, []))
    return expanded


class BM25:
    def __init__(self, docs: list[list[str]]):
        self.n = len(docs)
        self.doc_lens = [len(d) for d in docs]
        self.avgdl = (sum(self.doc_lens) / self.n) if self.n else 0.0
        self.index: dict[str, dict[int, int]] = {}
        for i, doc in enumerate(docs):
            for term, freq in Counter(doc).items():
                self.index.setdefault(term, {})[i] = freq
        self.idf = {
            term: math.log(1 + (self.n - len(postings) + 0.5) / (len(postings) + 0.5))
            for term, postings in self.index.items()
        }

    def score(self, query_tokens: list[str], k1: float = 1.5, b: float = 0.75) -> dict[int, float]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(query_tokens):
            postings = self.index.get(term)
            if not postings:
                continue
            idf = self.idf[term]
            for doc_idx, freq in postings.items():
                dl = self.doc_lens[doc_idx]
                denom = freq + k1 * (1 - b + b * dl / self.avgdl)
                scores[doc_idx] += idf * (freq * (k1 + 1)) / denom
        return dict(scores)


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _recency_score(last_release: str | None) -> float:
    if not last_release:
        return 0.0
    try:
        released = datetime.fromisoformat(last_release.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    days_since = (datetime.now(timezone.utc) - released).days
    return 1.0 / (1.0 + max(days_since, 0) / 365.0)


def _field_weighted_doc(package: dict) -> list[str]:
    name_tokens = tokenize(package["name"]) * NAME_FIELD_BOOST
    body_tokens = tokenize(package.get("summary")) + tokenize(package.get("keywords"))
    return name_tokens + body_tokens


def search(query: str, packages: list[dict], top_n: int = 10) -> list[tuple[dict, float]]:
    """Rank cached packages for a free-text use-case description.

    Stage 1 (recall + gate): BM25 relevance, query expanded with synonyms,
    package name weighted above summary/keywords. Anything below
    RELEVANCE_GATE_RATIO of the top relevance score is dropped as noise.

    Stage 2 (rank): survivors are ordered by popularity, with recency and
    leftover relevance as tiebreakers — so a hugely more-downloaded package
    doesn't lose to a short, keyword-dense but far less popular one.
    """
    docs = [_field_weighted_doc(p) for p in packages]
    bm25 = BM25(docs)
    query_tokens = expand_query(tokenize(query))
    raw_scores = bm25.score(query_tokens)
    if not raw_scores:
        return []

    top_relevance = max(raw_scores.values())
    gate = top_relevance * RELEVANCE_GATE_RATIO
    qualified = [(idx, score) for idx, score in raw_scores.items() if score >= gate]

    relevance_norm = _minmax([score for _, score in qualified])
    popularity_norm = _minmax(
        [math.log10((packages[idx]["download_count"] or 0) + 1) for idx, _ in qualified]
    )
    recency = [_recency_score(packages[idx].get("last_release")) for idx, _ in qualified]

    blended = [
        (
            packages[idx],
            POPULARITY_WEIGHT * pop + RELEVANCE_TIEBREAK_WEIGHT * rel + MAINTENANCE_WEIGHT * rec,
        )
        for (idx, _), rel, pop, rec in zip(
            qualified, relevance_norm, popularity_norm, recency, strict=True
        )
    ]
    blended.sort(key=lambda pair: pair[1], reverse=True)
    return blended[:top_n]
