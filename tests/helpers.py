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
