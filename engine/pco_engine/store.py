"""Storage: connection, schema, and forward-only migrations.

Rust port: `rusqlite` with the same statements; the migration list becomes a
`const MIGRATIONS: &[(&str, &str)]`.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from typing import Any

SCHEMA_VERSION = 6


def _sql(name: str) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", name)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# (version, sql). Forward-only; a version is never edited after release.
MIGRATIONS: list[tuple[int, str]] = [
    (1, _sql("schema.sql")),
    (2, _sql("schema_2.sql")),
    (3, _sql("schema_3.sql")),
    (4, _sql("schema_4.sql")),
    (5, _sql("schema_5.sql")),
    (6, _sql("schema_6.sql")),
]


class StoreError(RuntimeError):
    pass


def connect(path: str) -> sqlite3.Connection:
    """Open (or create) a database with the production pragmas."""
    # check_same_thread=False: the service is shared by the HTTP bridge's worker
    # threads. Callers serialise access (server._LOCK); this is about keeping
    # multi-statement transactions in one logical order, not memory safety.
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _backup_before_migrate(conn: sqlite3.Connection,
                           current: int) -> str | None:
    """Copy the database next to itself before a migration runs.

    Returns the backup path, or None when there is nothing to back up (a brand
    new file) or the file is not on disk (in-memory test databases). Never
    raises: failing to back up must not be what stops the app from starting —
    but it is stamped so a failed migration can point at the reason.
    """
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    if not path or path == ":memory:":
        return None
    try:
        from pathlib import Path
        src = Path(path)
        if not src.exists() or src.stat().st_size == 0:
            return None
        target = src.with_name(f"{src.stem}.pre-v{current}.bak")
        if target.exists():                      # keep one per source version
            return str(target)
        dest = sqlite3.connect(str(target))
        try:
            conn.backup(dest)                    # consistent, unlike a file copy
        finally:
            dest.close()
        return str(target)
    except Exception:                            # noqa: BLE001 - see docstring
        return None


def migrate(conn: sqlite3.Connection, *, clock=time.time) -> list[int]:
    """Apply pending migrations. Backs up the file first, if there is one.

    The backup is not decoration. `executescript` commits implicitly, so a
    migration that fails halfway leaves the database in whatever state the last
    successful statement produced, and the version stamp that would let SQLite
    roll back never got written. The only way back is a copy made before the
    first byte changed — which this docstring promised for a long time without
    the code ever doing it.

    The copy is a plain file copy taken through SQLite's own backup API, so it is
    consistent even if another connection is mid-transaction.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [(v, sql) for v, sql in MIGRATIONS if v > current]
    if not pending:
        return []

    # Only worth copying a database that already has a schema. A brand-new file
    # has had its header written by connect(), so a size check is not enough —
    # ask SQLite whether anything is in it.
    has_tables = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
    if has_tables:
        _backup_before_migrate(conn, current)

    applied: list[int] = []
    for version, sql in pending:
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        started = time.monotonic()
        try:
            # executescript() commits implicitly, so it must run outside a
            # transaction; the version stamp is what makes the migration atomic.
            conn.executescript(sql)
            conn.execute(
                "INSERT OR REPLACE INTO migrations(version,applied_at,checksum,duration_ms) "
                "VALUES(?,?,?,?)",
                (version, int(clock() * 1000), checksum,
                 int((time.monotonic() - started) * 1000)))
            # PRAGMA does not take bound parameters. force it through int() so
            # the f-string can only ever contain a number, never SQL.
            conn.execute(f"PRAGMA user_version={int(version)}")
        except Exception:
            raise StoreError(f"migration {version} failed") from None
        applied.append(version)

    _stamp(conn, "schema_version", str(max(v for v, _ in MIGRATIONS)))
    return applied


def _stamp(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, int(time.time() * 1000)))


def integrity(conn: sqlite3.Connection) -> tuple[bool, str]:
    row = conn.execute("PRAGMA quick_check").fetchone()
    ok = bool(row and row[0] == "ok")
    return ok, (row[0] if row else "no result")
