"""CLI tests: command wiring, error paths and safety guards.

fetch.* is monkeypatched here (its network behaviour is covered in
test_fetch.py); this file proves the CLI reacts correctly to each outcome.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from helpers import bulky_packages, damage_middle_pages, make_pkg

from findmypylibrary import cache, cli, fetch, golden

OPENPYXL = make_pkg(
    "openpyxl", "A Python library to read/write Excel 2010 xlsx/xlsm files", 339_316_525
)


def run(*args: str) -> tuple[int, str]:
    """Invoke the CLI. CliRunner hides uncaught exceptions from `output`, so an
    assertion on the text alone can never catch a crash: fail here instead."""
    result = CliRunner().invoke(cli.main, list(args))
    crashed = result.exception is not None and not isinstance(result.exception, SystemExit)
    assert not crashed, f"CLI crashed with {result.exception!r}"
    return result.exit_code, result.output


def fake_crawl(monkeypatch: pytest.MonkeyPatch, listed: int, fetched: int) -> None:
    monkeypatch.setattr(
        fetch,
        "get_top_packages",
        lambda limit: [{"project": f"pkg{i}", "download_count": 1} for i in range(listed)],
    )
    monkeypatch.setattr(
        fetch,
        "run_refresh_sync",
        lambda rows, on_progress=None: [make_pkg(f"pkg{i}", "parse pdf") for i in range(fetched)],
    )


def age_snapshot(days: int) -> None:
    conn = sqlite3.connect(cache.db_path())
    conn.execute(
        "UPDATE meta SET value = datetime('now', ?) || '+00:00' WHERE key = 'refreshed_at'",
        (f"-{days} days",),
    )
    conn.commit()
    conn.close()


# --- search ----------------------------------------------------------------


def test_help_lists_all_commands() -> None:
    code, output = run("--help")
    assert code == 0
    for command in ("refresh", "search", "status", "golden"):
        assert command in output


def test_search_without_snapshot_gives_actionable_error_not_traceback() -> None:
    code, output = run("search", "excel")
    assert code != 0
    assert "findmypylibrary refresh" in output
    assert "Traceback" not in output


def test_search_with_corrupt_snapshot_gives_actionable_error_not_traceback() -> None:
    cache.db_path().write_bytes(b"garbage, not sqlite")
    code, output = run("search", "excel")
    assert code != 0
    assert "findmypylibrary refresh" in output
    assert "Traceback" not in output


def test_search_returns_ranked_results() -> None:
    cache.save_packages([OPENPYXL])
    code, output = run("search", "excel", "spreadsheets")
    assert code == 0
    assert "openpyxl" in output
    assert "https://pypi.org/project/openpyxl/" in output


def test_bare_query_falls_back_to_search() -> None:
    cache.save_packages([OPENPYXL])
    code, output = run("excel", "spreadsheets")
    assert code == 0
    assert "openpyxl" in output


def test_no_match_prints_friendly_message() -> None:
    cache.save_packages([OPENPYXL])
    code, output = run("zzznonexistentterm")
    assert code == 0
    assert "No packages matched" in output


def test_multi_term_query_where_nothing_covers_half_the_terms_is_a_clean_no_match() -> None:
    """Regression: candidates existed but none survived the gate -> min() of empty list."""
    cache.save_packages([OPENPYXL, make_pkg("pdfthing", "parse pdf files")])
    code, output = run("search", "pdf", "qwzx", "vbnmq")
    assert code == 0
    assert "No packages matched" in output


def test_search_with_corruption_beyond_the_header_is_actionable() -> None:
    """Regression: only header corruption was handled; a damaged page mid-file crashed."""
    cache.save_packages(bulky_packages())
    cache.db_path().write_bytes(damage_middle_pages(cache.db_path().read_bytes()))
    assert cache.snapshot_info()["count"] == 400  # header still reads fine

    code, output = run("search", "parse", "pdf")

    assert code != 0
    assert "findmypylibrary refresh" in output


def test_query_starting_with_a_no_argument_command_word_is_a_search() -> None:
    cache.save_packages([make_pkg("statusbar", "status bar widget for terminals")])
    code, output = run("status", "bar", "widget")
    assert code == 0
    assert "statusbar" in output


def test_an_option_after_a_no_argument_command_is_a_usage_error_not_a_search() -> None:
    """Regression: `findmypylibrary status --json` ran a search for the word "status"."""
    cache.save_packages([make_pkg("statusbar", "status bar widget for terminals")])
    code, output = run("status", "--json")
    assert code == 2
    assert "statusbar" not in output


def test_short_help_flag_works() -> None:
    code, output = run("-h")
    assert code == 0
    assert "refresh" in output


def test_options_may_come_before_a_bare_query() -> None:
    cache.save_packages([make_pkg(f"pkg{i}", "parse pdf files", 1000 * (i + 1)) for i in range(5)])
    code, output = run("-n", "2", "parse", "pdf")
    assert code == 0
    assert output.count("pypi.org/project/") == 2


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_num_must_be_positive(bad: str) -> None:
    cache.save_packages([OPENPYXL])
    code, output = run("search", "excel", "-n", bad)
    assert code != 0
    assert "-n" in output or "num" in output.lower()


def test_num_option_limits_results() -> None:
    cache.save_packages([make_pkg(f"pkg{i}", "parse pdf files", 1000 * (i + 1)) for i in range(5)])
    code, output = run("search", "parse", "pdf", "-n", "2")
    assert code == 0
    assert output.count("pypi.org/project/") == 2


def test_json_output_is_machine_readable() -> None:
    cache.save_packages([OPENPYXL])
    code, output = run("search", "excel", "--json")
    assert code == 0
    results = json.loads(output)
    assert results[0]["name"] == "openpyxl"
    assert results[0]["downloads_30d"] == 339_316_525
    assert set(results[0]) == {
        "name",
        "summary",
        "score",
        "downloads_30d",
        "last_release",
        "version",
        "homepage",
        "url",
    }
    assert results[0]["url"] == "https://pypi.org/project/openpyxl/"
    assert 0.0 <= results[0]["score"] <= 1.0


def test_stale_snapshot_warns_but_still_answers() -> None:
    cache.save_packages([OPENPYXL])
    age_snapshot(days=cli.STALE_AFTER_DAYS + 5)
    code, output = run("search", "excel")
    assert code == 0
    assert "openpyxl" in output
    assert "days old" in output


def test_fresh_snapshot_does_not_warn() -> None:
    cache.save_packages([OPENPYXL])
    _, output = run("search", "excel")
    assert "days old" not in output


# --- status ----------------------------------------------------------------


def test_status_reports_count_and_age() -> None:
    cache.save_packages([OPENPYXL])
    code, output = run("status")
    assert code == 0
    assert "1 packages" in output
    assert str(cache.db_path()) in output


def test_status_without_snapshot_is_actionable() -> None:
    code, output = run("status")
    assert code != 0
    assert "findmypylibrary refresh" in output


# --- refresh: prebuilt snapshot ----------------------------------------------


def test_refresh_downloads_prebuilt_snapshot_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_download(dest_path: Path) -> None:
        assert dest_path == cache.db_path()
        cache.save_packages([OPENPYXL])

    monkeypatch.setattr(fetch, "download_prebuilt_snapshot", fake_download)

    code, output = run("refresh")

    assert code == 0
    assert "1 packages" in output


def test_refresh_never_silently_falls_back_to_crawling_pypi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_download(dest_path: Path) -> None:
        raise fetch.FetchError("No prebuilt snapshot available. Try --build-locally.")

    def must_not_crawl(limit: int) -> list[dict]:
        raise AssertionError("refresh crawled PyPI without --build-locally")

    monkeypatch.setattr(fetch, "download_prebuilt_snapshot", failing_download)
    monkeypatch.setattr(fetch, "get_top_packages", must_not_crawl)

    code, output = run("refresh")

    assert code != 0
    assert "--build-locally" in output
    assert "Traceback" not in output


# --- refresh: live crawl -----------------------------------------------------


def test_refresh_build_locally_crawls_and_skips_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_download(dest_path: Path) -> None:
        raise AssertionError("downloaded the prebuilt snapshot despite --build-locally")

    monkeypatch.setattr(fetch, "download_prebuilt_snapshot", must_not_download)
    fake_crawl(monkeypatch, listed=10, fetched=10)

    code, output = run("refresh", "--build-locally", "--limit", "10")

    assert code == 0
    assert cache.snapshot_info()["count"] == 10


def test_refresh_refuses_to_replace_good_snapshot_with_a_mostly_failed_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a network drop mid-crawl used to overwrite 15k packages with a handful."""
    cache.save_packages([OPENPYXL])
    fake_crawl(monkeypatch, listed=100, fetched=40)

    code, output = run("refresh", "--build-locally")

    assert code != 0
    assert "40 of 100" in output
    assert [c["name"] for c in cache.search_candidates('"excel"')] == ["openpyxl"]


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_refresh_limit_must_be_positive_and_never_wipes_the_snapshot(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """Regression: --limit 0 passed the 95% guard (0 < 0.95*0 is false) and saved 0 packages."""
    cache.save_packages([OPENPYXL])
    fake_crawl(monkeypatch, listed=0, fetched=0)

    code, _ = run("refresh", "--build-locally", "--limit", bad)

    assert code != 0
    assert cache.snapshot_info()["count"] == 1


def test_refresh_with_an_empty_package_list_keeps_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache.save_packages([OPENPYXL])
    fake_crawl(monkeypatch, listed=0, fetched=0)

    code, output = run("refresh", "--build-locally")

    assert code != 0
    assert cache.snapshot_info()["count"] == 1


def test_unusable_cache_directory_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where the cache directory should go")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))

    for command in (["status"], ["search", "excel"], ["refresh"]):
        code, output = run(*command)
        assert code != 0
        assert "cache directory" in output


def test_limit_without_build_locally_is_rejected_instead_of_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `refresh --limit 5` ignored --limit and downloaded the full snapshot."""

    def must_not_download(dest_path: Path) -> None:
        raise AssertionError("downloaded although the options were contradictory")

    monkeypatch.setattr(fetch, "download_prebuilt_snapshot", must_not_download)
    code, output = run("refresh", "--limit", "5")
    assert code == 2
    assert "--build-locally" in output


def test_refresh_tolerates_a_few_missing_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_crawl(monkeypatch, listed=100, fetched=99)
    code, output = run("refresh", "--build-locally")
    assert code == 0
    assert "99" in output


def test_refresh_network_failure_is_actionable_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_list(limit: int) -> list[dict]:
        raise fetch.FetchError("Could not download the top package list. Check your connection.")

    monkeypatch.setattr(fetch, "get_top_packages", failing_list)

    code, output = run("refresh", "--build-locally")

    assert code != 0
    assert "Check your connection" in output
    assert "Traceback" not in output


# --- golden ------------------------------------------------------------------


def test_golden_fails_with_nonzero_exit_when_quality_is_below_the_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache.save_packages([OPENPYXL])
    report = {"passed": 1, "total": 10, "pass_rate": 0.1, "failures": [{"query": "q", "got": []}]}
    monkeypatch.setattr(golden, "evaluate", lambda: report)

    code, output = run("golden")

    assert code != 0
    assert "1/10" in output


def test_golden_passes_when_quality_meets_the_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    cache.save_packages([OPENPYXL])
    report = {"passed": 10, "total": 10, "pass_rate": 1.0, "failures": []}
    monkeypatch.setattr(golden, "evaluate", lambda: report)

    code, output = run("golden")

    assert code == 0
    assert "10/10" in output


# --- startup cost ------------------------------------------------------------


def test_search_path_does_not_import_the_http_stack() -> None:
    """httpx costs ~140 ms to import and only `refresh` needs it."""
    code = "import sys, findmypylibrary.cli; sys.exit('httpx' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], timeout=30).returncode == 0
