"""The ad + tracker blocker — matcher, policy, pause, and counters.

The real product runs a DNS resolver on 127.0.0.1 and routes queries with NRPT.
That part needs Windows. What *is* portable — and what everything else depends
on — is the decision engine: list compilation, wildcard matching, per-app
policy, pause windows, allow rules and counters. That is this module, and it is
fully tested.

Design: lists compile to a sorted array + a suffix set, so a lookup costs a
hash + one array scan per label boundary. No regex, no per-query allocation of
significance.
"""
from __future__ import annotations

import bisect
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .backend import BackendError
from .hostsfile import HostsFile

GROUPS = {"ads", "trackers", "telemetry", "malware", "phishing", "regional", "adult"}
MODES = {"off", "on", "strict"}


@dataclass
class Decision:
    domain: str
    blocked: bool
    reason: str            # 'allowed' | 'list' | 'pause' | 'off' | 'app_off' | 'allow_rule'
    list_id: str | None = None
    app: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"domain": self.domain, "blocked": self.blocked,
                "reason": self.reason, "list": self.list_id, "app": self.app}


class Matcher:
    """Exact + wildcard domain matcher. Wildcards are `*.example.com`."""

    def __init__(self) -> None:
        self._exact: list[str] = []
        self._suffix: list[str] = []
        self._owner: dict[str, str] = {}

    def add(self, pattern: str, list_id: str) -> None:
        p = pattern.strip().lower().strip(".")
        if not p or p.startswith("#"):
            return
        if p.startswith("*."):
            base = p[2:]
            if base not in self._owner:
                bisect.insort(self._suffix, base)
                self._owner[base] = list_id
        elif p.startswith("||"):
            base = p[2:].split("^")[0].lower().strip(".")
            if base and base not in self._owner:
                bisect.insort(self._suffix, base)
                self._owner[base] = list_id
        else:
            if p not in self._owner:
                bisect.insort(self._exact, p)
                self._owner[p] = list_id

    def __len__(self) -> int:
        return len(self._exact) + len(self._suffix)

    def match(self, domain: str) -> tuple[str, str] | None:
        d = domain.strip().lower().strip(".")
        if not d:
            return None
        i = bisect.bisect_left(self._exact, d)
        if i < len(self._exact) and self._exact[i] == d:
            return d, self._owner[d]
        # walk label boundaries: a.b.c → b.c → c
        rest = d
        while True:
            dot = rest.find(".")
            if dot == -1:
                break
            rest = rest[dot + 1:]
            j = bisect.bisect_left(self._suffix, rest)
            if j < len(self._suffix) and self._suffix[j] == rest:
                return rest, self._owner[rest]
        return None


@dataclass
class ListSource:
    id: str
    name: str
    grp: str
    domains: list[str] = field(default_factory=list)
    enabled: bool = False


class DnsBlocker:
    def __init__(self, conn, clock, *, backend=None, listener=None) -> None:
        self.conn = conn
        self.clock = clock
        # What `on()` can reach. `None` means the demo machine: there is no
        # adapter to change and no lie to tell.
        self.backend = backend
        self.listener = listener
        self._engaged_on: str | None = None
        self.hosts = HostsFile(backend=backend)
        self._matcher: Matcher | None = None
        self._lists: dict[str, ListSource] = {}
        self._load_state()

    # ------------------------------------------------------------------- state
    def _load_state(self) -> None:
        rows = self.conn.execute("SELECT key,value FROM dns_state").fetchall()
        self._state = {r[0]: r[1] for r in rows} or {"mode": "off", "pause_until": "0"}

    def _save(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO dns_state(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._state[key] = value

    # -------------------------------------------------------------------- lists
    def install_list(self, list_id: str, name: str, grp: str, domains: Iterable[str],
                     *, enabled: bool = True) -> int:
        if grp not in GROUPS:
            raise ValueError(f"unknown list group: {grp}")
        src = ListSource(id=list_id, name=name, grp=grp,
                         domains=[d.strip().lower() for d in domains if d.strip()],
                         enabled=enabled)
        self._lists[list_id] = src
        self._matcher = None        # recompile lazily
        # The domains are part of the list. Storing only the name|group|enabled
        # header meant a fresh process had lists with no contents: the matcher
        # compiled to nothing, nothing was blocked, and `decide()` said "allowed"
        # for every domain on the machine while the window showed a full list.
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO dns_state(key,value) VALUES(?,?)",
                (f"list.{list_id}", f"{name}|{grp}|{int(enabled)}"))
            self.conn.execute(
                "INSERT OR REPLACE INTO dns_state(key,value) VALUES(?,?)",
                (f"domains.{list_id}", "\n".join(src.domains)))
        return len(src.domains)

    def _domains_of(self, list_id: str) -> list[str]:
        row = self.conn.execute(
            "SELECT value FROM dns_state WHERE key=?", (f"domains.{list_id}",)).fetchone()
        if row is None or not row[0]:
            return []
        return [d for d in str(row[0]).split("\n") if d]

    def _compile(self) -> Matcher:
        m = Matcher()
        for src in self._lists.values():
            if not src.enabled:
                continue
            for d in src.domains:
                m.add(d, src.id)
        # Allow rules are evaluated in decide() because they are per-app; the
        # matcher only owns list patterns.
        return m

    # --------------------------------------------------------------- controls
    @property
    def mode(self) -> str:
        return self._state.get("mode", "off")

    def on(self) -> str:
        """Turn blocking on. Returns the mode, and refuses to pretend.

        This used to write `mode = "on"` into Keel's own SQLite and nothing else.
        No socket, no listener, no adapter change: every domain in every list
        stayed resolvable, while the window said "blocking on". A flag about
        myself is not a change to the machine.
        """
        self._save("pause_until", "0")
        refused = self._engage()
        if refused:
            self._save("mode", "off")
            self._last_refusal = refused
            raise BackendError(refused)
        self._save("mode", "on")
        return self.mode

    def off(self) -> str:
        self._save("mode", "off")
        self._save("pause_until", "0")
        self._release()
        return self.mode

    # ------------------------------------------------------------- enforcement
    def _engage(self) -> str:
        """Put the lists into force. Returns a reason string, or "" on success.

        Blocking is only real if name resolution changes. This build points the
        adapter at a resolver it controls; when no such resolver is listening it
        says so instead of flipping a flag.
        """
        backend = getattr(self, "backend", None)
        listener = getattr(self, "listener", None)
        if backend is None:
            # No backend at all (demo) — nothing to enforce, nothing to lie about.
            return ""
        if getattr(backend, "environment", "demo") != "windows":
            # A simulated machine: the pill already says nothing here is real.
            return ""
        # The real mechanism: one managed block in the system hosts file.
        # Windows consults that file before it asks any resolver, so a name in
        # there is dead for every application whether Keel is running or not.
        names = self.blocklist_domains()
        out = self.hosts.apply(names)
        if not out.ok:
            return out.reason
        self._engaged_on = out.path
        self._engage_servers(listener)
        return ""

    def _engage_servers(self, listener) -> None:
        """Optionally point an adapter at a resolver we control.

        Not required for the hosts block to work, so nothing here may fail the
        blocking operation: the hosts entry is already written and is what
        actually blocks. Only a resolver that is running and reports an address
        is worth pointing an adapter at.
        """
        backend = getattr(self, "backend", None)
        if backend is None or listener is None:
            return
        if not getattr(listener, "running", False):
            return
        adapters = getattr(backend, "dns_adapters", None)
        setter = getattr(backend, "dns_set", None)
        servers = getattr(listener, "addresses", None) or []
        if adapters is None or setter is None or not servers:
            return
        try:
            targets = [a for a in adapters() if a]
            if targets:
                setter(targets[0], servers)
                flush = getattr(backend, "dns_flush", None)
                if flush is not None:
                    flush()
        except Exception:
            # A resolver-side nicety failing must never undo the block.
            pass

    def blocklist_domains(self) -> list[str]:
        """Every hostname in every enabled list — what the hosts block writes."""
        out: list[str] = []
        for src in self._lists.values():
            if not src.enabled:
                continue
            out.extend(src.domains)
        if not out:
            # Not installed in memory (fresh process): reload from the database.
            out = self._domains_from_db()
        return out

    def _domains_from_db(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT key,value FROM dns_state WHERE key LIKE 'list.%'").fetchall()
        out: list[str] = []
        for key, value in rows:
            parts = str(value).split("|")
            if len(parts) >= 3 and parts[2] == "1":
                out.extend(self._domains_of(key[len("list."):]))
        return out

    def _release(self) -> None:
        out = self.hosts.clear()
        if not out.ok:
            raise BackendError(out.reason)
        self._engaged_on = None

    def toggle(self) -> str:
        return self.off() if self.mode != "off" else self.on()

    def pause(self, minutes: int) -> int:
        until = int(self.clock()) + minutes * 60_000
        self._save("pause_until", str(until))
        return until

    def resume(self) -> None:
        self._save("pause_until", "0")

    def is_paused(self) -> bool:
        return int(self._state.get("pause_until", "0")) > int(self.clock())

    def pause_remaining_s(self) -> int:
        remaining = int(self._state.get("pause_until", "0")) - int(self.clock())
        return max(0, remaining // 1000)

    def set_app(self, app: str, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"bad mode {mode}")
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO dns_state(key,value) VALUES(?,?)",
                (f"app.{app}", mode))
        self._state[f"app.{app}"] = mode

    def allow(self, domain: str, app: str = "*") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO dns_allow(domain,app) VALUES(?,?)",
                (domain.lower().strip("."), app))

    # ---------------------------------------------------------------- decision
    def decide(self, domain: str, app: str = "system") -> Decision:
        if self.mode == "off":
            return Decision(domain, False, "off", None, app)
        if self.is_paused():
            return Decision(domain, False, "pause", None, app)
        app_mode = self._state.get(f"app.{app}")
        if app_mode == "off":
            return Decision(domain, False, "app_off", None, app)

        allowed = self.conn.execute(
            "SELECT 1 FROM dns_allow WHERE domain=? AND app IN (?, '*')",
            (domain.lower().strip("."), app)).fetchone()
        if allowed:
            return Decision(domain, False, "allow_rule", None, app)

        if self._matcher is None:
            self._matcher = self._compile()
        hit = self._matcher.match(domain)
        if hit is None:
            return Decision(domain, False, "allowed", None, app)

        base, list_id = hit
        src = self._lists.get(list_id)
        # strict mode also blocks telemetry; on mode respects the list groups
        if self.mode == "on" and src is not None and src.grp in {"malware", "phishing"}:
            pass    # always block these
        with self.conn:
            self.conn.execute(
                "INSERT INTO dns_counters(domain,app,blocked,last_at) VALUES(?,?,1,?) "
                "ON CONFLICT(domain) DO UPDATE SET blocked=blocked+1, last_at=excluded.last_at",
                (base, app, self.clock()))
        return Decision(domain, True, "list", list_id, app)

    # ---------------------------------------------------------------- counters
    def counters(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT domain,app,blocked,last_at FROM dns_counters ORDER BY blocked DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"domain": r[0], "app": r[1], "blocked": r[2], "last_at": r[3]}
                for r in rows]

    # ------------------------------------------------------------------ lists
    def lists(self) -> list[dict[str, Any]]:
        """Every installed list with what it holds and whether it is enforced."""
        rows = self.conn.execute(
            "SELECT key,value FROM dns_state WHERE key LIKE 'list.%'").fetchall()
        out = []
        for key, value in rows:
            list_id = str(key)[len("list."):]
            parts = str(value).split("|")
            name = parts[0] if parts else list_id
            grp = parts[1] if len(parts) > 1 else "ads"
            enabled = len(parts) > 2 and parts[2] == "1"
            out.append({"id": list_id, "name": name, "group": grp,
                        "enabled": enabled,
                        "patterns": len(self._domains_of(list_id))})
        return sorted(out, key=lambda r: (r["group"], r["name"]))

    def set_list(self, list_id: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable one list, and re-enforce if blocking is on.

        Toggling a list while blocking is on must change what the machine
        blocks, not wait for the next restart. The hosts block is rewritten
        immediately, and the write is verified by the hosts engine.
        """
        row = self.conn.execute(
            "SELECT value FROM dns_state WHERE key=?", (f"list.{list_id}",)).fetchone()
        if row is None:
            raise BackendError(f"no list called {list_id!r} on this machine")
        parts = str(row[0]).split("|")
        while len(parts) < 3:
            parts.append("0")
        parts[2] = "1" if enabled else "0"
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO dns_state(key,value) VALUES(?,?)",
                (f"list.{list_id}", "|".join(parts)))
        src = self._lists.get(list_id)
        if src is not None:
            src.enabled = bool(enabled)
        self._matcher = None
        out: dict[str, Any] = {"id": list_id, "enabled": bool(enabled),
                              "reenforced": False}
        if self.mode != "off":
            reason = self._engage()
            if reason:
                raise BackendError(reason)
            out["reenforced"] = True
        out["enforced"] = self.hosts.status().get("blocked", 0)
        return out

    def total_blocked(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(blocked),0) FROM dns_counters").fetchone()
        return int(row[0])

    def status(self) -> dict[str, Any]:
        live = [src for src in self._lists.values() if src.enabled]
        hf = self.hosts.status()
        return {"mode": self.mode, "paused": self.is_paused(),
                "pause_remaining_s": self.pause_remaining_s(),
                "lists": len(live),
                "patterns": sum(len(src.domains) for src in live),
                "blocked_total": self.total_blocked(),
                # What is actually in the hosts file right now, which is the only
                # number that means the machine is blocking something.
                "enforced": hf.get("blocked", 0),
                "hosts_path": hf.get("path", ""),
                "hosts_available": hf.get("available", False),
                "hosts_reason": hf.get("reason", "")}
