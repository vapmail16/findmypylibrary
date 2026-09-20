"""Tests for the refresh pipeline, with all HTTP calls mocked via respx.

No test in this file makes a real network call — PyPI and GitHub should
never be hit during the test suite.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from findmypylibrary import cache, fetch


def pypi_json(name: str, summary: str = "A package.") -> dict:
    return {
        "info": {
            "name": name,
            "summary": summary,
            "keywords": "",
            "version": "1.0.0",
            "project_urls": {"Homepage": f"https://example.com/{name}"},
        },
        "urls": [{"upload_time_iso_8601": "2026-01-01T00:00:00.000000Z"}],
    }


@respx.mock
def test_get_top_packages_returns_rows_up_to_limit() -> None:
    respx.get(fetch.TOP_PACKAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={"rows": [{"project": f"pkg{i}", "download_count": 100 - i} for i in range(10)]},
        )
    )
    rows = fetch.get_top_packages(limit=3)
    assert len(rows) == 3
    assert rows[0]["project"] == "pkg0"


@respx.mock
def test_get_top_packages_limit_larger_than_dataset() -> None:
    respx.get(fetch.TOP_PACKAGES_URL).mock(
        return_value=httpx.Response(200, json={"rows": [{"project": "solo", "download_count": 1}]})
    )
    rows = fetch.get_top_packages(limit=100)
    assert len(rows) == 1


@respx.mock
def test_run_refresh_sync_success_populates_all_fields() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="boto3")).mock(
        return_value=httpx.Response(200, json=pypi_json("boto3", "AWS SDK"))
    )
    rows = [{"project": "boto3", "download_count": 5000}]

    results = fetch.run_refresh_sync(rows)

    assert len(results) == 1
    pkg = results[0]
    assert pkg["name"] == "boto3"
    assert pkg["summary"] == "AWS SDK"
    assert pkg["homepage"] == "https://example.com/boto3"
    assert pkg["download_count"] == 5000
    assert pkg["rank"] == 1
    assert pkg["last_release"] == "2026-01-01T00:00:00.000000Z"


@respx.mock
def test_run_refresh_sync_skips_404_packages() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="ghost")).mock(return_value=httpx.Response(404))
    rows = [{"project": "ghost", "download_count": 1}]

    results = fetch.run_refresh_sync(rows)

    assert results == []


@respx.mock
def test_run_refresh_sync_retries_on_429_then_succeeds() -> None:
    route = respx.get(fetch.PYPI_JSON_URL.format(name="flaky"))
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(200, json=pypi_json("flaky")),
    ]
    rows = [{"project": "flaky", "download_count": 1}]

    results = fetch.run_refresh_sync(rows)

    assert len(results) == 1
    assert results[0]["name"] == "flaky"


@respx.mock
def test_run_refresh_sync_gives_up_after_persistent_server_errors() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="broken")).mock(return_value=httpx.Response(500))
    rows = [{"project": "broken", "download_count": 1}]

    results = fetch.run_refresh_sync(rows)

    assert results == []


@respx.mock
def test_run_refresh_sync_reports_progress_for_every_row() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="a")).mock(
        return_value=httpx.Response(200, json=pypi_json("a"))
    )
    respx.get(fetch.PYPI_JSON_URL.format(name="b")).mock(return_value=httpx.Response(404))
    rows = [{"project": "a", "download_count": 1}, {"project": "b", "download_count": 1}]

    progress_calls = []
    fetch.run_refresh_sync(
        rows, on_progress=lambda done, total: progress_calls.append((done, total))
    )

    assert len(progress_calls) == 2
    assert all(total == 2 for _, total in progress_calls)


@respx.mock
def test_run_refresh_sync_falls_back_to_home_page_when_no_project_urls() -> None:
    data = pypi_json("nopu")
    data["info"]["project_urls"] = None
    data["info"]["home_page"] = "https://legacy.example.com"
    respx.get(fetch.PYPI_JSON_URL.format(name="nopu")).mock(
        return_value=httpx.Response(200, json=data)
    )
    rows = [{"project": "nopu", "download_count": 1}]

    results = fetch.run_refresh_sync(rows)

    assert results[0]["homepage"] == "https://legacy.example.com"


@pytest.mark.parametrize("field", ["summary", "keywords"])
@respx.mock
def test_run_refresh_sync_defaults_missing_text_fields_to_empty_string(field: str) -> None:
    data = pypi_json("sparse")
    data["info"][field] = None
    respx.get(fetch.PYPI_JSON_URL.format(name="sparse")).mock(
        return_value=httpx.Response(200, json=data)
    )
    rows = [{"project": "sparse", "download_count": 1}]

    results = fetch.run_refresh_sync(rows)

    assert results[0][field] == ""


def _build_valid_snapshot_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> bytes:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "source-cache"))
    cache.save_packages(
        [
            {
                "name": "boto3",
                "summary": "AWS SDK",
                "keywords": "",
                "homepage": "",
                "version": "1.0",
                "last_release": "2026-01-01T00:00:00Z",
                "download_count": 100,
                "rank": 1,
            }
        ]
    )
    return cache.db_path().read_bytes()


@respx.mock
def test_download_prebuilt_snapshot_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_bytes = _build_valid_snapshot_bytes(tmp_path, monkeypatch)
    respx.get(
        fetch.GITHUB_RELEASE_SNAPSHOT_URL.format(repo=fetch.DEFAULT_SNAPSHOT_REPO)
    ).mock(return_value=httpx.Response(200, content=snapshot_bytes))

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "dest"))
    dest = cache.db_path()

    assert fetch.download_prebuilt_snapshot(dest) is True
    assert dest.exists()
    assert cache.load_all()[0]["name"] == "boto3"


@respx.mock
def test_download_prebuilt_snapshot_returns_false_when_no_release_yet(tmp_path: Path) -> None:
    respx.get(
        fetch.GITHUB_RELEASE_SNAPSHOT_URL.format(repo=fetch.DEFAULT_SNAPSHOT_REPO)
    ).mock(return_value=httpx.Response(404))

    dest = tmp_path / "snapshot.sqlite"

    assert fetch.download_prebuilt_snapshot(dest) is False
    assert not dest.exists()


@respx.mock
def test_download_prebuilt_snapshot_rejects_corrupt_download(tmp_path: Path) -> None:
    respx.get(
        fetch.GITHUB_RELEASE_SNAPSHOT_URL.format(repo=fetch.DEFAULT_SNAPSHOT_REPO)
    ).mock(return_value=httpx.Response(200, content=b"not a real sqlite file"))

    dest = tmp_path / "snapshot.sqlite"

    assert fetch.download_prebuilt_snapshot(dest) is False
    assert not dest.exists()
    assert not dest.with_suffix(".download").exists()


@respx.mock
def test_download_prebuilt_snapshot_handles_network_error(tmp_path: Path) -> None:
    respx.get(
        fetch.GITHUB_RELEASE_SNAPSHOT_URL.format(repo=fetch.DEFAULT_SNAPSHOT_REPO)
    ).mock(side_effect=httpx.ConnectError("boom"))

    dest = tmp_path / "snapshot.sqlite"

    assert fetch.download_prebuilt_snapshot(dest) is False
    assert not dest.exists()
