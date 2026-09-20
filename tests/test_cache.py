"""Tests for the sqlite snapshot cache."""
from __future__ import annotations

from pathlib import Path

import pytest

from findmypylibrary import cache


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def sample_result(name: str = "boto3") -> dict:
    return {
        "name": name,
        "summary": "The AWS SDK for Python",
        "keywords": "aws,sdk",
        "homepage": "https://github.com/boto/boto3",
        "version": "1.0.0",
        "last_release": "2026-01-01T00:00:00Z",
        "download_count": 1000,
        "rank": 1,
    }


def test_cache_dir_creates_directory(tmp_path: Path) -> None:
    d = cache.cache_dir()
    assert d.is_dir()
    assert d.name == "findmypylibrary"


def test_db_path_is_inside_cache_dir() -> None:
    assert cache.db_path().parent == cache.cache_dir()


def test_exists_false_before_any_save() -> None:
    assert cache.exists() is False


def test_save_and_load_round_trip() -> None:
    cache.save_packages([sample_result("boto3"), sample_result("requests")])
    assert cache.exists() is True

    loaded = cache.load_all()
    names = sorted(p["name"] for p in loaded)
    assert names == ["boto3", "requests"]


def test_save_replaces_previous_snapshot() -> None:
    cache.save_packages([sample_result("boto3")])
    cache.save_packages([sample_result("requests")])

    loaded = cache.load_all()
    assert [p["name"] for p in loaded] == ["requests"]


def test_meta_records_refresh_timestamp_and_count() -> None:
    cache.save_packages([sample_result("boto3"), sample_result("requests")])
    assert cache.get_meta("count") == "2"
    assert cache.get_meta("refreshed_at") is not None


def test_get_meta_missing_key_returns_none() -> None:
    assert cache.get_meta("does-not-exist") is None
