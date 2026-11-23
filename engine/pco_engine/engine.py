"""The engine — the only thing allowed to change the machine.

Every public method maps to one thing a user can do, and every one of them
goes through: preflight → snapshot → journal(begin) → write → journal(applied)
→ verify → journal(verified). A failure at any step leaves the journal able to
describe exactly what happened, which is what makes recovery possible.

Rust port: this module is a 1:1 target. Keep the method names.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable

from .address import values_equal
from .backend import Backend, BackendError
from .catalog import Catalog
from .ids import new_id
from .journal import Journal
from .preflight import Plan, PlannedEffect, Preflight
from .snapshots import SnapshotStore
from .store import migrate


class EngineError(RuntimeError):
    pass


class PreflightBlocked(EngineError):
    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        reasons = "; ".join(i.message for i in plan.errors)
        super().__init__(f"preflight blocked the change: {reasons}")


@dataclass
class Applied:
    tx_id: str
    tweak_ids: list[str]
    effects: list[PlannedEffect]
    snapshot_id: str | None
    needs_reboot: bool
    armed_until: int | None = None

    @property
    def summary(self) -> str:
        bits = [f"{len(self.effects)} change(s)"]
        if self.snapshot_id:
            bits.append("snapshot taken")
        if self.needs_reboot:
            bits.append("needs restart")
        if self.armed_until:
            bits.append("auto-revert armed")
        return " · ".join(bits)


@dataclass
class Reverted:
    tx_id: str
    reverted: int
    failed: list[dict[str, Any]]

    @property
    def summary(self) -> str:
        if self.failed:
            return f"{self.reverted} undone, {len(self.failed)} could not be undone"
        return f"{self.reverted} change(s) undone"


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Engine:
    def __init__(self, conn: sqlite3.Connection, backend: Backend, catalog: Catalog,
                 *, clock: Callable[[], int] | None = None,
                 confirm_window_ms: int = 60_000) -> None:
        self.conn = conn
        self.backend = backend
        self.catalog = catalog
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.confirm_window_ms = confirm_window_ms
        self.journal = Journal(conn, self.clock)
        self.snapshots = SnapshotStore(conn, backend, self.clock)
        self.preflight = Preflight(catalog, backend)
        migrate(conn)

    # =================================================================== apply
    def apply(self, tweak_ids: list[str] | str, *, source: str = "ui",
              kind: str = "single", note: str | None = None,
              require_confirm: bool | None = None,
              snapshot: bool = True) -> Applied:
        ids = [tweak_ids] if isinstance(tweak_ids, str) else list(tweak_ids)
        if not ids:
            raise EngineError("nothing to apply")

        plan = self.preflight.simulate(ids)
        if not plan.ok:
            raise PreflightBlocked(plan)

        # Skip effects that are already correct — a no-op write is still a change
        # to the machine and should not appear in a receipt.
        effective = [e for e in plan.effects if not values_equal(e.old, e.new)]
        if not effective:
            raise EngineError("nothing to do: every value already matches")

        ordered = self._order(effective)
        tx_id = new_id()
        started = self.clock()

        snap_id = None
        if snapshot and (plan.requires_confirmation or len(ordered) > 1):
            snap = self.snapshots.capture([e.address for e in ordered],
                                          kind="pre_risky" if plan.requires_confirmation else "auto",
                                          note=f"before {', '.join(ids)}")
            snap_id = snap.id

        self.journal.ensure_transaction(tx_id, kind=kind, source=source, note=note)

        for eff in ordered:
            pre = {eff.address: eff.old}
            # 1. intent is durable BEFORE the machine is touched
            self.journal.append(tx_id=tx_id, tweak_id=eff.tweak_id, phase="begin",
                                prestate=pre, poststate={eff.address: eff.new})
            try:
                self.backend.write(eff.address, eff.new)
            except BackendError as exc:
                self.journal.append(tx_id=tx_id, tweak_id=eff.tweak_id, phase="failed",
                                    prestate=pre, poststate={eff.address: eff.new},
                                    error=str(exc))
                self._close_tx(tx_id, "failed")
                # roll back whatever already landed in this transaction
                self._rollback(tx_id, [eff.address])
                raise EngineError(f"{eff.tweak_id} failed: {exc}") from exc

            self.journal.append(tx_id=tx_id, tweak_id=eff.tweak_id, phase="applied",
                                prestate=pre, poststate={eff.address: eff.new})

            actual = self._read(eff.address)
            if not values_equal(actual, eff.new):
                self.journal.append(tx_id=tx_id, tweak_id=eff.tweak_id, phase="failed",
                                    prestate=pre, poststate={eff.address: actual},
                                    error="value did not stick (policy override?)")
                continue
            self.journal.append(tx_id=tx_id, tweak_id=eff.tweak_id, phase="verified",
                                prestate=pre, poststate={eff.address: actual})

            with self.conn:
                self.conn.execute(
                    "INSERT INTO managed(address,expected_json,applied_by,updated_at) "
                    "VALUES(?,?,?,?) ON CONFLICT(address) DO UPDATE SET "
                    "expected_json=excluded.expected_json, applied_by=excluded.applied_by, "
                    "updated_at=excluded.updated_at",
                    (eff.address, _canon(eff.new), tx_id, self.clock()))

        armed_until = None
        if require_confirm:
            armed_until = self.clock() + self.confirm_window_ms
            with self.conn:
                self.conn.execute("UPDATE transactions SET state='armed', armed_until=? "
                                  "WHERE id=?", (armed_until, tx_id))
        else:
            self._close_tx(tx_id, "committed")

        return Applied(tx_id=tx_id, tweak_ids=ids, effects=ordered,
                       snapshot_id=snap_id, needs_reboot=plan.needs_reboot,
                       armed_until=armed_until)

    # ================================================================= confirm
    def confirm(self, tx_id: str) -> None:
        """User said 'keep it'. Disarms auto-revert."""
        row = self.conn.execute("SELECT state FROM transactions WHERE id=?", (tx_id,)).fetchone()
        if row is None:
            raise EngineError(f"unknown transaction {tx_id}")
        if row[0] != "armed":
            return
        for tweak_id in {e.tweak_id for e in self.journal.entries(tx_id)}:
            self.journal.append(tx_id=tx_id, tweak_id=tweak_id, phase="confirmed",
                                prestate={}, poststate=None)
        self._close_tx(tx_id, "committed")

    # ================================================================== revert
    def revert(self, tx_id: str, *, reason: str = "user") -> Reverted:
        rows = self.journal.entries(tx_id)
        if not rows:
            raise EngineError(f"nothing journaled for {tx_id}")

        # newest-first: undo in the opposite order to which it was applied
        undone = 0
        failed: list[dict[str, Any]] = []
        for entry in reversed(rows):
            if entry.phase not in {"begin", "applied", "verified"}:
                continue
            for address, old in entry.prestate.items():
                try:
                    current = self._read(address)
                    if old is None:
                        if current is not None:
                            self._remove(address)
                            undone += 1
                    else:
                        if not values_equal(current, old):
                            self.backend.write(address, old)
                            undone += 1
                    with self.conn:
                        self.conn.execute("DELETE FROM managed WHERE address=?", (address,))
                except BackendError as exc:
                    failed.append({"address": address, "error": str(exc)})
                    self.journal.append(
                        tx_id=tx_id, tweak_id=entry.tweak_id, phase="failed",
                        prestate={address: old}, poststate=None,
                        error=f"revert failed: {exc}")

            self.journal.append(tx_id=tx_id, tweak_id=entry.tweak_id, phase="reverted",
                                prestate=entry.prestate, poststate=None)

        state = "reverted" if not failed else "failed"
        self._close_tx(tx_id, state, note=f"reverted ({reason})")
        return Reverted(tx_id=tx_id, reverted=undone, failed=failed)

    def _rollback(self, tx_id: str, skip: list[str]) -> None:
        """Internal safety net used when a write throws mid-batch."""
        skip_set = set(skip)
        for entry in reversed(self.journal.entries(tx_id)):
            if entry.phase not in {"applied", "verified"}:
                continue
            for address, old in entry.prestate.items():
                if address in skip_set:
                    continue
                try:
                    self.backend.write(address, old)
                    with self.conn:
                        self.conn.execute("DELETE FROM managed WHERE address=?", (address,))
                except BackendError:
                    pass  # recovery on next boot will retry from the journal

    def _remove(self, address: str) -> None:
        remove = getattr(self.backend, "remove", None)
        if remove is None:
            raise BackendError(f"backend cannot remove {address}")
        remove(address)

    # ======================================================== recovery & drift
    def recover(self) -> list[dict[str, Any]]:
        """Repair after a crash / power loss. Called at startup, before anything else.

        Any `begin` without a terminal entry means we cannot prove what the
        machine looks like — so the previous value is written back and the
        transaction is closed as reverted.
        """
        fixed: list[dict[str, Any]] = []
        for entry in self.journal.interrupted():
            for address, old in entry.prestate.items():
                try:
                    current = self._read(address)
                    if old is None:
                        if current is not None:
                            self._remove(address)
                    elif not values_equal(current, old):
                        self.backend.write(address, old)
                    fixed.append({"tx_id": entry.tx_id, "address": address,
                                  "action": "restored pre-write value"})
                except BackendError as exc:
                    fixed.append({"tx_id": entry.tx_id, "address": address,
                                  "action": f"could not restore: {exc}"})
            self.journal.append(tx_id=entry.tx_id, tweak_id=entry.tweak_id, phase="reverted",
                                prestate=entry.prestate, poststate=None,
                                error="interrupted transaction recovered on startup")
            self._close_tx(entry.tx_id, "reverted", note="recovered after interruption")
        return fixed

    def check_drift(self, *, actor_guess: str | None = None) -> list[dict[str, Any]]:
        """Compare managed state with reality. Windows Update and vendors will
        silently undo things; this is how we notice instead of guessing."""
        found: list[dict[str, Any]] = []
        rows = self.conn.execute(
            "SELECT address,expected_json,applied_by FROM managed").fetchall()
        for address, expected_json, applied_by in rows:
            expected = json.loads(expected_json)
            actual = self._read(address)
            if values_equal(actual, expected):
                continue
            found.append({"address": address, "expected": expected,
                          "actual": actual, "applied_by": applied_by})
            with self.conn:
                self.conn.execute(
                    "INSERT INTO drift(address,expected_json,actual_json,detected_at,actor_guess) "
                    "VALUES(?,?,?,?,?)",
                    (address, expected_json, _canon(actual), self.clock(), actor_guess))
                self.conn.execute("DELETE FROM managed WHERE address=?", (address,))
        return found

    # ================================================================ queries
    def managed(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT address,expected_json FROM managed").fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    def transaction(self, tx_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT id,kind,started_at,finished_at,state,source,note,armed_until "
            "FROM transactions WHERE id=?", (tx_id,)).fetchone()
        if row is None:
            raise EngineError(f"unknown transaction {tx_id}")
        return dict(row)

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,kind,started_at,finished_at,state,source,note FROM transactions "
            "ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def expire_armed(self) -> list[str]:
        """Auto-revert transactions whose confirmation window elapsed."""
        now = self.clock()
        rows = self.conn.execute(
            "SELECT id FROM transactions WHERE state='armed' AND armed_until IS NOT NULL "
            "AND armed_until <= ?", (now,)).fetchall()
        expired = []
        for (tx_id,) in rows:
            self.revert(tx_id, reason="confirmation window expired")
            expired.append(tx_id)
        return expired

    # ================================================================= helpers
    def _read(self, address: str) -> Any:
        """Read the *effective* value. `settled_read` lets a backend model
        policy overrides (e.g. group policy re-imposing a value)."""
        settled = getattr(self.backend, "settled_read", None)
        try:
            if settled is not None:
                return settled(address)
            return self.backend.read(address)
        except BackendError:
            return None

    def _close_tx(self, tx_id: str, state: str, note: str | None = None) -> None:
        with self.conn:
            if note:
                self.conn.execute(
                    "UPDATE transactions SET state=?, finished_at=?, note=? WHERE id=?",
                    (state, self.clock(), note, tx_id))
            else:
                self.conn.execute(
                    "UPDATE transactions SET state=?, finished_at=? WHERE id=?",
                    (state, self.clock(), tx_id))

    def _order(self, effects: list[PlannedEffect]) -> list[PlannedEffect]:
        order = {
            "service_start_type": 0, "scheduled_task_state": 1, "registry_value": 2,
            "registry_tree": 3, "startup_defer": 4, "display_mode": 5,
            "display_setting": 6, "power_setting": 7, "driver_action": 8,
            "dns_policy": 9, "app_setting": 10, "file_write": 11, "file_delete": 12,
        }
        return sorted(effects, key=lambda e: (order.get(e.kind, 99), e.address))

    # ---------------------------------------------------------------- factory
    @classmethod
    def open(cls, db_path: str, backend: Backend, catalog: Catalog,
             **kwargs: Any) -> "Engine":
        from .store import connect
        conn = connect(db_path)
        return cls(conn, backend, catalog, **kwargs)
