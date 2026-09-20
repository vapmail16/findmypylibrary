"""Tests for the sqlite snapshot cache (schema v2: FTS5 index + schema version)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from helpers import bulky_packages, damage_middle_pages, make_pkg

from findmypylibrary import cache


def test_cache_dir_creates_directory() -> None:
    d = cache.cache_dir()
    assert d.is_dir()
    assert d.name == "findmypylibrary"


def test_exists_false_before_any_save() -> None:
    assert cache.exists() is False


def test_save_then_info_reports_count_version_and_age() -> None:
    cache.save_packages([make_pkg("boto3", "AWS SDK"), make_pkg("requests", "HTTP for Humans")])

    info = cache.snapshot_info()

    assert cache.exists() is True
    assert info["count"] == 2
    assert info["schema_version"] == cache.SCHEMA_VERSION
    assert info["age_days"] == 0
    assert info["refreshed_at"]


def test_save_replaces_previous_snapshot_and_its_index() -> None:
    cache.save_packages([make_pkg("boto3", "AWS SDK")])
    cache.save_packages([make_pkg("requests", "HTTP for Humans")])

    assert cache.snapshot_info()["count"] == 1
    assert cache.search_candidates('"aws"') == []
    assert [c["name"] for c in cache.search_candidates('"http"')] == ["requests"]


def test_search_candidates_stems_query_and_document_terms() -> None:
    cache.save_packages([make_pkg("pillow", "Python Imaging Library (fork)")])

    names = [c["name"] for c in cache.search_candidates('"images"')]

    assert names == ["pillow"]


def test_search_candidates_indexes_description_and_topics() -> None:
    cache.save_packages(
        [
            make_pkg(
                "pandas",
                "Powerful data structures for data analysis",
                topics="Scientific/Engineering",
                description="The DataFrame object is the primary pandas data structure.",
            )
        ]
    )

    assert [c["name"] for c in cache.search_candidates('"dataframes"')] == ["pandas"]
    assert [c["name"] for c in cache.search_candidates('"engineering"')] == ["pandas"]


def test_search_candidates_weights_name_above_description() -> None:
    cache.save_packages(
        [
            make_pkg("scheduler", "does something else entirely"),
            make_pkg("otherlib", "unrelated", description="a wrapper around scheduler things"),
        ]
    )

    candidates = cache.search_candidates('"scheduler"')

    assert [c["name"] for c in candidates] == ["scheduler", "otherlib"]
    assert candidates[0]["relevance"] > candidates[1]["relevance"] > 0


def test_search_candidates_can_be_restricted_to_columns() -> None:
    cache.save_packages(
        [
            make_pkg("corelib", "unit testing helpers"),
            make_pkg("noisy", "an aws sdk", description="run the unit tests with tox"),
        ]
    )

    core = cache.search_candidates('"unit"', columns=cache.CORE_COLUMNS)
    desc = cache.search_candidates('"unit"', columns=("description",))

    assert [c["name"] for c in core] == ["corelib"]
    assert [c["name"] for c in desc] == ["noisy"]


def test_search_candidates_returns_package_fields() -> None:
    cache.save_packages([make_pkg("boto3", "AWS SDK", downloads=5000)])

    candidate = cache.search_candidates('"aws"')[0]

    assert candidate["download_count"] == 5000
    assert candidate["summary"] == "AWS SDK"
    assert candidate["last_release"] == "2026-01-01T00:00:00Z"


def test_matching_rowids_returns_ids_usable_for_coverage() -> None:
    cache.save_packages([make_pkg("a", "parse pdf"), make_pkg("b", "parse html")])

    parse_ids = cache.matching_ids('"parse"')
    pdf_ids = cache.matching_ids('"pdf"')

    assert len(parse_ids) == 2
    assert len(pdf_ids) == 1
    assert pdf_ids <= parse_ids


def test_get_meta_missing_key_returns_none() -> None:
    cache.save_packages([make_pkg("boto3")])
    assert cache.get_meta("does-not-exist") is None


def test_reading_missing_snapshot_raises_snapshot_error() -> None:
    with pytest.raises(cache.SnapshotError, match="refresh"):
        cache.snapshot_info()


def test_reading_corrupt_snapshot_raises_snapshot_error() -> None:
    cache.db_path().write_bytes(b"garbage, not sqlite")

    with pytest.raises(cache.SnapshotError, match="refresh"):
        cache.search_candidates('"anything"')


def test_reading_snapshot_with_old_schema_raises_snapshot_error() -> None:
    conn = sqlite3.connect(cache.db_path())
    conn.executescript(
        "CREATE TABLE packages (name TEXT PRIMARY KEY, summary TEXT);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
    )
    conn.close()

    with pytest.raises(cache.SnapshotError, match="refresh"):
        cache.snapshot_info()


def test_save_recovers_from_a_corrupt_existing_file() -> None:
    cache.db_path().write_bytes(b"garbage, not sqlite")

    cache.save_packages([make_pkg("boto3", "AWS SDK")])

    assert cache.snapshot_info()["count"] == 1


def test_validate_snapshot_accepts_a_saved_snapshot_at_another_path(tmp_path: Path) -> None:
    cache.save_packages([make_pkg("boto3", "AWS SDK")])
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes(cache.db_path().read_bytes())

    cache.validate_snapshot(copy)


def test_validate_snapshot_rejects_empty_snapshot(tmp_path: Path) -> None:
    cache.save_packages([])
    with pytest.raises(cache.SnapshotError, match="empty"):
        cache.validate_snapshot(cache.db_path())


def test_snapshot_without_a_build_date_raises_snapshot_error() -> None:
    cache.save_packages([make_pkg("boto3", "AWS SDK")])
    conn = sqlite3.connect(cache.db_path())
    conn.execute("DELETE FROM meta WHERE key = 'refreshed_at'")
    conn.commit()
    conn.close()

    with pytest.raises(cache.SnapshotError, match="refresh"):
        cache.snapshot_info()


def test_damage_beyond_the_header_raises_snapshot_error_on_query_and_on_validation() -> None:
    cache.save_packages(bulky_packages())
    cache.db_path().write_bytes(damage_middle_pages(cache.db_path().read_bytes()))

    with pytest.raises(cache.SnapshotError, match="damaged"):
        cache.search_candidates('"pdf"')
    with pytest.raises(cache.SnapshotError, match="damaged"):
        cache.validate_snapshot(cache.db_path())


def test_save_replaces_a_snapshot_that_is_damaged_beyond_the_header() -> None:
    cache.save_packages(bulky_packages())
    cache.db_path().write_bytes(damage_middle_pages(cache.db_path().read_bytes()))

    cache.save_packages([make_pkg("boto3", "AWS SDK")])

    assert [c["name"] for c in cache.search_candidates('"aws"')] == ["boto3"]
