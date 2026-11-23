"""Installer Autopsy — every install leaves a receipt, and every line is erasable.

Most apps have an uninstaller that lies: it removes the program and leaves a
service, a scheduled task, a firewall rule, three registry keys and 2 GB of
cached installers. This module watches an install while it happens, records what
appeared, and can remove exactly that.

Critical rule: an item flagged `critical` (shared runtimes like VC++ redist,
DirectX) is *never* auto-erased. Removing it breaks other software, and that is
the failure mode that makes people distrust cleaners forever.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .ids import new_id

CLASSES = {"file", "registry", "service", "task", "startup", "firewall", "cache"}
CRITICAL_PATHS = (
    "system32", "winsxs", "windows\\system32", "microsoft\\windows\\currentversion",
    "microsoft visual c++", "directx", ".net framework", "dotnet", "redist",
)


@dataclass
class Item:
    cls: str
    locator: str
    size: int = 0
    critical: bool = False
    action: str = "pending"
    error: str | None = None
    id: str = field(default_factory=lambda: new_id())

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "class": self.cls, "locator": self.locator,
                "size": self.size, "critical": self.critical,
                "action": self.action, "error": self.error}


def is_critical(locator: str) -> bool:
    low = locator.lower()
    return any(marker in low for marker in CRITICAL_PATHS)


@dataclass
class Autopsy:
    id: str
    installer: str
    state: str
    items: list[Item]

    def as_dict(self) -> dict[str, Any]:
        by_class: dict[str, int] = {}
        for i in self.items:
            by_class[i.cls] = by_class.get(i.cls, 0) + 1
        return {
            "id": self.id, "installer": self.installer, "state": self.state,
            "counts": by_class, "total_items": len(self.items),
            "total_bytes": sum(i.size for i in self.items),
            "erasable_bytes": sum(i.size for i in self.items if not i.critical),
            "critical_items": [i.as_dict() for i in self.items if i.critical],
            "items": [i.as_dict() for i in self.items],
        }


class AutopsyLab:
    """Watcher + eraser.

    `remover` is injected so the logic is testable on any OS and so the real
    Windows implementation is the only platform-specific piece.
    """

    def __init__(self, conn, clock, *, remover=None,
                 snapshot=None) -> None:
        self.conn = conn
        self.clock = clock
        self.remover = remover or self._simulated_remove
        self.snapshot = snapshot        # callable -> snapshot id, taken before erase

    # ---------------------------------------------------------------- watching
    def begin(self, installer: str) -> str:
        aid = new_id()
        with self.conn:
            self.conn.execute(
                "INSERT INTO autopsies(id,installer,started_at,state) VALUES(?,?,?,'watching')",
                (aid, installer, self.clock()))
        return aid

    def note(self, autopsy_id: str, items: Iterable[dict[str, Any]]) -> list[Item]:
        """Record what the installer created. Called by the watcher with deltas."""
        out: list[Item] = []
        for raw in items:
            cls = raw.get("class", "file")
            if cls not in CLASSES:
                raise ValueError(f"unknown item class {cls!r}")
            locator = raw["locator"]
            item = Item(cls=cls, locator=locator, size=int(raw.get("size", 0)),
                        critical=bool(raw.get("critical", is_critical(locator))))
            out.append(item)
            with self.conn:
                self.conn.execute(
                    "INSERT INTO autopsy_items(id,autopsy_id,class,locator,size,critical,action) "
                    "VALUES(?,?,?,?,?,?, 'pending')",
                    (item.id, autopsy_id, cls, locator, item.size, int(item.critical)))
        return out

    def finish(self, autopsy_id: str) -> Autopsy:
        with self.conn:
            self.conn.execute("UPDATE autopsies SET ended_at=? WHERE id=?",
                              (self.clock(), autopsy_id))
        return self.get(autopsy_id)

    # ------------------------------------------------------------------ reading
    def get(self, autopsy_id: str) -> Autopsy:
        row = self.conn.execute(
            "SELECT id,installer,state FROM autopsies WHERE id=?", (autopsy_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown autopsy {autopsy_id}")
        rows = self.conn.execute(
            "SELECT id,class,locator,size,critical,action,error FROM autopsy_items "
            "WHERE autopsy_id=? ORDER BY class, locator", (autopsy_id,)).fetchall()
        items = [Item(cls=r["class"], locator=r["locator"], size=r["size"],
                      critical=bool(r["critical"]), action=r["action"],
                      error=r["error"], id=r["id"]) for r in rows]
        return Autopsy(id=row[0], installer=row[1], state=row[2], items=items)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,installer,started_at,ended_at,state,summary FROM autopsies "
            "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ erasing
    def erase(self, autopsy_id: str, *, include_classes: set[str] | None = None,
              keep: set[str] | None = None) -> dict[str, Any]:
        """Two-phase: stop/delete tasks and services first, then files and keys.

        Skipping an item is a normal outcome, not an error: critical items are
        reported as kept, with the reason, so the receipt is honest.
        """
        au = self.get(autopsy_id)
        keep = keep or set()
        want = include_classes or CLASSES
        order = ["service", "task", "startup", "firewall", "registry", "file", "cache"]
        snapshot_id = None
        if self.snapshot is not None:
            snapshot_id = self.snapshot()

        erased, kept, failed = 0, 0, []
        for cls in order:
            if cls not in want:
                continue
            for item in [i for i in au.items if i.cls == cls]:
                if item.id in keep or item.critical:
                    kept += 1
                    continue
                try:
                    self.remover(item)
                    erased += 1
                    with self.conn:
                        self.conn.execute(
                            "UPDATE autopsy_items SET action='erased' WHERE id=?", (item.id,))
                except Exception as exc:      # noqa: BLE001 - partial failure is data
                    failed.append({"locator": item.locator, "error": str(exc)})
                    with self.conn:
                        self.conn.execute(
                            "UPDATE autopsy_items SET action='failed', error=? WHERE id=?",
                            (str(exc), item.id))

        summary = (f"{erased} trace(s) erased · {kept} kept (critical or excluded) · "
                   f"{len(failed)} failed")
        with self.conn:
            self.conn.execute(
                "UPDATE autopsies SET state=?, summary=? WHERE id=?",
                ("erased", summary, autopsy_id))
            self.conn.execute(
                "INSERT INTO receipts(id,at,kind,title,summary,detail_json,tx_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (new_id(), self.clock(), "autopsy", f"Autopsy · {au.installer}", summary,
                 _json({"autopsy_id": autopsy_id, "erased": erased, "kept": kept,
                        "failed": failed, "snapshot_id": snapshot_id}), None))
        return {"autopsy_id": autopsy_id, "erased": erased, "kept": kept,
                "failed": failed, "summary": summary, "snapshot_id": snapshot_id}

    def keep(self, autopsy_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE autopsies SET state='kept' WHERE id=?", (autopsy_id,))

    def _simulated_remove(self, item: Item) -> None:
        """Demo backend: nothing to remove, so removal always succeeds and the
        flow can be exercised end to end on any OS."""
        return None


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
