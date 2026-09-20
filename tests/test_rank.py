"""Ranking tests. Each asserts the ranking *outcome* a user would see (which
package comes first / is excluded), never a proxy like "score changed"."""

from __future__ import annotations

from helpers import make_pkg

from findmypylibrary import cache, rank


def names(query: str, packages: list[dict], top_n: int = 10) -> list[str]:
    cache.save_packages(packages)
    return [pkg["name"] for pkg, _ in rank.search(query, top_n=top_n)]


def test_tokenize_lowercases_and_splits_on_non_alnum() -> None:
    assert rank.tokenize("Read/Write Excel-2010 xlsx!") == [
        "read",
        "write",
        "excel",
        "2010",
        "xlsx",
    ]


def test_content_terms_drops_stopwords_and_duplicates() -> None:
    assert rank.content_terms("a python library to parse the PDF and parse html") == [
        "parse",
        "pdf",
        "html",
    ]


def test_content_terms_keeps_everything_when_query_is_only_stopwords() -> None:
    assert rank.content_terms("to be or not to be") != []


def test_empty_or_punctuation_only_query_returns_nothing() -> None:
    assert names("?!", [make_pkg("foo", "does something")]) == []


def test_candidates_that_all_fail_the_coverage_gate_give_an_empty_result() -> None:
    """Regression: ValueError (min of empty list) when no candidate survived the gate."""
    assert names("pdf qwzx vbnmq", [make_pkg("pdfthing", "parse pdf files")]) == []


def test_non_ascii_words_are_kept_whole_and_match_their_unaccented_form() -> None:
    """Regression: 'résumé' was split into 'r' + 'sum'."""
    assert rank.tokenize("Résumé parser") == ["résumé", "parser"]
    assert names("résumé parser", [make_pkg("resumelib", "A resume parser")]) == ["resumelib"]


def test_no_match_returns_empty_list() -> None:
    assert names("zzznonexistentterm", [make_pkg("foo", "does something unrelated")]) == []


def test_popular_relevant_package_beats_short_keyword_dense_niche_one() -> None:
    """Regression: BM25 short-document bias let numbers-parser outrank openpyxl."""
    result = names(
        "read and write excel spreadsheets",
        [
            make_pkg(
                "openpyxl", "A Python library to read/write Excel 2010 xlsx/xlsm files", 339_316_525
            ),
            make_pkg("numbers-parser", "Read and write Apple Numbers spreadsheets", 924_605),
            make_pkg("tifffile", "Read and write TIFF files", 27_744_478),
        ],
    )
    assert result[0] == "openpyxl"


def test_weak_match_is_excluded_even_if_far_more_popular() -> None:
    result = names(
        "parse messy pdf",
        [
            make_pkg("mega-lib", "a pdf viewer for something else entirely", 500_000_000),
            make_pkg(
                "docling-parse", "Parse text with coordinates from messy pdf files", 3_581_578
            ),
        ],
    )
    assert result == ["docling-parse"]


def test_stemming_finds_famous_packages_whose_summary_uses_another_word_form() -> None:
    """Regression: 'resize images' missed Pillow ('Imaging'), 'plot' missed 'plotting'."""
    packages = [
        make_pkg("pillow", "Python Imaging Library (fork)", 535_067_156),
        make_pkg("matplotlib", "Python plotting package", 227_869_222),
        make_pkg("requests", "Python HTTP for Humans.", 1_695_910_251),
    ]
    assert names("resize images", packages)[0] == "pillow"
    assert names("plot charts", packages)[0] == "matplotlib"


def test_description_text_finds_package_when_no_core_field_has_the_term() -> None:
    """Regression: 'dataframes' could not find pandas because its summary never says it."""
    result = names(
        "dataframes",
        [
            make_pkg(
                "pandas",
                "Powerful data structures for data analysis, time series, and statistics",
                739_713_944,
                description="Size mutability: columns can be inserted into a DataFrame.",
            ),
            make_pkg("requests", "Python HTTP for Humans.", 1_695_910_251),
        ],
    )
    assert result == ["pandas"]


def test_readme_noise_does_not_beat_a_real_match() -> None:
    """Regression: boto3's README mentions 'unit tests', which tied it with testing tools."""
    unrelated = [
        make_pkg(f"filler{i}", f"library number {i} for something else") for i in range(30)
    ]
    result = names(
        "unit testing",
        [
            make_pkg(
                "boto3",
                "The AWS SDK for Python",
                3_206_668_324,
                description="Boto3 is the AWS SDK. Running tests: run the unit tests with tox.",
            ),
            make_pkg(
                "testtools", "Extensions to the standard library unit testing framework", 900_000
            ),
            *unrelated,
        ],
    )
    assert result[0] == "testtools"
    assert "boto3" not in result


def test_synonym_connects_postgres_to_postgresql() -> None:
    result = names(
        "connect to postgres database",
        [
            make_pkg("psycopg2", "psycopg2 - Python-PostgreSQL Database Adapter", 63_666_344),
            make_pkg("pymssql", "DB-API interface to Microsoft SQL Server database", 9_000_000),
        ],
    )
    assert result[0] == "psycopg2"


def test_partial_match_by_a_dominant_package_survives_the_gate() -> None:
    """Regression: 'unit testing' dropped pytest because a niche package matched both words."""
    result = names(
        "unit testing",
        [
            make_pkg("pytest", "pytest: simple powerful testing with Python", 1_038_243_019),
            make_pkg("kgb", "Utilities for spying on function calls in unit tests.", 150_000),
        ],
    )
    assert result[0] == "pytest"


def test_name_match_outranks_description_only_match() -> None:
    result = names(
        "scheduler",
        [
            make_pkg("scheduler", "does something else entirely", 10_000),
            make_pkg("otherlib", "unrelated", 10_000, description="wraps a scheduler internally"),
        ],
    )
    assert result[0] == "scheduler"


def test_top_n_limits_results() -> None:
    packages = [make_pkg(f"pkg{i}", "parse pdf files", 1000 * (i + 1)) for i in range(20)]
    assert len(names("parse pdf", packages, top_n=5)) == 5


def test_more_recent_release_breaks_a_tie() -> None:
    result = names(
        "parse pdf documents",
        [
            make_pkg(
                "old-lib", "parse pdf documents", 1_000_000, last_release="2015-01-01T00:00:00Z"
            ),
            make_pkg(
                "new-lib", "parse pdf documents", 1_000_000, last_release="2026-09-01T00:00:00Z"
            ),
        ],
    )
    assert result[0] == "new-lib"


def test_missing_or_malformed_release_date_counts_as_least_recent() -> None:
    result = names(
        "parse pdf documents",
        [
            make_pkg("no-date", "parse pdf documents", 1_000_000, last_release=None),
            make_pkg("bad-date", "parse pdf documents", 1_000_000, last_release="not-a-date"),
            make_pkg(
                "dated", "parse pdf documents", 1_000_000, last_release="2020-01-01T00:00:00Z"
            ),
        ],
    )
    assert result[0] == "dated"


def test_scores_are_between_zero_and_one_and_sorted() -> None:
    cache.save_packages([make_pkg(f"pkg{i}", "parse pdf files", 10 ** (i + 2)) for i in range(5)])
    scores = [score for _, score in rank.search("parse pdf")]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)
