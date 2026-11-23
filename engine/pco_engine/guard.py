"""Panic key and boot guard — the two promises that must never depend on the UI.

Panic (`Ctrl+Alt+P`) must work when the app's window is not focused, is
minimised, or is still starting. That means the hotkey lives in a global-hook
process and calls the same `Engine.revert` path as everything else. This module
is the decision logic + the audit trail; the hook itself is Windows-specific.

Boot guard is the ordering rule after a crash: nothing else in the app is
allowed to touch the machine until `Engine.recover()` has finished and the
journal chain has been verified. If recovery itself fails, the app enters
*read-only* mode instead of guessing — a machine in an unknown state is exactly
when a "helpful" optimiser does the most damage.

Both paths write a `boot_events` row, so "why did my PC change on Tuesday" has
an answer even when the change happened before login completed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ids import new_id
from .sentinel import Event

PANIC_HOTKEY = "Ctrl+Alt+P"
MAX_MODE = "read_only"          # what the app becomes if recovery cannot be trusted
NORMAL_MODE = "normal"


@dataclass
class BootEvent:
    kind: str                   # normal|recovered|unsafe|panic
    detail: str
    tx_ids: list[str]
    at: int

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail,
                "tx_ids": self.tx_ids, "at": self.at}


class Guard:
    def __init__(self, service: Any) -> None:
        self.svc = service
        self.mode = NORMAL_MODE

    # ------------------------------------------------------------------- boot
    def boot(self) -> dict[str, Any]:
        """Run before anything else. Returns the mode the app is allowed to use.

        Order is not negotiable:
          1. verify the chain — if it is broken we must not "repair" anything;
          2. recover interrupted transactions;
          3. verify again, because recovery appends rows.
        """
        ok0, rows0, detail0 = self.svc.engine.journal.verify_chain()
        if not ok0:
            self.mode = MAX_MODE
            ev = self._record(BootEvent("unsafe", f"journal chain broken: {detail0}",
                                        [], self.svc.clock()))
            return {"mode": self.mode, "journal_ok": False, "recovered": 0,
                    "reason": ("The safety journal failed verification. The app will "
                               "not change this machine until it is repaired."),
                    "event": ev.as_dict()}

        recovered = self.svc.engine.recover()
        ok1, rows1, detail1 = self.svc.engine.journal.verify_chain()
        if not ok1:
            self.mode = MAX_MODE
            ev = self._record(BootEvent("unsafe", f"chain broken after recovery: {detail1}",
                                        [r.get("tx_id", "") for r in recovered],
                                        self.svc.clock()))
            return {"mode": self.mode, "journal_ok": False,
                    "recovered": len(recovered), "reason": ev.detail,
                    "event": ev.as_dict()}

        self.mode = NORMAL_MODE
        kind = "recovered" if recovered else "normal"
        detail = (f"recovered {len(recovered)} interrupted change(s)" if recovered
                  else "clean boot")
        ev = self._record(BootEvent(kind, detail,
                                    [r.get("tx_id", "") for r in recovered],
                                    self.svc.clock()))
        return {"mode": self.mode, "journal_ok": True, "recovered": len(recovered),
                "journal_rows": rows1, "event": ev.as_dict()}

    # ------------------------------------------------------------------- panic
    def panic(self, *, reason: str = "user pressed " + PANIC_HOTKEY) -> dict[str, Any]:
        """Undo everything this app did, newest first, and stop automation.

        Deliberately blunt: a panic key that asks "are you sure" is not a panic
        key. It reverts every transaction that is still in effect, disables
        recipes, and turns the blocker off — then reports what it did.
        """
        txs = [r for r in self.svc.engine.history(200)
               if r["state"] in {"committed", "armed", "open"}]
        reverted, failed = [], []
        for tx in txs:
            try:
                res = self.svc.engine.revert(tx["id"], reason=reason)
                reverted.append({"tx_id": tx["id"], "changed": res.reverted})
            except Exception as exc:          # noqa: BLE001
                failed.append({"tx_id": tx["id"], "error": str(exc)})

        turned_off = []
        for rec in self.svc.recipes_list():
            if rec["enabled"]:
                self.svc.recipes.set_enabled(rec["id"], False)
                turned_off.append(rec["id"])
        dns_mode = self.svc.dns.off()

        ev = self._record(BootEvent(
            "panic",
            f"panic: reverted {len(reverted)} transaction(s), "
            f"disabled {len(turned_off)} recipe(s), blocker {dns_mode}",
            [r["tx_id"] for r in reverted], self.svc.clock()))
        self.svc.sentinel.record(Event(
            kind="panic", source="guard", severity="critical",
            narrative=ev.detail, payload={"failed": failed}))
        summary = (f"Reverted {len(reverted)} change(s), disabled "
                   f"{len(turned_off)} recipe(s), blocker {dns_mode}.")
        if failed:
            summary += f" {len(failed)} could not be reverted and are listed."
        return {"reverted": reverted, "failed": failed,
                "recipes_disabled": turned_off, "dns_mode": dns_mode,
                "summary": summary, "event": ev.as_dict()}

    # ------------------------------------------------------------------- audit
    def events(self, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(r) for r in self.svc.conn.execute(
            "SELECT id,at,boot_guard_action,unexpected_shutdown,healthy_at_ms "
            "FROM boot_events ORDER BY at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()]

    def safe_to_write(self) -> bool:
        return self.mode == NORMAL_MODE

    def _record(self, ev: BootEvent) -> BootEvent:
        """Written into the schema-1 boot_events table: kind goes in
        boot_guard_action, and 'recovered' implies an unexpected shutdown."""
        with self.svc.conn:
            self.svc.conn.execute(
                "INSERT INTO boot_events(id,at,healthy_at_ms,boot_guard_action,"
                "unexpected_shutdown) VALUES(?,?,?,?,?)",
                (new_id(), ev.at, ev.at if ev.kind in {"normal", "recovered"} else None,
                 f"{ev.kind}: {ev.detail}", int(ev.kind != "normal")))
        return ev
