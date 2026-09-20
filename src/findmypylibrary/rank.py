"""Ranks snapshot packages against a free-text query. Fully offline, no model.

Stage 1 — recall and gate. The query is reduced to content terms (stopwords
dropped), each expanded with a few domain synonyms, and matched against the
snapshot's stemmed full-text index. Name, summary, keywords and topics count in
full; the README excerpt is heavily discounted because READMEs are noisy (boto3's
says "unit tests"). A candidate survives only if it matches at least half of the
query's terms and is not far below the best match.

The constants below were chosen on the real 15k snapshot with the golden-query
set (golden.py): pass rate is flat across a wide band around them, so they are
not knife-edge. Re-run `findmypylibrary golden` after changing any of them.

Stage 2 — rank. Survivors are ordered by a blend of relevance, download
popularity and release recency. Popularity is what lets pytest beat an obscure
package whose one-line summary happens to repeat the query, but a package only
earns popularity credit to the extent it matches the query's rare, informative
terms; otherwise idna (1.8B downloads) tops "gui application".
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from . import cache

# Unicode letters/digits, so "résumé" stays one word (the index folds accents itself).
TOKEN_RE = re.compile(r"[^\W_]+")

CANDIDATE_LIMIT = 1000
PUBLIC_FIELDS = (
    "name",
    "summary",
    "keywords",
    "topics",
    "homepage",
    "version",
    "last_release",
    "downloads_30d",
)
MIN_TERM_COVERAGE = 0.5
RELEVANCE_GATE_RATIO = 0.25
DESCRIPTION_WEIGHT = 0.5

RELEVANCE_WEIGHT = 0.45
POPULARITY_WEIGHT = 0.45
RECENCY_WEIGHT = 0.10
# How strongly popularity credit is scaled by the share of the query's informative
# (rare) terms a package matches. 0 would let idna top "gui application" by matching
# only "application"; validated on query sets that played no part in choosing it.
COVERAGE_EXPONENT = 0.5

STOPWORDS = frozenset(
    """a an and are as at be by can do for from how i in into is it library lib me module my
    need of on or package packages python some that the this to tool use using want way
    with""".split()
)

# Small, curated domain synonyms for terms stemming cannot connect. Not a
# thesaurus: only add pairs that fix a real, observed recall miss.
_SYNONYM_SETS = [
    ["excel", "xlsx", "xls", "spreadsheet"],
    ["postgres", "postgresql", "psql"],
    ["mongo", "mongodb"],
    ["k8s", "kubernetes"],
    ["js", "javascript"],
    ["db", "database"],
    ["image", "photo", "picture"],
    ["chart", "plot", "visualization"],
    ["scrape", "scraper", "crawl", "crawler", "spider"],
    ["email", "mail", "smtp"],
    ["cli", "commandline"],
    ["auth", "authentication"],
    ["config", "configuration", "settings"],
    ["env", "environment", "dotenv"],
    ["cron", "schedule", "scheduler"],
    ["async", "asyncio", "asynchronous"],
    ["jwt", "jws", "jose"],
    ["encryption", "cryptography", "crypto"],
]


# Two-word phrases whose one-word spelling is common in package metadata.
_COMPOUNDS = {
    ("unit", "test"): "unittest",
    ("time", "zone"): "timezone",
    ("data", "frame"): "dataframe",
    ("web", "socket"): "websocket",
}


def tokenize(text: str | None) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())


def _stem(token: str) -> str:
    """Cheap plural fold, used only to look synonyms up ("emails" -> "email")."""
    for suffix, replacement in (("ies", "y"), ("ing", ""), ("s", "")):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)] + replacement
    return token


SYNONYMS: dict[str, list[str]] = {
    term: [other for other in group if other != term] for group in _SYNONYM_SETS for term in group
}


def content_terms(query: str) -> list[str]:
    """Distinct query tokens in order, minus stopwords (unless nothing else is left)."""
    tokens = list(dict.fromkeys(tokenize(query)))
    meaningful = [t for t in tokens if t not in STOPWORDS]
    return meaningful or tokens


def term_group(term: str) -> list[str]:
    """A query term plus its synonyms; matching any of them counts as matching the term."""
    synonyms = SYNONYMS.get(term) or SYNONYMS.get(_stem(term)) or []
    return [term, *synonyms]


def term_groups(terms: list[str]) -> list[list[str]]:
    """One synonym group per query term. A listed compound counts for both of its words:
    "unit testing" also matches "unittest". (Compounding every adjacent pair was measured
    and rejected: rare accidental compounds inflate the top relevance and gate out good
    packages; 84/95 evaluation queries passed instead of 89/95.)"""
    groups = [term_group(t) for t in terms]
    for i in range(len(terms) - 1):
        compound = _COMPOUNDS.get((_stem(terms[i]), _stem(terms[i + 1])))
        if compound:
            groups[i].append(compound)
            groups[i + 1].append(compound)
    return groups


def _match_expr(group: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in group)


def _recency_score(last_release: str | None) -> float:
    if not last_release:
        return 0.0
    try:
        released = datetime.fromisoformat(last_release.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if released.tzinfo is None:
        released = released.replace(tzinfo=timezone.utc)
    days_since = (datetime.now(timezone.utc) - released).days
    return 1.0 / (1.0 + max(days_since, 0) / 365.0)


def search(query: str, top_n: int = 10) -> list[tuple[dict, float]]:
    """Rank snapshot packages for a free-text use-case description.

    Returns up to top_n (package, score) pairs, best first. score is 0..1 and only
    comparable within one query. package has the keys in PUBLIC_FIELDS.
    Raises SnapshotError when there is no usable local snapshot.
    """
    if top_n < 1:
        raise ValueError(f"top_n must be at least 1, got {top_n}")
    terms = content_terms(query)
    if not terms:
        return []

    group_exprs = [_match_expr(group) for group in term_groups(terms)]
    match_expr = " OR ".join(f"({g})" for g in group_exprs)
    core = cache.search_candidates(match_expr, columns=cache.CORE_COLUMNS, limit=CANDIDATE_LIMIT)
    described = cache.search_candidates(match_expr, columns=("description",), limit=CANDIDATE_LIMIT)

    # README text is noisy (boto3's mentions "unit tests"), so evidence found
    # only in the description counts for much less than name/summary/keywords/topics.
    candidates: dict[int, dict] = {}
    for pkg in core:
        candidates[pkg["id"]] = pkg
    for pkg in described:
        discounted = DESCRIPTION_WEIGHT * pkg["relevance"]
        if pkg["id"] in candidates:
            candidates[pkg["id"]]["relevance"] += discounted
        else:
            candidates[pkg["id"]] = {**pkg, "relevance": discounted}
    if not candidates:
        return []

    ids_per_term = [cache.matching_ids(g) for g in group_exprs]
    corpus_size = cache.snapshot_info()["count"]
    # Rare terms say more about what the user wants than common ones ("gui" vs "application").
    term_weights = [math.log(1 + corpus_size / max(len(ids), 1)) for ids in ids_per_term]
    top_relevance = max(pkg["relevance"] for pkg in candidates.values())
    survivors = []
    for pkg in candidates.values():
        matched = [pkg["id"] in ids for ids in ids_per_term]
        coverage = sum(matched) / len(matched)
        relevant_enough = pkg["relevance"] >= top_relevance * RELEVANCE_GATE_RATIO
        if coverage >= MIN_TERM_COVERAGE and relevant_enough:
            informative = sum(w for w, hit in zip(term_weights, matched, strict=True) if hit)
            survivors.append({**pkg, "informative_coverage": informative / sum(term_weights)})
    if not survivors:
        return []

    # Min-max over the survivors (not a ratio to the max): log-downloads only span
    # ~5-9.5, so a ratio would barely separate a 1M-download package from a 1B one.
    log_downloads = [math.log10((pkg["download_count"] or 0) + 1) for pkg in survivors]
    lowest, highest = min(log_downloads), max(log_downloads)
    scored = []
    for pkg, log_dl in zip(survivors, log_downloads, strict=True):
        popularity = (log_dl - lowest) / (highest - lowest) if highest > lowest else 1.0
        score = (
            RELEVANCE_WEIGHT * (pkg["relevance"] / top_relevance)
            + POPULARITY_WEIGHT * popularity * pkg["informative_coverage"] ** COVERAGE_EXPONENT
            + RECENCY_WEIGHT * _recency_score(pkg["last_release"])
        )
        public = {field: pkg[field] for field in PUBLIC_FIELDS if field != "downloads_30d"}
        public["downloads_30d"] = pkg["download_count"] or 0
        scored.append((public, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_n]
