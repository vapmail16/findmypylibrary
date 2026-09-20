"""Gets snapshot data: either the prebuilt monthly snapshot (one download) or a
live build from two public sources (no API key needed for either)."""

from __future__ import annotations

import asyncio
import gzip
import re
import zlib
from collections.abc import Callable
from pathlib import Path

import httpx

from . import __version__, cache
from .errors import FetchError

TOP_PACKAGES_URL = (
    "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/"
    "top-pypi-packages-30-days.min.json"
)
PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"

# A fixed tag, not "releases/latest": publishing a code release on GitHub must
# never change what this URL points at.
SNAPSHOT_REPO = "vapmail16/findmypylibrary"
SNAPSHOT_TAG = "snapshot-latest"

USER_AGENT = f"findmypylibrary/{__version__} (+https://pypi.org/project/findmypylibrary/)"
CONCURRENCY = 25
REQUEST_TIMEOUT = 10.0
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.5
DESCRIPTION_MAX_CHARS = 3000
# The real snapshot is ~11 MB gzipped / ~20 MB raw; anything far beyond is not ours.
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_RST_DIRECTIVE_RE = re.compile(r"^\s*\.\. .*::.*$", re.MULTILINE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def snapshot_asset_name() -> str:
    return f"snapshot-v{cache.SCHEMA_VERSION}.sqlite.gz"


def snapshot_url() -> str:
    return (
        f"https://github.com/{SNAPSHOT_REPO}/releases/download/"
        f"{SNAPSHOT_TAG}/{snapshot_asset_name()}"
    )


def clean_description(text: str | None) -> str:
    """README text reduced to searchable prose: no badges, links, URLs or markup."""
    cleaned = _MD_IMAGE_RE.sub(" ", text or "")
    cleaned = _MD_LINK_RE.sub(r"\1", cleaned)
    cleaned = _URL_RE.sub(" ", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = _RST_DIRECTIVE_RE.sub(" ", cleaned)
    return clean_text(cleaned)[:DESCRIPTION_MAX_CHARS].rstrip()


def clean_text(value: object) -> str:
    """Upstream text as one printable line: results are shown in a terminal, so no
    control characters (escape sequences) may survive."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    text = "" if value is None else str(value)
    return " ".join(_CONTROL_CHARS_RE.sub("", text).split())


def topic_classifiers(classifiers: list[str] | None) -> str:
    """The subject-matter classifiers ("Topic ::", "Framework ::"), prefix removed."""
    topics = []
    for classifier in classifiers or []:
        prefix, _, value = classifier.partition(" :: ")
        if prefix in ("Topic", "Framework") and value:
            topics.append(value)
    return "; ".join(topics)


def get_top_packages(limit: int = 15000) -> list[dict]:
    """Source 1: the top-downloaded package list (name + 30-day download count)."""
    try:
        resp = httpx.get(TOP_PACKAGES_URL, timeout=30.0, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchError(
            f"Could not download the top package list ({exc}). Check your connection and retry."
        ) from exc
    try:
        rows = resp.json()["rows"]
        return list(rows[: min(limit, len(rows))])
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchError(
            "The top package list came back in an unexpected format. Retry later; if it "
            "persists, the upstream dataset changed and findmypylibrary needs an update."
        ) from exc


async def _fetch_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, row: dict, rank: int
) -> dict | None:
    """Source 2: one package's metadata from the PyPI JSON API."""
    name = row["project"]
    async with sem:
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get(PYPI_JSON_URL.format(name=name), timeout=REQUEST_TIMEOUT)
            except httpx.HTTPError:
                await asyncio.sleep(RETRY_BASE_DELAY * (attempt + 1))
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                await asyncio.sleep(RETRY_BASE_DELAY * (attempt + 1))
                continue
            if resp.status_code != 200:
                return None

            # One odd response (HTML error page, non-object JSON) must cost one
            # package, never the whole crawl.
            try:
                data = resp.json()
            except ValueError:
                return None
            if not isinstance(data, dict):
                return None
            info = data.get("info") or {}
            urls = data.get("urls") or []
            return {
                "name": clean_text(info.get("name")) or name,
                "summary": clean_text(info.get("summary")),
                "keywords": clean_text(info.get("keywords")),
                "topics": topic_classifiers(info.get("classifiers")),
                "description": clean_description(info.get("description")),
                "homepage": (info.get("project_urls") or {}).get("Homepage")
                or info.get("home_page")
                or "",
                "version": clean_text(info.get("version")),
                "last_release": urls[0].get("upload_time_iso_8601") if urls else None,
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
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        sem = asyncio.Semaphore(CONCURRENCY)
        tasks = [
            asyncio.create_task(_fetch_one(client, sem, row, i + 1)) for i, row in enumerate(rows)
        ]
        for coro in asyncio.as_completed(tasks):
            detail = await coro
            done += 1
            if on_progress:
                on_progress(done, total)
            if detail:
                results.append(detail)
    results.sort(key=lambda pkg: pkg["rank"])
    return results


def run_refresh_sync(
    rows: list[dict], on_progress: Callable[[int, int], None] | None = None
) -> list[dict]:
    """Fetch per-package metadata for every row, with bounded concurrency."""
    return asyncio.run(_run_refresh(rows, on_progress))


def download_prebuilt_snapshot(dest_path: Path) -> None:
    """Download, decompress and validate the prebuilt snapshot, then swap it in.

    dest_path is only replaced once the download is proven valid, so a failed
    or corrupt download never damages an existing good snapshot.
    """
    hint = "Retry later, or run 'findmypylibrary refresh --build-locally' to crawl PyPI directly."
    gz_path = dest_path.with_suffix(".download.gz")
    db_tmp_path = dest_path.with_suffix(".download")
    try:
        try:
            with httpx.stream(
                "GET",
                snapshot_url(),
                follow_redirects=True,
                timeout=60.0,
                headers={"User-Agent": USER_AGENT},
            ) as resp:
                if resp.status_code != 200:
                    raise FetchError(
                        f"No prebuilt snapshot available (HTTP {resp.status_code}). {hint}"
                    )
                try:
                    received = 0
                    with open(gz_path, "wb") as f:
                        for chunk in resp.iter_bytes():
                            received += len(chunk)
                            if received > MAX_DOWNLOAD_BYTES:
                                raise FetchError(
                                    f"The download is larger than {MAX_DOWNLOAD_BYTES} bytes, "
                                    f"which no real snapshot is. {hint}"
                                )
                            f.write(chunk)
                except OSError as exc:
                    raise FetchError(
                        f"Could not save the snapshot to {dest_path} ({exc}). "
                        "Check permissions and disk space."
                    ) from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"Could not download the prebuilt snapshot ({exc}). {hint}") from exc

        try:
            with gzip.open(gz_path, "rb") as src, open(db_tmp_path, "wb") as dst:
                written = 0
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_SNAPSHOT_BYTES:
                        raise OSError("decompressed size exceeds the snapshot size limit")
                    dst.write(chunk)
            cache.validate_snapshot(db_tmp_path)
        except (OSError, EOFError, zlib.error, cache.SnapshotError) as exc:
            raise FetchError(f"The downloaded snapshot is invalid ({exc}). {hint}") from exc

        try:
            db_tmp_path.replace(dest_path)
        except OSError as exc:
            raise FetchError(f"Could not save the snapshot to {dest_path} ({exc}).") from exc
    finally:
        gz_path.unlink(missing_ok=True)
        db_tmp_path.unlink(missing_ok=True)
