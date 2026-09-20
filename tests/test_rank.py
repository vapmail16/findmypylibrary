"""Unit tests for the ranking engine.

Written before the two-stage ranking fix (TDD): these assert the real
ranking *outcome* users see (e.g. openpyxl beats numbers-parser for an
"excel" query), not a proxy like "score changed" or "list is non-empty".
"""
from __future__ import annotations

from findmypylibrary.rank import BM25, expand_query, search, tokenize


def make_pkg(
    name: str,
    summary: str,
    downloads: int,
    last_release: str = "2026-01-01T00:00:00Z",
    keywords: str = "",
) -> dict:
    return {
        "name": name,
        "summary": summary,
        "keywords": keywords,
        "download_count": downloads,
        "last_release": last_release,
    }


def test_tokenize_lowercases_and_splits_on_non_alnum() -> None:
    assert tokenize("Read/Write Excel-2010 xlsx!") == ["read", "write", "excel", "2010", "xlsx"]


def test_tokenize_handles_none() -> None:
    assert tokenize(None) == []


def test_no_match_returns_empty_list() -> None:
    packages = [make_pkg("foo", "does something completely unrelated", 1000)]
    assert search("zzznonexistentterm", packages) == []


def test_relevance_gate_prefers_popularity_among_qualified_matches() -> None:
    """Regression test for the short-doc-bias bug: a short, keyword-dense
    summary must not outrank a hugely more popular, genuinely relevant package."""
    packages = [
        make_pkg(
            "openpyxl",
            "A Python library to read/write Excel 2010 xlsx/xlsm files",
            339_316_525,
        ),
        make_pkg("numbers-parser", "Read and write Apple Numbers spreadsheets", 924_605),
        make_pkg("tifffile", "Read and write TIFF files", 27_744_478),
    ]
    results = search("read and write excel spreadsheets", packages)
    names = [pkg["name"] for pkg, _ in results]
    assert names[0] == "openpyxl"


def test_low_relevance_candidate_excluded_even_if_far_more_popular() -> None:
    packages = [
        make_pkg("mega-lib", "a pdf viewer for something else entirely", 500_000_000),
        make_pkg(
            "docling-parse",
            "Simple package to extract text with coordinates from programmatic pdf files",
            3_581_578,
        ),
    ]
    results = search("parse messy pdf", packages)
    names = [pkg["name"] for pkg, _ in results]
    assert names[0] == "docling-parse"
    assert "mega-lib" not in names


def test_name_match_is_weighted_above_summary_only_match() -> None:
    packages = [
        make_pkg("scheduler", "does something else entirely", 10_000),
        make_pkg("otherlib", "a wrapper around scheduler for something else", 10_000),
    ]
    results = search("scheduler", packages)
    assert results[0][0]["name"] == "scheduler"


def test_synonym_expansion_matches_xlsx_for_excel_query() -> None:
    packages = [make_pkg("xlsxwriter", "Write xlsx files fast", 10_000_000)]
    results = search("excel", packages)
    assert len(results) == 1
    assert results[0][0]["name"] == "xlsxwriter"


def test_top_n_limits_results() -> None:
    packages = [make_pkg(f"pkg{i}", "parse pdf files", 1000 * (i + 1)) for i in range(20)]
    results = search("parse pdf", packages, top_n=5)
    assert len(results) == 5


def test_more_recent_release_breaks_a_near_tie() -> None:
    packages = [
        make_pkg(
            "old-lib", "parse pdf documents", 1_000_000, last_release="2015-01-01T00:00:00Z"
        ),
        make_pkg(
            "new-lib", "parse pdf documents", 1_000_000, last_release="2026-09-01T00:00:00Z"
        ),
    ]
    results = search("parse pdf documents", packages)
    assert results[0][0]["name"] == "new-lib"


def test_missing_last_release_scores_as_least_recent() -> None:
    known_date = "2020-01-01T00:00:00Z"
    packages = [
        make_pkg("known-date", "parse pdf documents", 1_000_000, last_release=known_date),
        make_pkg("unknown-date", "parse pdf documents", 1_000_000, last_release=None),
    ]
    results = search("parse pdf documents", packages)
    assert results[0][0]["name"] == "known-date"


def test_bm25_scores_empty_for_unseen_term() -> None:
    bm25 = BM25([["alpha", "beta"], ["gamma"]])
    assert bm25.score(["zzz"]) == {}


def test_expand_query_includes_synonyms_but_keeps_original_tokens() -> None:
    expanded = expand_query(["excel"])
    assert "excel" in expanded
    assert "xlsx" in expanded
