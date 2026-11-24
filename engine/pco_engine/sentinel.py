"""Sentinel — the change feed.

The product's smartest feature is not a scan; it is *noticing*. This module
turns things that happened into a single readable timeline, and gives other
modules (recipes, the weekly report, blame) something to react to.

In production this is ETW + power + registry notifications, zero polling. Here
the sources are pluggable so the logic is testable: a source is any callable
returning zero or more events.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .ids import new_id

SEVERITIES = {"info", "notice", "warning", "critical"}


@dataclass
class Event:
    kind: str
    source: str
    narrative: str
    payload: dict[str, Any]
    severity: str = "info"
    tx_id: str | None = None
    id: str = ""
    at: int | None = None


class Sentinel:
    def __init__(self, conn, clock) -> None:
        self.conn = conn
        self.clock = clock
        self._sources: list[Callable[[], Iterable[Event]]] = []

    def add_source(self, fn: Callable[[], Iterable[Event]]) -> None:
        self._sources.append(fn)

    # ------------------------------------------------------------------ record
    def record(self, event: Event) -> Event:
        import json
        if event.severity not in SEVERITIES:
            raise ValueError(f"bad severity {event.severity}")
        event.id = event.id or new_id()
        event.at = event.at if event.at is not None else self.clock()
        with self.conn:
            self.conn.execute(
                "INSERT INTO events(id,at,kind,source,severity,narrative,payload_json,tx_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (event.id, event.at, event.kind, event.source, event.severity,
                 event.narrative, json.dumps(event.payload), event.tx_id))
        return event

    def poll(self) -> list[Event]:
        """Ask every source for new events. Cheap because sources are event-driven."""
        out: list[Event] = []
        for source in self._sources:
            try:
                for e in source() or []:
                    out.append(self.record(e))
            except Exception:  # a broken source must not stop the feed
                continue
        return out

    # ------------------------------------------------------------------ read
    def feed(self, limit: int = 50, kinds: set[str] | None = None) -> list[dict[str, Any]]:
        import json
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            rows = self.conn.execute(
                f"SELECT id,at,kind,source,severity,narrative,payload_json,tx_id FROM events "
                f"WHERE kind IN ({placeholders}) ORDER BY at DESC LIMIT ?",
                (*kinds, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id,at,kind,source,severity,narrative,payload_json,tx_id FROM events "
                "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "at": r[1], "kind": r[2], "source": r[3],
                 "severity": r[4], "narrative": r[5], "payload": json.loads(r[6]),
                 "tx_id": r[7]} for r in rows]

    def since(self, at_ms: int) -> list[dict[str, Any]]:
        import json
        rows = self.conn.execute(
            "SELECT id,at,kind,source,severity,narrative,payload_json,tx_id FROM events "
            "WHERE at > ? ORDER BY at", (at_ms,)).fetchall()
        return [{"id": r[0], "at": r[1], "kind": r[2], "source": r[3],
                 "severity": r[4], "narrative": r[5], "payload": json.loads(r[6]),
                 "tx_id": r[7]} for r in rows]

    def digest(self, limit: int = 5) -> str:
        """One plain-English sentence for the dashboard."""
        rows = self.feed(limit)
        if not rows:
            return "Nothing to report — the PC is quiet."
        top = rows[0]
        return top["narrative"]
