"""Tests for the refresh pipeline, with all HTTP calls mocked via respx.

No test in this file makes a real network call — PyPI and GitHub are never hit.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import httpx
import pytest
import respx
from helpers import bulky_packages, damage_middle_pages, make_pkg

from findmypylibrary import cache, fetch


def pypi_json(name: str, summary: str = "A package.") -> dict:
    return {
        "info": {
            "name": name,
            "summary": summary,
            "keywords": "",
            "version": "1.0.0",
            "project_urls": {"Homepage": f"https://example.com/{name}"},
            "classifiers": [
                "Programming Language :: Python :: 3",
                "Topic :: Multimedia :: Graphics",
                "Framework :: Django",
            ],
            "description": "Resize and crop images. See https://example.com/docs for more.",
        },
        "urls": [{"upload_time_iso_8601": "2026-01-01T00:00:00.000000Z"}],
    }


# --- text extraction -------------------------------------------------------


def test_clean_description_strips_urls_html_badges_and_directives() -> None:
    raw = (
        "[![Build](https://img.shields.io/badge.svg)](https://ci.example.com)\n"
        ".. image:: https://example.com/logo.png\n"
        "<p align='center'>Fast <b>DataFrame</b> library</p>\n"
        "See [the docs](https://docs.example.com/guide) for details."
    )

    cleaned = fetch.clean_description(raw)

    assert "DataFrame library" in cleaned
    assert "the docs" in cleaned
    for noise in ("http", "shields", "<p", "image::", "badge"):
        assert noise not in cleaned


def test_clean_description_truncates_and_handles_none() -> None:
    assert fetch.clean_description(None) == ""
    assert len(fetch.clean_description("word " * 5000)) <= fetch.DESCRIPTION_MAX_CHARS


def test_topic_classifiers_keeps_only_topic_and_framework_values() -> None:
    topics = fetch.topic_classifiers(
        [
            "Programming Language :: Python :: 3",
            "Topic :: Scientific/Engineering :: Image Processing",
            "Framework :: Django",
            "License :: OSI Approved :: MIT License",
        ]
    )

    assert topics == "Scientific/Engineering :: Image Processing; Django"
    assert fetch.topic_classifiers(None) == ""


# --- top package list ------------------------------------------------------


@respx.mock
def test_get_top_packages_returns_rows_up_to_limit() -> None:
    respx.get(fetch.TOP_PACKAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={"rows": [{"project": f"pkg{i}", "download_count": 100 - i} for i in range(10)]},
        )
    )
    rows = fetch.get_top_packages(limit=3)
    assert [r["project"] for r in rows] == ["pkg0", "pkg1", "pkg2"]


@respx.mock
def test_get_top_packages_limit_larger_than_dataset() -> None:
    respx.get(fetch.TOP_PACKAGES_URL).mock(
        return_value=httpx.Response(200, json={"rows": [{"project": "solo", "download_count": 1}]})
    )
    assert len(fetch.get_top_packages(limit=100)) == 1


@respx.mock
def test_get_top_packages_network_failure_raises_fetch_error() -> None:
    respx.get(fetch.TOP_PACKAGES_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(fetch.FetchError, match="package list"):
        fetch.get_top_packages()


@respx.mock
def test_get_top_packages_http_error_raises_fetch_error() -> None:
    respx.get(fetch.TOP_PACKAGES_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(fetch.FetchError, match="package list"):
        fetch.get_top_packages()


# --- per-package crawl -----------------------------------------------------


@respx.mock
def test_run_refresh_sync_success_populates_all_fields() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="boto3")).mock(
        return_value=httpx.Response(200, json=pypi_json("boto3", "AWS SDK"))
    )

    results = fetch.run_refresh_sync([{"project": "boto3", "download_count": 5000}])

    assert results == [
        {
            "name": "boto3",
            "summary": "AWS SDK",
            "keywords": "",
            "topics": "Multimedia :: Graphics; Django",
            "description": "Resize and crop images. See for more.",
            "homepage": "https://example.com/boto3",
            "version": "1.0.0",
            "last_release": "2026-01-01T00:00:00.000000Z",
            "download_count": 5000,
            "rank": 1,
        }
    ]


@respx.mock
def test_run_refresh_sync_output_is_accepted_by_the_cache() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="boto3")).mock(
        return_value=httpx.Response(200, json=pypi_json("boto3", "AWS SDK"))
    )

    cache.save_packages(fetch.run_refresh_sync([{"project": "boto3", "download_count": 1}]))

    assert [c["name"] for c in cache.search_candidates('"resize"')] == ["boto3"]


@respx.mock
def test_run_refresh_sync_skips_404_packages() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="ghost")).mock(return_value=httpx.Response(404))
    assert fetch.run_refresh_sync([{"project": "ghost", "download_count": 1}]) == []


@respx.mock
def test_run_refresh_sync_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "RETRY_BASE_DELAY", 0.0)
    route = respx.get(fetch.PYPI_JSON_URL.format(name="flaky"))
    route.side_effect = [httpx.Response(429), httpx.Response(200, json=pypi_json("flaky"))]

    results = fetch.run_refresh_sync([{"project": "flaky", "download_count": 1}])

    assert [r["name"] for r in results] == ["flaky"]


@respx.mock
def test_run_refresh_sync_retries_after_a_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "RETRY_BASE_DELAY", 0.0)
    route = respx.get(fetch.PYPI_JSON_URL.format(name="flaky"))
    route.side_effect = [httpx.ConnectError("boom"), httpx.Response(200, json=pypi_json("flaky"))]

    results = fetch.run_refresh_sync([{"project": "flaky", "download_count": 1}])

    assert [r["name"] for r in results] == ["flaky"]


@respx.mock
def test_run_refresh_sync_gives_up_after_persistent_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch, "RETRY_BASE_DELAY", 0.0)
    respx.get(fetch.PYPI_JSON_URL.format(name="broken")).mock(return_value=httpx.Response(500))
    assert fetch.run_refresh_sync([{"project": "broken", "download_count": 1}]) == []


@respx.mock
def test_run_refresh_sync_reports_progress_for_every_row() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="a")).mock(
        return_value=httpx.Response(200, json=pypi_json("a"))
    )
    respx.get(fetch.PYPI_JSON_URL.format(name="b")).mock(return_value=httpx.Response(404))
    rows = [{"project": "a", "download_count": 1}, {"project": "b", "download_count": 1}]

    progress: list[tuple[int, int]] = []
    fetch.run_refresh_sync(rows, on_progress=lambda done, total: progress.append((done, total)))

    assert sorted(progress) == [(1, 2), (2, 2)]


@respx.mock
def test_run_refresh_sync_falls_back_to_home_page_when_no_project_urls() -> None:
    data = pypi_json("nopu")
    data["info"]["project_urls"] = None
    data["info"]["home_page"] = "https://legacy.example.com"
    respx.get(fetch.PYPI_JSON_URL.format(name="nopu")).mock(
        return_value=httpx.Response(200, json=data)
    )

    results = fetch.run_refresh_sync([{"project": "nopu", "download_count": 1}])

    assert results[0]["homepage"] == "https://legacy.example.com"


@pytest.mark.parametrize("field", ["summary", "keywords"])
@respx.mock
def test_run_refresh_sync_defaults_missing_text_fields_to_empty_string(field: str) -> None:
    data = pypi_json("sparse")
    data["info"][field] = None
    respx.get(fetch.PYPI_JSON_URL.format(name="sparse")).mock(
        return_value=httpx.Response(200, json=data)
    )

    results = fetch.run_refresh_sync([{"project": "sparse", "download_count": 1}])

    assert results[0][field] == ""


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, text="<html>forbidden</html>"),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
@respx.mock
def test_one_unexpected_response_skips_that_package_not_the_whole_crawl(
    response: httpx.Response,
) -> None:
    """Regression: a single 403 / HTML body raised JSONDecodeError and killed a 15k crawl."""
    respx.get(fetch.PYPI_JSON_URL.format(name="bad")).mock(return_value=response)
    respx.get(fetch.PYPI_JSON_URL.format(name="good")).mock(
        return_value=httpx.Response(200, json=pypi_json("good"))
    )
    rows = [{"project": "bad", "download_count": 1}, {"project": "good", "download_count": 1}]

    assert [r["name"] for r in fetch.run_refresh_sync(rows)] == ["good"]


@respx.mock
def test_crawl_follows_redirects() -> None:
    respx.get(fetch.PYPI_JSON_URL.format(name="Old_Name")).mock(
        return_value=httpx.Response(
            301, headers={"Location": fetch.PYPI_JSON_URL.format(name="old-name")}
        )
    )
    respx.get(fetch.PYPI_JSON_URL.format(name="old-name")).mock(
        return_value=httpx.Response(200, json=pypi_json("old-name"))
    )

    results = fetch.run_refresh_sync([{"project": "Old_Name", "download_count": 1}])

    assert [r["name"] for r in results] == ["old-name"]


@respx.mock
def test_list_valued_keywords_are_stored_as_text() -> None:
    data = pypi_json("kw")
    data["info"]["keywords"] = ["alpha", "beta"]
    respx.get(fetch.PYPI_JSON_URL.format(name="kw")).mock(
        return_value=httpx.Response(200, json=data)
    )

    results = fetch.run_refresh_sync([{"project": "kw", "download_count": 1}])
    cache.save_packages(results)

    assert results[0]["keywords"] == "alpha, beta"


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="<html>"), httpx.Response(200, json={"unexpected": "shape"})],
)
@respx.mock
def test_get_top_packages_unexpected_body_raises_fetch_error(response: httpx.Response) -> None:
    respx.get(fetch.TOP_PACKAGES_URL).mock(return_value=response)
    with pytest.raises(fetch.FetchError, match="package list"):
        fetch.get_top_packages()


# --- prebuilt snapshot download --------------------------------------------


def _gzipped_valid_snapshot() -> bytes:
    cache.save_packages([make_pkg("boto3", "AWS SDK")])
    data = gzip.compress(cache.db_path().read_bytes())
    cache.db_path().unlink()
    return data


def test_snapshot_url_is_a_fixed_tag_carrying_the_schema_version() -> None:
    url = fetch.snapshot_url()
    assert "/releases/download/snapshot-latest/" in url
    assert url.endswith(f"snapshot-v{cache.SCHEMA_VERSION}.sqlite.gz")
    assert "/releases/latest/" not in url


@respx.mock
def test_download_prebuilt_snapshot_success() -> None:
    respx.get(fetch.snapshot_url()).mock(
        return_value=httpx.Response(200, content=_gzipped_valid_snapshot())
    )

    fetch.download_prebuilt_snapshot(cache.db_path())

    assert cache.snapshot_info()["count"] == 1
    assert list(cache.cache_dir().glob("*.download*")) == []


@respx.mock
def test_download_prebuilt_snapshot_missing_release_raises() -> None:
    respx.get(fetch.snapshot_url()).mock(return_value=httpx.Response(404))

    with pytest.raises(fetch.FetchError, match="--build-locally"):
        fetch.download_prebuilt_snapshot(cache.db_path())

    assert not cache.exists()


@respx.mock
def test_download_prebuilt_snapshot_network_error_raises() -> None:
    respx.get(fetch.snapshot_url()).mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(fetch.FetchError, match="--build-locally"):
        fetch.download_prebuilt_snapshot(cache.db_path())


def _gzip_with_corrupt_deflate_stream() -> bytes:
    data = bytearray(gzip.compress(b"x" * 50_000))
    data[20:40] = bytes(20)
    return bytes(data)


def _gzipped_snapshot_with_damaged_pages() -> bytes:
    cache.save_packages(bulky_packages())
    damaged = damage_middle_pages(cache.db_path().read_bytes())
    cache.db_path().unlink()
    return gzip.compress(damaged)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"not gzip at all", id="not-gzip"),
        pytest.param(gzip.compress(b"not a sqlite file"), id="not-sqlite"),
        pytest.param(_gzip_with_corrupt_deflate_stream(), id="corrupt-deflate-stream"),
        pytest.param("damaged-pages", id="sqlite-damaged-beyond-header"),
    ],
)
@respx.mock
def test_download_prebuilt_snapshot_rejects_corrupt_download_and_keeps_old_cache(
    payload: bytes | str,
) -> None:
    if payload == "damaged-pages":
        payload = _gzipped_snapshot_with_damaged_pages()
    cache.save_packages([make_pkg("existing", "the good old snapshot")])
    respx.get(fetch.snapshot_url()).mock(return_value=httpx.Response(200, content=payload))

    with pytest.raises(fetch.FetchError, match="invalid"):
        fetch.download_prebuilt_snapshot(cache.db_path())

    assert [c["name"] for c in cache.search_candidates('"good"')] == ["existing"]
    assert list(cache.cache_dir().glob("*.download*")) == []


@respx.mock
def test_download_that_cannot_be_written_to_disk_raises_fetch_error(tmp_path: Path) -> None:
    respx.get(fetch.snapshot_url()).mock(
        return_value=httpx.Response(200, content=_gzipped_valid_snapshot())
    )
    unwritable = tmp_path / "missing-directory" / "snapshot.sqlite"

    with pytest.raises(fetch.FetchError, match="Could not save"):
        fetch.download_prebuilt_snapshot(unwritable)
