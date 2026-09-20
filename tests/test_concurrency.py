"""Concurrency test: while a refresh replaces the snapshot, a reader must never
see a partial one (rows or full-text index) and the refresh itself must not fail."""

from __future__ import annotations

import threading

from helpers import make_pkg

from findmypylibrary import cache


def batch(prefix: str, n: int) -> list[dict]:
    return [make_pkg(f"{prefix}{i}", "parse pdf files") for i in range(n)]


def test_reader_never_sees_a_partial_write_during_concurrent_refresh() -> None:
    cache.save_packages(batch("initial", 50))

    errors: list[Exception] = []
    observed: list[tuple[int, int]] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                rows = cache.snapshot_info()["count"]
                indexed = len(cache.search_candidates('"pdf"', limit=1000))
                observed.append((rows, indexed))
            except Exception as exc:  # pragma: no cover - failure path under test
                errors.append(exc)

    def writer() -> None:
        for i in range(20):
            try:
                cache.save_packages(batch(f"batch{i}", 50))
            except Exception as exc:  # pragma: no cover - failure path under test
                errors.append(exc)

    reader_thread = threading.Thread(target=reader)
    writer_thread = threading.Thread(target=writer)
    reader_thread.start()
    writer_thread.start()
    writer_thread.join()
    stop.set()
    reader_thread.join()

    assert errors == []
    assert observed
    assert all(pair == (50, 50) for pair in observed)
