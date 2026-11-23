"""The journal — append-only, hash-chained, crash-safe.

Why this file matters more than any other: it is the thing that lets the app
say "you will never be stuck" and mean it. Every design choice here is about
surviving a crash at the worst possible moment.

Guarantees
----------
1. Append-only, enforced by SQLite triggers as well as by code.
2. Every row's checksum includes the previous row's checksum → tampering or
   truncation is detectable (verify_chain()).
3. `begin` is written *before* a write is attempted. A crash therefore leaves
   an unmatched `begin`, and recovery knows exactly what to undo.
4. `journal_seq` is stored in meta after every row, so gaps are detectable.

Rust port: same table, same checksum formula. The formula is the contract.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

GENESIS = "0" * 64


class JournalError(RuntimeError):
    pass


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_checksum(prev_checksum: str, seq: int, tx_id: str, tweak_id: str,
                     phase: str, at: int, prestate: str, poststate: str | None,
                     error: str | None) -> str:
    """The exact, versioned checksum formula. Changing this is a schema migration."""
    payload = "|".join([
        prev_checksum, str(seq), tx_id, tweak_id, phase, str(at),
        prestate, poststate or "", error or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entry:
    seq: int
    tx_id: str
    tweak_id: str
    phase: str
    at: int
    prestate: dict[str, Any]
    poststate: dict[str, Any] | None
    error: str | None
    checksum: str
    prev_checksum: str


class Journal:
    def __init__(self, conn: sqlite3.Connection, clock) -> None:
        self.conn = conn
        self.clock = clock

    # ------------------------------------------------------------------ write
    def _last(self) -> tuple[int, str]:
        row = self.conn.execute(
            "SELECT seq, checksum FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            return 0, GENESIS
        return int(row[0]), str(row[1])

    def append(self, *, tx_id: str, tweak_id: str, phase: str,
               prestate: dict[str, Any], poststate: dict[str, Any] | None = None,
               error: str | None = None, at: int | None = None) -> Entry:
        if phase not in {"begin", "applied", "verified", "failed",
                         "reverted", "suspended", "confirmed"}:
            raise JournalError(f"bad phase: {phase}")
        at = self.clock() if at is None else at
        pre_s, post_s = _canon(prestate), (_canon(poststate) if poststate is not None else None)

        # A single IMMEDIATE transaction guarantees the seq/prev pair is consistent
        # even if two appends race (they cannot today, but the invariant is cheap).
        with self.conn:
            seq, prev = self._last()
            seq += 1
            checksum = compute_checksum(prev, seq, tx_id, tweak_id, phase, at,
                                        pre_s, post_s, error)
            self.conn.execute(
                "INSERT INTO journal(seq,tx_id,tweak_id,phase,at,prestate_json,"
                "poststate_json,error,checksum,prev_checksum) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (seq, tx_id, tweak_id, phase, at, pre_s, post_s, error, checksum, prev))
            self.conn.execute(
                "INSERT INTO meta(key,value,updated_at) VALUES('journal_seq',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (str(seq), at))
        return Entry(seq, tx_id, tweak_id, phase, at, prestate, poststate, error, checksum, prev)

    def ensure_transaction(self, tx_id: str, *, kind: str = "single",
                           source: str = "internal", note: str | None = None,
                           state: str = "open") -> None:
        """Foreign-key requirement: a journal row always belongs to a real
        transaction. Idempotent, so recovery can use it safely."""
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO transactions(id,kind,started_at,state,source,note) "
                "VALUES(?,?,?,?,?,?)",
                (tx_id, kind, self.clock(), state, source, note))

    # ------------------------------------------------------------------- read
    def entries(self, tx_id: str | None = None) -> list[Entry]:
        if tx_id:
            rows = self.conn.execute(
                "SELECT seq,tx_id,tweak_id,phase,at,prestate_json,poststate_json,error,"
                "checksum,prev_checksum FROM journal WHERE tx_id=? ORDER BY seq", (tx_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT seq,tx_id,tweak_id,phase,at,prestate_json,poststate_json,error,"
                "checksum,prev_checksum FROM journal ORDER BY seq").fetchall()
        return [
            Entry(r[0], r[1], r[2], r[3], r[4],
                  json.loads(r[5]), json.loads(r[6]) if r[6] else None,
                  r[7], r[8], r[9])
            for r in rows
        ]

    def open_transactions(self) -> list[str]:
        """Transactions that were never closed — i.e. the process died mid-work.

        The authority is `transactions.state`, not the journal phases:
          - 'open'    → we started but never finished (crash / power loss)  ← recover
          - 'armed'   → deliberately waiting for the user's confirmation    ← leave alone
          - anything else (committed/reverted/failed) → finished
        A committed transaction legitimately has no `confirmed` journal row,
        so inferring from phases alone would re-revert good changes on every boot.
        """
        rows = self.conn.execute(
            """
            SELECT t.id FROM transactions t
            WHERE t.state = 'open'
              AND EXISTS (SELECT 1 FROM journal j WHERE j.tx_id = t.id AND j.phase = 'begin')
              AND NOT EXISTS (
                SELECT 1 FROM journal k
                WHERE k.tx_id = t.id AND k.phase IN ('reverted','failed')
              )
            ORDER BY t.started_at
            """
        ).fetchall()
        return [r[0] for r in rows]

    def interrupted(self) -> list[Entry]:
        """Unmatched `begin` entries, newest first — the recovery work list."""
        out: list[Entry] = []
        for tx_id in self.open_transactions():
            begins = [e for e in self.entries(tx_id) if e.phase == "begin"]
            if begins:
                out.extend(begins)
        return sorted(out, key=lambda e: e.seq, reverse=True)

    # --------------------------------------------------------------- verify
    def verify_chain(self) -> tuple[bool, int, str]:
        """Verify the whole chain. Returns (ok, rows_checked, detail)."""
        prev = GENESIS
        expected_seq = 1
        for e in self.entries():
            if e.seq != expected_seq:
                return False, expected_seq, f"sequence gap at {expected_seq} (found {e.seq})"
            if e.prev_checksum != prev:
                return False, e.seq, f"broken link at seq {e.seq}: prev_checksum mismatch"
            recomputed = compute_checksum(
                prev, e.seq, e.tx_id, e.tweak_id, e.phase, e.at,
                _canon(e.prestate), _canon(e.poststate) if e.poststate is not None else None,
                e.error)
            if recomputed != e.checksum:
                return False, e.seq, f"row {e.seq} was modified after it was written"
            prev = e.checksum
            expected_seq += 1

        stored = self.conn.execute(
            "SELECT value FROM meta WHERE key='journal_seq'").fetchone()
        if stored and int(stored[0]) != expected_seq - 1:
            return False, expected_seq - 1, (
                f"meta says {stored[0]} entries but the chain has {expected_seq - 1} "
                f"— rows were removed from the end")
        return True, expected_seq - 1, "intact"
