"""Tests for the CLI: command wiring, the DefaultGroup fallback, and error paths.

fetch.* calls are monkeypatched here — network behavior is covered by
test_fetch.py. This file proves the CLI wires those pieces together correctly.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from findmypylibrary import cache, cli


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def seed_cache() -> None:
    cache.save_packages(
        [
            {
                "name": "openpyxl",
                "summary": "A Python library to read/write Excel 2010 xlsx/xlsm files",
                "keywords": "",
                "homepage": "",
                "version": "3.1.0",
                "last_release": "2026-01-01T00:00:00Z",
                "download_count": 339_316_525,
                "rank": 1,
            }
        ]
    )


def test_help_lists_both_commands() -> None:
    result = CliRunner().invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "refresh" in result.output
    assert "search" in result.output


def test_search_without_snapshot_gives_clear_error() -> None:
    result = CliRunner().invoke(cli.main, ["search", "excel"])
    assert result.exit_code != 0
    assert "findmypylibrary refresh" in result.output


def test_search_returns_ranked_results() -> None:
    seed_cache()
    result = CliRunner().invoke(cli.main, ["search", "excel", "spreadsheets"])
    assert result.exit_code == 0
    assert "openpyxl" in result.output


def test_bare_query_falls_back_to_search_without_subcommand_name() -> None:
    seed_cache()
    result = CliRunner().invoke(cli.main, ["excel", "spreadsheets"])
    assert result.exit_code == 0
    assert "openpyxl" in result.output


def test_no_match_prints_friendly_message() -> None:
    seed_cache()
    result = CliRunner().invoke(cli.main, ["zzznonexistentterm"])
    assert result.exit_code == 0
    assert "No packages matched" in result.output


def test_num_option_limits_results() -> None:
    cache.save_packages(
        [
            {
                "name": f"pkg{i}",
                "summary": "parse pdf files",
                "keywords": "",
                "homepage": "",
                "version": "1.0",
                "last_release": "2026-01-01T00:00:00Z",
                "download_count": 1000 * (i + 1),
                "rank": i,
            }
            for i in range(5)
        ]
    )
    result = CliRunner().invoke(cli.main, ["search", "parse", "pdf", "-n", "2"])
    assert result.exit_code == 0
    assert result.output.count("pypi.org/project/") == 2


def test_refresh_builds_and_saves_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.fetch,
        "get_top_packages",
        lambda limit: [{"project": "boto3", "download_count": 100}],
    )
    monkeypatch.setattr(
        cli.fetch,
        "run_refresh_sync",
        lambda rows, on_progress=None: [
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
        ],
    )

    result = CliRunner().invoke(cli.main, ["refresh", "--limit", "1"])

    assert result.exit_code == 0
    assert "Cached 1 packages" in result.output
    assert cache.exists() is True
