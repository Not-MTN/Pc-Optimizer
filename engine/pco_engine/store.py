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

SCHEMA_VERSION = 5


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


def migrate(conn: sqlite3.Connection, *, clock=time.time) -> list[int]:
    """Apply pending migrations. Backs up the file first (if it already exists)."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [(v, sql) for v, sql in MIGRATIONS if v > current]
    if not pending:
        return []

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
            conn.execute(f"PRAGMA user_version={version}")
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
