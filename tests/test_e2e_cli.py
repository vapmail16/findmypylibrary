"""Black-box end-to-end tests: invoke the real installed CLI binary as a
subprocess and assert on real stdout, exit codes. This is the CLI-shaped
equivalent of a Playwright end-to-end test for a browser UI — there is no
browser here, so we exercise the actual, installed entry point instead of
calling Python functions directly.

No network calls: the snapshot cache is pre-seeded before the subprocess runs.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from findmypylibrary import cache


@pytest.fixture()
def seeded_cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
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
    env = dict(os.environ)
    env["XDG_CACHE_HOME"] = str(tmp_path)
    return env


def _installed_binary() -> str:
    bin_dir = Path(sys.executable).parent
    for name in ("findmypylibrary.exe", "findmypylibrary"):
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)
    return "findmypylibrary"


def test_installed_cli_help_runs() -> None:
    result = subprocess.run(
        [_installed_binary(), "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0
    assert "refresh" in result.stdout


def test_installed_cli_search_end_to_end(seeded_cache_env: dict) -> None:
    result = subprocess.run(
        [_installed_binary(), "excel", "spreadsheets"],
        capture_output=True,
        text=True,
        timeout=10,
        env=seeded_cache_env,
    )
    assert result.returncode == 0
    assert "openpyxl" in result.stdout


def test_installed_cli_missing_snapshot_exits_nonzero(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["XDG_CACHE_HOME"] = str(tmp_path / "empty")
    result = subprocess.run(
        [_installed_binary(), "search", "excel"],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode != 0
    assert "findmypylibrary refresh" in result.stderr + result.stdout
