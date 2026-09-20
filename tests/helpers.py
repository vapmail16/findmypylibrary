"""Test data builders shared across test modules."""

from __future__ import annotations


def make_pkg(
    name: str,
    summary: str = "",
    downloads: int = 1000,
    last_release: str | None = "2026-01-01T00:00:00Z",
    keywords: str = "",
    topics: str = "",
    description: str = "",
) -> dict:
    return {
        "name": name,
        "summary": summary,
        "keywords": keywords,
        "topics": topics,
        "description": description,
        "homepage": "",
        "version": "1.0.0",
        "last_release": last_release,
        "download_count": downloads,
        "rank": 1,
    }


def bulky_packages() -> list[dict]:
    """Enough rows that the snapshot spans many sqlite pages."""
    return [make_pkg(f"pkg{i}", f"parse pdf files number {i} " * 20) for i in range(400)]


def damage_middle_pages(snapshot: bytes) -> bytes:
    """Overwrite 30-60% of the file's pages: the header and meta table stay readable,
    so the damage only shows once a query reads the index."""
    pages = len(snapshot) // 4096
    start, end = int(pages * 0.3) * 4096, int(pages * 0.6) * 4096
    return snapshot[:start] + b"\xff" * (end - start) + snapshot[end:]
