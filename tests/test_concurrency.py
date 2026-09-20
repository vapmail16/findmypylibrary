"""Concurrency test: a reader must never see a corrupt/partial snapshot
while a refresh is writing a new one."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from findmypylibrary import cache


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def make_batch(prefix: str, n: int) -> list[dict]:
    return [
        {
            "name": f"{prefix}{i}",
            "summary": "x",
            "keywords": "",
            "homepage": "",
            "version": "1.0",
            "last_release": "2026-01-01T00:00:00Z",
            "download_count": i,
            "rank": i,
        }
        for i in range(n)
    ]


def test_reader_never_sees_a_partial_write_during_concurrent_refresh() -> None:
    cache.save_packages(make_batch("initial", 50))

    errors: list[Exception] = []
    observed_counts: list[int] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                rows = cache.load_all()
                observed_counts.append(len(rows))
            except Exception as exc:  # pragma: no cover - failure path under test
                errors.append(exc)

    def writer() -> None:
        for i in range(20):
            cache.save_packages(make_batch(f"batch{i}", 50))

    reader_thread = threading.Thread(target=reader)
    writer_thread = threading.Thread(target=writer)

    reader_thread.start()
    writer_thread.start()
    writer_thread.join()
    stop.set()
    reader_thread.join()

    assert errors == []
    # Every observed read must be one full, consistent snapshot (50 rows) —
    # never a half-written state.
    assert all(count == 50 for count in observed_counts)
