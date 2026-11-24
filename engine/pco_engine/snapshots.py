"""Golden snapshots — small, fast, content-addressed.

A snapshot is a captured value for every managed address. Because it is small
(hundreds of keys, not a whole registry hive), capturing takes milliseconds and
restoring takes the same. That is the entire reason this product can promise
"you will never be stuck" instead of "System Restore, maybe".

Rust port: same table. Manifests are JSON blobs keyed by their SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .backend import Backend, BackendError
from .ids import new_id

KINDS = {"golden", "auto", "manual", "pre_risky"}


@dataclass(frozen=True)
class Snapshot:
    id: str
    kind: str
    at: int
    size_bytes: int
    manifest_hash: str
    parent_id: str | None
    note: str | None


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_hash(state: dict[str, Any]) -> str:
    return hashlib.sha256(_canon(state).encode("utf-8")).hexdigest()


class SnapshotStore:
    def __init__(self, conn: sqlite3.Connection, backend: Backend, clock) -> None:
        self.conn = conn
        self.backend = backend
        self.clock = clock

    # ---------------------------------------------------------------- capture
    def capture(self, addresses: list[str], *, kind: str = "manual",
                note: str | None = None, parent_id: str | None = None) -> Snapshot:
        if kind not in KINDS:
            raise ValueError(f"bad snapshot kind: {kind}")
        state: dict[str, Any] = {}
        for addr in sorted(set(addresses)):
            try:
                state[addr] = self.backend.read(addr)
            except BackendError:
                state[addr] = None      # recorded as absent, not skipped
        h = manifest_hash(state)
        size = len(_canon(state).encode("utf-8"))
        at = self.clock()
        snap = Snapshot(id=new_id(), kind=kind, at=at, size_bytes=size,
                        manifest_hash=h, parent_id=parent_id, note=note)
        with self.conn:
            self.conn.execute(
                "INSERT INTO snapshots(id,kind,at,size_bytes,manifest_hash,parent_id,note) "
                "VALUES(?,?,?,?,?,?,?)",
                (snap.id, snap.kind, snap.at, snap.size_bytes, snap.manifest_hash,
                 snap.parent_id, snap.note))
            # the manifest itself lives beside the row for fast restore
            self.conn.execute(
                "INSERT INTO settings(scope,key,value_json,schema,updated_at) "
                "VALUES('snapshot',?,?,'snapshot.v1',?) "
                "ON CONFLICT(scope,key) DO UPDATE SET value_json=excluded.value_json",
                (snap.id, _canon(state), at))
        return snap

    # ---------------------------------------------------------------- restore
    def state_of(self, snapshot_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT value_json FROM settings WHERE scope='snapshot' AND key=?",
            (snapshot_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown snapshot: {snapshot_id}")
        return json.loads(row[0])

    def verify(self, snapshot_id: str) -> bool:
        """Content-addressed: the manifest must still hash to its recorded value."""
        state = self.state_of(snapshot_id)
        row = self.conn.execute(
            "SELECT manifest_hash FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if row is None:
            return False
        ok = manifest_hash(state) == row[0]
        if ok:
            with self.conn:
                self.conn.execute(
                    "UPDATE snapshots SET verified_at=? WHERE id=?",
                    (self.clock(), snapshot_id))
        return ok

    def diff(self, a: str, b: str) -> dict[str, Any]:
        """What changed between two snapshots — the basis of the time machine."""
        sa, sb = self.state_of(a), self.state_of(b)
        addrs = sorted(set(sa) | set(sb))
        changed, added, removed = [], [], []
        for addr in addrs:
            va, vb = sa.get(addr), sb.get(addr)
            if va == vb:
                continue
            if va is None:
                added.append({"address": addr, "new": vb})
            elif vb is None:
                removed.append({"address": addr, "old": va})
            else:
                changed.append({"address": addr, "old": va, "new": vb})
        return {"changed": changed, "added": added, "removed": removed,
                "counts": {"changed": len(changed), "added": len(added),
                           "removed": len(removed)}}

    def list(self, limit: int = 50) -> list[Snapshot]:
        rows = self.conn.execute(
            "SELECT id,kind,at,size_bytes,manifest_hash,parent_id,note FROM snapshots "
            "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [Snapshot(*r) for r in rows]

    def latest(self, kind: str | None = None) -> Snapshot | None:
        if kind:
            row = self.conn.execute(
                "SELECT id,kind,at,size_bytes,manifest_hash,parent_id,note FROM snapshots "
                "WHERE kind=? ORDER BY at DESC LIMIT 1", (kind,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT id,kind,at,size_bytes,manifest_hash,parent_id,note FROM snapshots "
                "ORDER BY at DESC LIMIT 1").fetchone()
        return Snapshot(*row) if row else None
