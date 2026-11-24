"""Command palette — one box, engine-native verbs, no console flash.

Design rules from FEATURES.md §6, kept literally:

  * built-ins execute inside the engine (no subprocess, no window, ~ms);
  * a user command declares its executor and a timeout, and imported commands
    show a permission diff before their first run;
  * the palette opens in ≤ 50 ms because it is a local array filter, and the
    result cap keeps it there.

Fuzzy matching is deliberate rather than clever: subsequence match on the
command name, then a bonus for a prefix match and for word-boundary starts. That
is fast enough to run on every keystroke with a few thousand commands and it
behaves predictably, which matters more than being clever.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .ids import new_id

EXECUTORS = {"engine", "shell", "app", "web", "script"}


@dataclass
class Command:
    id: str
    title: str
    verb: str                     # what the user types after ':'
    group: str = "General"
    executor: str = "engine"
    payload: dict[str, Any] = field(default_factory=dict)
    keywords: tuple[str, ...] = ()
    dangerous: bool = False
    timeout_s: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "verb": self.verb,
                "group": self.group, "executor": self.executor,
                "dangerous": self.dangerous, "timeout_s": self.timeout_s,
                "payload": self.payload}


def score(query: str, text: str) -> int:
    """Subsequence score: 0 means no match. Prefix and word starts rank higher."""
    q, t = query.lower().strip(), text.lower()
    if not q:
        return 1
    if t.startswith(q):
        return 1000 - len(t)
    words = re.split(r"[^a-z0-9]+", t)
    if any(w.startswith(q) for w in words if w):
        return 700 - len(t)
    i = 0
    hits, gaps = 0, 0
    last = -1
    for ch in q:
        j = t.find(ch, i)
        if j == -1:
            return 0
        hits += 1
        if last != -1:
            gaps += j - last - 1
        last = j
        i = j + 1
    return max(1, 400 - gaps - len(t) // 2) if hits == len(q) else 0


class Palette:
    def __init__(self, conn, clock, *, run_action: Callable[[str, dict], Any] | None = None,
                 allow_shell: bool = False) -> None:
        self.conn = conn
        self.clock = clock
        self.run_action = run_action
        self.allow_shell = allow_shell      # deliberate default: engine verbs only
        self._builtins: list[Command] = _builtins()

    # ------------------------------------------------------------------ catalog
    def commands(self) -> list[Command]:
        rows = self.conn.execute(
            'SELECT id,title,verb,"group",executor,payload_json,dangerous,timeout_s '
            'FROM palette_commands ORDER BY verb').fetchall()
        user = [Command(id=r[0], title=r[1], verb=r[2], group=r[3], executor=r[4],
                        payload=_load(r[5]), dangerous=bool(r[6]), timeout_s=r[7])
                for r in rows]
        return self._builtins + user

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        ranked = []
        for cmd in self.commands():
            s = max(score(query, cmd.verb), score(query, cmd.title) - 50)
            if s <= 0:
                continue
            s += sum(10 for k in cmd.keywords if query.lower() in k.lower())
            ranked.append((s, cmd))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].verb))
        return [c.as_dict() for _, c in ranked[:limit]]

    # ------------------------------------------------------------------ execute
    def execute(self, verb_or_id: str, args: dict[str, Any] | None = None
                ) -> dict[str, Any]:
        args = args or {}
        cmd = next((c for c in self.commands()
                    if c.id == verb_or_id or c.verb == verb_or_id), None)
        if cmd is None:
            # Not Absent: the command was never a thing, so this is a bad
            # request (400), not a missing object (404). KeyError here used to
            # inherit the "missing field" wrapper and tell the user to add a
            # field named "unknown command 'shell-anything'".
            raise ValueError(f"unknown command {verb_or_id!r}")
        if cmd.executor != "engine" and not self.allow_shell:
            raise PermissionError(
                f"'{cmd.verb}' needs the {cmd.executor} executor, which is disabled. "
                "Shell/script executors are an explicit, per-command opt-in.")
        started = self.clock()
        result = self._run(cmd, args)
        elapsed = self.clock() - started
        with self.conn:
            self.conn.execute(
                "INSERT INTO receipts(id,at,kind,title,summary,detail_json,tx_id) "
                "VALUES(?,?,?,?,?,?,NULL)",
                (new_id(), started, "command", f"Command · {cmd.verb}",
                 f"ran in {elapsed} ms",
                 _json({"command": cmd.verb, "args": args, "elapsed_ms": elapsed,
                        "result": _safe(result)})))
        return {"command": cmd.verb, "elapsed_ms": elapsed, "result": result}

    def _run(self, cmd: Command, args: dict[str, Any]) -> Any:
        if cmd.payload.get("action") and self.run_action is not None:
            return self.run_action(cmd.payload["action"], {**cmd.payload.get("args", {}),
                                                           **args})
        return cmd.payload.get("builtin", "ok")

    # ------------------------------------------------------------ user commands
    def add(self, cmd: Command) -> str:
        if cmd.executor not in EXECUTORS:
            raise ValueError(f"bad executor {cmd.executor!r}")
        if not cmd.verb or " " in cmd.verb:
            raise ValueError("verb must be a single word")
        with self.conn:
            self.conn.execute(
                'INSERT OR REPLACE INTO palette_commands(id,title,verb,"group",executor,'
                'payload_json,dangerous,timeout_s,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (cmd.id, cmd.title, cmd.verb, cmd.group, cmd.executor,
                 _json(cmd.payload), int(cmd.dangerous), cmd.timeout_s, self.clock()))
        return cmd.id

    def permission_diff(self, cmd: Command) -> dict[str, Any]:
        """What this command would be allowed to do that the app itself cannot."""
        base = {"reads": ["your PC's configuration"],
                "writes": ["journaled, reversible settings"]}
        if cmd.executor == "engine":
            extra: list[str] = []
        elif cmd.executor == "shell":
            extra = ["run any program with your user rights"]
        elif cmd.executor == "script":
            extra = ["run a script with your user rights", "filesystem access"]
        elif cmd.executor == "web":
            extra = ["open a URL in your browser"]
        else:
            extra = ["launch an application"]
        return {"command": cmd.verb, "base": base, "extra": extra,
                "needs_confirm": bool(extra)}


def _builtins() -> list[Command]:
    def c(verb, title, group, action=None, args=None, keywords=(), dangerous=False):
        return Command(id=f"builtin:{verb}", title=title, verb=verb, group=group,
                       executor="engine",
                       payload={"action": action, "args": args or {}},
                       keywords=keywords, dangerous=dangerous)

    return [
        c("clean", "Scan and clean safe junk", "Cleaner", "clean.run",
          keywords=("junk", "temp", "space")),
        c("check", "Run a full PC checkup", "Checkup", "checkup.run",
          keywords=("health", "diagnose", "scan")),
        c("fix", "Fix everything the consent level allows", "Checkup", "checkup.fix",
          keywords=("repair", "apply")),
        c("snap", "Take a snapshot", "Safety", "snapshot.take",
          args={"kind": "manual"}, keywords=("backup", "restore point")),
        c("undo", "Undo the last change", "Safety", None, keywords=("revert", "back")),
        c("bench", "Run the benchmark", "Performance", "bench.run",
          keywords=("score", "speed")),
        c("dns on", "Turn the ad blocker on", "Blocking", "dns.on",
          keywords=("ads", "tracker", "block")),
        c("dns off", "Turn the ad blocker off", "Blocking", "dns.off",
          keywords=("ads", "pause")),
        c("verify", "Verify the safety journal", "Safety", "verify",
          keywords=("integrity", "tamper")),
        c("drift", "Check for changes Windows reverted", "Safety", "drift.check",
          keywords=("reverted", "update")),
        c("report", "Build the weekly report", "Overview", "report.weekly",
          keywords=("summary", "week")),
        c("status", "Show engine status", "Overview", "status", keywords=("info",)),
        Command(id="builtin:help", title="Show every command", verb="help",
                group="General", executor="engine", payload={"builtin": "help"},
                keywords=("commands", "?")),
    ]


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load(text: str) -> Any:
    import json
    return json.loads(text or "{}")


def _safe(value: Any) -> Any:
    """Receipts must stay small: keep primitives, summarise the rest."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in list(value.items())[:12]}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in list(value)[:12]]
    return str(value)[:200]
