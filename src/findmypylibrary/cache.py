"""Local sqlite cache holding the offline PyPI snapshot."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    name TEXT PRIMARY KEY,
    summary TEXT,
    keywords TEXT,
    homepage TEXT,
    version TEXT,
    last_release TEXT,
    download_count INTEGER,
    rank INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "findmypylibrary"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return cache_dir() / "snapshot.sqlite"


def exists() -> bool:
    return db_path().exists()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.executescript(SCHEMA)
    return conn


def save_packages(results: list[dict]) -> None:
    """Replace the cached snapshot with a fresh set of packages."""
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM packages")
            conn.executemany(
                """
                INSERT INTO packages
                    (name, summary, keywords, homepage, version,
                     last_release, download_count, rank)
                VALUES
                    (:name, :summary, :keywords, :homepage, :version,
                     :last_release, :download_count, :rank)
                """,
                results,
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('refreshed_at', ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('count', ?)",
                (str(len(results)),),
            )
    finally:
        conn.close()


def load_all() -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM packages").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_meta(key: str) -> str | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()
