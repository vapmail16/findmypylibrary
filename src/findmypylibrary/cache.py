"""Local sqlite snapshot: package metadata plus a full-text (FTS5) index.

The FTS5 index ships inside the snapshot, so queries never rebuild an index.
It uses the porter stemmer ("images" matches "imaging", "plot" matches
"plotting") and is contentless: long description text is searchable but not
stored, which keeps the snapshot small.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import SnapshotError

# Bump whenever the tables below change shape. The prebuilt snapshot's file
# name carries this number, so an old client never downloads a newer layout.
SCHEMA_VERSION = 2

# bm25 column weights, in packages_fts column order:
# name, summary, keywords, topics, description
FTS_WEIGHTS = (8.0, 5.0, 4.0, 3.0, 1.0)
CORE_COLUMNS = ("name", "summary", "keywords", "topics")

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    summary TEXT,
    keywords TEXT,
    topics TEXT,
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
CREATE VIRTUAL TABLE IF NOT EXISTS packages_fts USING fts5(
    name, summary, keywords, topics, description,
    content='', tokenize='porter unicode61'
);
"""

REFRESH_HINT = "Run 'findmypylibrary refresh' to download a fresh one."


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "findmypylibrary"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SnapshotError(
            f"Cannot use the cache directory {d} ({exc}). Fix its permissions, or point "
            "XDG_CACHE_HOME at a writable location."
        ) from exc
    return d


def db_path() -> Path:
    return cache_dir() / "snapshot.sqlite"


def exists() -> bool:
    return db_path().exists()


@contextmanager
def _open_snapshot(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a snapshot read-only, raising SnapshotError unless it is usable."""
    if not path.exists():
        raise SnapshotError(f"No local snapshot yet. {REFRESH_HINT}")
    # A plain connection made read-only by pragma. Opening with a "?mode=ro" URI
    # instead makes a concurrent refresh fail with SQLITE_IOERR_LOCK.
    try:
        conn = sqlite3.connect(path)
    except sqlite3.DatabaseError as exc:
        raise SnapshotError(f"The local snapshot cannot be opened ({exc}). {REFRESH_HINT}") from exc
    try:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise SnapshotError(f"The local snapshot is unreadable. {REFRESH_HINT}") from exc
        if row is None or row["value"] != str(SCHEMA_VERSION):
            raise SnapshotError(
                f"The local snapshot was built for a different version. {REFRESH_HINT}"
            )
        try:
            yield conn
        except sqlite3.DatabaseError as exc:
            # Damage beyond the first page only shows up once a query reads it.
            raise SnapshotError(f"The local snapshot is damaged. {REFRESH_HINT}") from exc
    finally:
        conn.close()


def validate_snapshot(path: Path) -> None:
    """Raise SnapshotError unless path is a non-empty snapshot this version can read."""
    with _open_snapshot(path) as conn:
        # quick_check walks every page, including the full-text index tables.
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SnapshotError(f"The snapshot is damaged. {REFRESH_HINT}")
        count = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        conn.execute("SELECT rowid FROM packages_fts WHERE packages_fts MATCH '\"python\"'")
    if count == 0:
        raise SnapshotError(f"The snapshot is empty. {REFRESH_HINT}")


def _is_compatible(path: Path) -> bool:
    try:
        with _open_snapshot(path) as conn:
            return bool(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok")
    except SnapshotError:
        return False


def save_packages(results: list[dict]) -> None:
    """Replace the snapshot (rows and full-text index) in one transaction."""
    path = db_path()
    try:
        _write_snapshot(path, results)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise SnapshotError(
            f"Could not write the snapshot to {path} ({exc}). Check permissions and disk space."
        ) from exc


def _write_snapshot(path: Path, results: list[dict]) -> None:
    if path.exists() and not _is_compatible(path):
        path.unlink()

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM packages")
            conn.execute("INSERT INTO packages_fts(packages_fts) VALUES ('delete-all')")
            for row_id, pkg in enumerate(results, 1):
                conn.execute(
                    """
                    INSERT INTO packages
                        (id, name, summary, keywords, topics, homepage, version,
                         last_release, download_count, rank)
                    VALUES
                        (:id, :name, :summary, :keywords, :topics, :homepage, :version,
                         :last_release, :download_count, :rank)
                    """,
                    {**pkg, "id": row_id},
                )
                conn.execute(
                    """
                    INSERT INTO packages_fts
                        (rowid, name, summary, keywords, topics, description)
                    VALUES (:id, :name, :summary, :keywords, :topics, :description)
                    """,
                    {**pkg, "id": row_id},
                )
            meta = {
                "schema_version": str(SCHEMA_VERSION),
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "count": str(len(results)),
            }
            conn.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", list(meta.items())
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def search_candidates(
    match_expr: str, columns: tuple[str, ...] | None = None, limit: int = 300
) -> list[dict]:
    """Packages matching an FTS5 expression, best first, each with a 'relevance' score.

    With columns given, only hits inside those index columns match and score.
    """
    if columns:
        match_expr = f"{{{' '.join(columns)}}} : ({match_expr})"
    weights = ", ".join(str(w) for w in FTS_WEIGHTS)
    with _open_snapshot(db_path()) as conn:
        rows = conn.execute(
            f"""
            SELECT p.*, -bm25(packages_fts, {weights}) AS relevance
            FROM packages_fts
            JOIN packages p ON p.id = packages_fts.rowid
            WHERE packages_fts MATCH ?
            ORDER BY relevance DESC
            LIMIT ?
            """,
            (match_expr, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def matching_ids(match_expr: str) -> set[int]:
    """Ids of every package matching an FTS5 expression."""
    with _open_snapshot(db_path()) as conn:
        rows = conn.execute(
            "SELECT rowid FROM packages_fts WHERE packages_fts MATCH ?", (match_expr,)
        ).fetchall()
    return {r[0] for r in rows}


def get_meta(key: str) -> str | None:
    with _open_snapshot(db_path()) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def snapshot_info() -> dict:
    """Count, build time, age in days, schema version and path of the snapshot."""
    with _open_snapshot(db_path()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    refreshed_at = meta.get("refreshed_at", "")
    try:
        age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(refreshed_at)).days
    except (ValueError, TypeError) as exc:
        raise SnapshotError(f"The local snapshot has no valid build date. {REFRESH_HINT}") from exc
    return {
        "count": count,
        "refreshed_at": refreshed_at,
        "age_days": age_days,
        "schema_version": int(meta["schema_version"]),
        "path": str(db_path()),
    }
