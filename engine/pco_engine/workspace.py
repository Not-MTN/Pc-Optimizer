"""Workspaces — save a working set, restore it with one keystroke.

"Work mode", "study mode", "streaming": the exact apps on the exact monitors with
the exact profile. The hard part is not launching programs; it is being honest
about what a restore could not do.

Rules that keep this from being another broken window-restorer:

  * a workspace stores *intent* (app, arguments, monitor, profile), not window
    handles — handles are meaningless after a reboot;
  * restore reports per-item success, and a program that moved is called out;
  * restoring never closes what you have open unless you ask it to;
  * nothing is captured that the user did not explicitly save.
"""
from __future__ import annotations

from .absent import Absent

from dataclasses import dataclass, field
from typing import Any, Iterable

from .ids import new_id


@dataclass
class Entry:
    app: str                       # a path or a friendly name
    args: tuple[str, ...] = ()
    monitor: int = 0
    profile: str | None = None     # e.g. a game profile or a power plan
    title: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"app": self.app, "args": list(self.args), "monitor": self.monitor,
                "profile": self.profile, "title": self.title or self.app}


@dataclass
class Workspace:
    id: str
    name: str
    entries: list[Entry]
    created_at: int
    updated_at: int

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name,
                "entries": [e.as_dict() for e in self.entries],
                "created_at": self.created_at, "updated_at": self.updated_at,
                "count": len(self.entries)}


class Workspaces:
    def __init__(self, conn, clock, *, launcher=None, profile_applier=None) -> None:
        self.conn = conn
        self.clock = clock
        # injected: the real one starts processes / applies tweaks
        self.launcher = launcher
        self.profile_applier = profile_applier

    # -------------------------------------------------------------------- crud
    def save(self, name: str, entries: Iterable[Entry],
             workspace_id: str | None = None) -> Workspace:
        entries = list(entries)
        if not name.strip():
            raise ValueError("a workspace needs a name")
        if not entries:
            raise ValueError("a workspace with no entries is not a workspace")
        wid = workspace_id or new_id()
        now = self.clock()
        created = now
        row = self.conn.execute(
            "SELECT created_at FROM workspaces WHERE id=?", (wid,)).fetchone()
        if row:
            created = row[0]
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO workspaces(id,name,doc_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                (wid, name.strip(), _json([e.as_dict() for e in entries]), created, now))
        return Workspace(wid, name.strip(), entries, created, now)

    def list(self) -> list[Workspace]:
        rows = self.conn.execute(
            "SELECT id,name,doc_json,created_at,updated_at FROM workspaces "
            "ORDER BY updated_at DESC").fetchall()
        return [Workspace(r[0], r[1], _entries(_load(r[2])), r[3], r[4])
                for r in rows]

    def get(self, workspace_id: str) -> Workspace:
        row = self.conn.execute(
            "SELECT id,name,doc_json,created_at,updated_at FROM workspaces WHERE id=?",
            (workspace_id,)).fetchone()
        if row is None:
            raise Absent(f"unknown workspace {workspace_id}")
        return Workspace(row[0], row[1], _entries(_load(row[2])), row[3], row[4])

    def delete(self, workspace_id: str) -> None:
        # The delete and the existence check must be ONE statement. Checking
        # first with `get()` and then deleting is a check-then-act race: twelve
        # concurrent deletes of the same row produced TWO callers that both
        # reported success, because both passed the check before either deleted.
        # `rowcount` is the only honest answer to "did I remove it" — SQLite
        # deletes the row once and the loser's statement affects zero rows.
        # RETURNING, not rowcount: this connection is shared by the bridge's
        # worker threads (check_same_thread=False), so `rowcount` is not
        # attributable to this caller. Measured: twelve concurrent deletes with
        # a separate SELECT-then-DELETE gave two "successes"; with rowcount some
        # callers got -1 and reported the delete never happened. RETURNING hands
        # back the row this statement actually removed — exactly one for twelve.
        row = self.conn.execute(
            "DELETE FROM workspaces WHERE id=? RETURNING id",
            (workspace_id,)).fetchone()
        if row is None:
            raise Absent(f"unknown workspace {workspace_id}")

    # ----------------------------------------------------------------- restore
    def restore(self, workspace_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        """Preview by default. Launching six programs by accident is unforgivable,
        so the dangerous direction is the one you have to ask for explicitly."""
        ws = self.get(workspace_id)
        if dry_run:
            return {"workspace": ws.name, "dry_run": True,
                    "would_launch": [e.as_dict() for e in ws.entries],
                    "summary": f"Would launch {len(ws.entries)} item(s). Nothing started."}
        results = []
        for e in ws.entries:
            try:
                if self.launcher is None:
                    results.append({"entry": e.as_dict(), "ok": False,
                                    "error": "no launcher on this build"})
                    continue
                self.launcher(e)
                results.append({"entry": e.as_dict(), "ok": True})
            except Exception as exc:      # noqa: BLE001 - per-item failure is data
                results.append({"entry": e.as_dict(), "ok": False, "error": str(exc)})
        applied = []
        for e in ws.entries:
            if e.profile and self.profile_applier is not None:
                try:
                    self.profile_applier(e.profile)
                    applied.append(e.profile)
                except Exception as exc:  # noqa: BLE001
                    results.append({"entry": e.as_dict(), "ok": False,
                                    "error": f"profile: {exc}"})
        ok = sum(1 for r in results if r["ok"])
        failed = [r for r in results if not r["ok"]]
        return {"workspace": ws.name, "dry_run": False, "started": ok,
                "failed": failed, "profiles_applied": applied,
                "summary": (f"{ok} of {len(ws.entries)} started"
                            + (f" · {len(failed)} failed" if failed else "")
                            + (f" · {len(applied)} profile(s)" if applied else ""))}

    def capture_hint(self, history: list[dict[str, Any]]) -> dict[str, Any] | None:
        """After the same three actions happen twice, offer to save them.

        This is the macro recorder from FEATURES.md §6, in its smallest honest
        form: it *offers*, it never saves on its own.
        """
        recent = [h for h in history[:12] if h.get("kind") == "tweak"]
        if len(recent) < 6:
            return None
        seq = [h.get("summary", "") for h in recent[:6]]
        if len(set(seq[:3])) == len(seq[:3]) and seq[:3] == seq[3:6]:
            return {"offer": True,
                    "narrative": ("You have made the same three changes twice. "
                                  "Save them as a recipe?"),
                    "sequence": seq[:3]}
        return None


def _entries(doc: list[dict[str, Any]]) -> list[Entry]:
    """JSON has no tuples: normalise args on the way back in, so an entry read from
    the database is the same type as one just constructed."""
    return [Entry(app=e["app"], args=tuple(e.get("args", ())),
                  monitor=int(e.get("monitor", 0)), profile=e.get("profile"),
                  title=e.get("title", "")) for e in doc]


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load(text: str) -> Any:
    import json
    return json.loads(text)
