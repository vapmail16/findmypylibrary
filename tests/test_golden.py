"""Ranking-quality regression suite: the golden queries, run offline against a
committed slice of the real PyPI corpus (tests/fixtures/golden_corpus.json.gz,
rebuilt with scripts/build_golden_fixture.py)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from findmypylibrary import cache, golden, rank

FIXTURE = Path(__file__).parent / "fixtures" / "golden_corpus.json.gz"


@pytest.fixture()
def real_corpus() -> None:
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as f:
        cache.save_packages(json.load(f))


def top5(query: str) -> list[str]:
    return [golden.normalize(pkg["name"]) for pkg, _ in rank.search(query, top_n=5)]


def test_normalize_follows_pypi_name_rules() -> None:
    assert golden.normalize("Ruamel.YAML_clib") == "ruamel-yaml-clib"


def test_golden_queries_file_is_well_formed() -> None:
    queries = golden.load_queries()
    assert len(queries) >= 30
    assert all(entry["query"] and entry["expect_any"] for entry in queries)
    assert len({entry["query"] for entry in queries}) == len(queries)


def test_golden_pass_rate_meets_the_bar_on_the_real_corpus(real_corpus: None) -> None:
    report = golden.evaluate()
    assert report["pass_rate"] >= golden.MIN_PASS_RATE, report["failures"]
    assert report["passed"] + len(report["failures"]) == report["total"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("resize images", "pillow"),
        ("plot charts", "matplotlib"),
        ("dataframes", "polars"),
        ("machine learning", "scikit-learn"),
        ("make http requests", "requests"),
        ("read and write excel spreadsheets", "openpyxl"),
    ],
)
def test_famous_package_is_in_the_top_five(real_corpus: None, query: str, expected: str) -> None:
    """Regressions from the 2026-09 review: each of these was missing entirely."""
    assert expected in top5(query)


def test_postgres_query_returns_postgres_drivers_not_mssql(real_corpus: None) -> None:
    results = top5("connect to postgres database")
    assert {"psycopg2", "psycopg2-binary", "psycopg", "asyncpg"} & set(results)
    assert "pymssql" not in results


def test_readme_noise_stays_out_of_unit_testing_results(real_corpus: None) -> None:
    """boto3 matches 'unit testing' only through its README ("run the unit tests").
    (tqdm is deliberately not asserted: its own classifiers declare a Testing topic.)"""
    assert "boto3" not in top5("unit testing")
