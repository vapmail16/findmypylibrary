"""Builds the local offline snapshot from public PyPI data (no API key needed)."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

TOP_PACKAGES_URL = (
    "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/"
    "top-pypi-packages-30-days.min.json"
)
PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
USER_AGENT = "findmypylibrary-refresh/0.1 (+https://pypi.org/project/findmypylibrary/)"
CONCURRENCY = 25
REQUEST_TIMEOUT = 10.0
MAX_RETRIES = 3


def get_top_packages(limit: int = 15000) -> list[dict]:
    """Fetch the top-downloaded package list (name + 30-day download count)."""
    resp = httpx.get(TOP_PACKAGES_URL, timeout=30.0, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    rows = resp.json()["rows"]
    return rows[: min(limit, len(rows))]


async def _fetch_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, row: dict, rank: int
) -> dict | None:
    name = row["project"]
    async with sem:
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get(PYPI_JSON_URL.format(name=name), timeout=REQUEST_TIMEOUT)
            except httpx.HTTPError:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            data = resp.json()
            info = data.get("info") or {}
            urls = data.get("urls") or []
            last_release = urls[0].get("upload_time_iso_8601") if urls else None
            return {
                "name": info.get("name") or name,
                "summary": info.get("summary") or "",
                "keywords": info.get("keywords") or "",
                "homepage": (info.get("project_urls") or {}).get("Homepage")
                or info.get("home_page")
                or "",
                "version": info.get("version") or "",
                "last_release": last_release,
                "download_count": row.get("download_count") or 0,
                "rank": rank,
            }
        return None


async def _run_refresh(
    rows: list[dict], on_progress: Callable[[int, int], None] | None
) -> list[dict]:
    total = len(rows)
    done = 0
    results: list[dict] = []
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        sem = asyncio.Semaphore(CONCURRENCY)
        tasks = [
            asyncio.create_task(_fetch_one(client, sem, row, i + 1))
            for i, row in enumerate(rows)
        ]
        for coro in asyncio.as_completed(tasks):
            detail = await coro
            done += 1
            if on_progress:
                on_progress(done, total)
            if detail:
                results.append(detail)
    return results


def run_refresh_sync(
    rows: list[dict], on_progress: Callable[[int, int], None] | None = None
) -> list[dict]:
    """Fetch per-package metadata for every row, with bounded concurrency."""
    return asyncio.run(_run_refresh(rows, on_progress))
