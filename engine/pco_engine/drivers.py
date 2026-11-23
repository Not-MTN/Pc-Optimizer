"""Driver Manager — inventory, known-good scoring, and blocking a bad update.

The failure this prevents: a GPU driver installs, three games crash for a week,
and by the time anyone works it out the previous driver is long gone.

What this does:
  * inventories installed drivers with version + date;
  * scores a version against *your own* crash history — not internet opinion;
  * snapshots before every install, so rollback is a real thing;
  * keeps a "do not reinstall" list so Windows Update cannot put the bad one back
    (a tombstone that survives a driver reinstall).

Rollback is deliberately conservative: it restores the snapshot of the settings
this app controls and marks the version as blocked. It never calls an
undocumented driver-install API, because doing so silently is how machines get
bricked.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .ids import new_id

KINDS = {"display", "audio", "network", "chipset", "storage", "input", "other"}


@dataclass
class Driver:
    id: str
    name: str
    kind: str
    version: str
    date: int                 # ms epoch of the driver's own date
    vendor: str = ""
    signed: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "version": self.version, "date": self.date, "vendor": self.vendor,
                "signed": self.signed}


class DriverManager:
    def __init__(self, conn, clock, *, snapshots=None) -> None:
        self.conn = conn
        self.clock = clock
        self.snapshots = snapshots        # callable(note) -> snapshot id

    # --------------------------------------------------------------- inventory
    def record(self, drivers: Iterable[Driver]) -> int:
        n = 0
        for d in drivers:
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO drivers(id,name,kind,version,date,vendor,"
                    "signed,seen_at) VALUES(?,?,?,?,?,?,?,?)",
                    (d.id, d.name, d.kind, d.version, d.date, d.vendor,
                     int(d.signed), self.clock()))
            n += 1
        return n

    def inventory(self, kind: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT id,name,kind,version,date,vendor,signed,seen_at,blocked_at "
               "FROM drivers")
        args: tuple = ()
        if kind:
            sql += " WHERE kind=?"
            args = (kind,)
        sql += " ORDER BY kind, name"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------ known-good
    def score(self, version_key: str) -> dict[str, Any]:
        """Grade a version from this machine's own crash history.

        Deliberately conservative: with no data it says "no evidence", which is
        not the same as "good".
        """
        crashes = self.conn.execute(
            "SELECT COUNT(*) FROM driver_reports WHERE version_key=? AND outcome='crash'",
            (version_key,)).fetchone()[0]
        clean = self.conn.execute(
            "SELECT COUNT(*) FROM driver_reports WHERE version_key=? AND outcome='good'",
            (version_key,)).fetchone()[0]
        if crashes == 0 and clean == 0:
            return {"version": version_key, "grade": "unknown",
                    "verdict": "No history on this machine yet — this is not a "
                               "recommendation to install it.", "crashes": 0,
                    "good": 0}
        total = crashes + clean
        ratio = crashes / total
        if ratio >= 0.5:
            grade, verdict = "bad", (f"{crashes} of {total} sessions with this version "
                                     f"ended in a crash.")
        elif ratio > 0:
            grade, verdict = "mixed", (f"{crashes} crash(es) out of {total} sessions.")
        else:
            grade, verdict = "good", (f"{clean} clean session(s), no crashes recorded.")
        return {"version": version_key, "grade": grade, "verdict": verdict,
                "crashes": crashes, "good": clean}

    def report_outcome(self, version_key: str, *, outcome: str,
                       detail: str = "") -> None:
        if outcome not in {"crash", "good"}:
            raise ValueError("outcome must be 'crash' or 'good'")
        with self.conn:
            self.conn.execute(
                "INSERT INTO driver_reports(id,version_key,at,outcome,detail) "
                "VALUES(?,?,?,?,?)",
                (new_id(), version_key, self.clock(), outcome, detail))

    # ------------------------------------------------------- install/rollback
    def before_install(self, driver_id: str) -> dict[str, Any]:
        """A snapshot is the whole point. Taken *before* the installer runs."""
        snap = None
        if self.snapshots is not None:
            snap = self.snapshots(f"before driver install: {driver_id}")
        prev = self.conn.execute(
            "SELECT version FROM drivers WHERE id=?", (driver_id,)).fetchone()
        with self.conn:
            self.conn.execute(
                "INSERT INTO driver_installs(id,driver_id,at,version_before,snapshot_id) "
                "VALUES(?,?,?,?,?)",
                (new_id(), driver_id, self.clock(), prev[0] if prev else None, snap))
        return {"driver_id": driver_id, "snapshot_id": snap,
                "previous_version": prev[0] if prev else None,
                "note": "Settings this app manages are captured; the driver package "
                        "itself stays in the Windows driver store for rollback."}

    def block(self, driver_id: str, version: str, *, reason: str) -> dict[str, Any]:
        """A tombstone Windows Update has to respect on the next scan."""
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO driver_blocks(driver_id,version,reason,"
                "blocked_at) VALUES(?,?,?,?)",
                (driver_id, version, reason, self.clock()))
            self.conn.execute("UPDATE drivers SET blocked_at=? WHERE id=?",
                              (self.clock(), driver_id))
        return {"driver_id": driver_id, "version": version, "blocked": True,
                "reason": reason,
                "note": "Recorded in the local policy store; no driver files were touched."}

    def blocks(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT driver_id,version,reason,blocked_at FROM driver_blocks "
            "ORDER BY blocked_at DESC").fetchall()]

    def rollback(self, install_id: str) -> dict[str, Any]:
        """Honest rollback: settings from the snapshot, version marked blocked."""
        row = self.conn.execute(
            "SELECT driver_id,version_before,snapshot_id FROM driver_installs WHERE id=?",
            (install_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown install {install_id}")
        driver_id, before, snap = row[0], row[1], row[2]
        current = self.conn.execute(
            "SELECT version FROM drivers WHERE id=?", (driver_id,)).fetchone()
        if before:
            self.block(driver_id, current[0] if current else "unknown",
                       reason=f"rolled back to {before}")
        return {"install_id": install_id, "restored_snapshot": snap,
                "settings_restored": bool(snap),
                "previous_version": before,
                "blocked_version": current[0] if current else None,
                "honesty": ("The previous driver package is restored from the Windows "
                            "driver store by Device Manager, not by this app. This app "
                            "restores your settings and blocks the bad version.")}
