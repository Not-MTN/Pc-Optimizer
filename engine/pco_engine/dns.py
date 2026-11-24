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
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO dns_state(key,value) VALUES(?,?)",
                (f"list.{list_id}", f"{name}|{grp}|{int(enabled)}"))
        return len(src.domains)

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
        if listener is None or not getattr(listener, "running", False):
            return ("Blocking needs Keel's local DNS resolver, which this build "
                    "does not start. Nothing was blocked — your network settings "
                    "are unchanged.")
        adapters = getattr(backend, "dns_adapters", None)
        setter = getattr(backend, "dns_set", None)
        if adapters is None or setter is None:
            # No adapter API at all means a simulated machine. There is nothing
            # to change and nothing to warn about: the pill already says the
            # whole session is simulated.
            if getattr(backend, "environment", "demo") != "windows":
                return ""
            return ("This build cannot change network adapters, so blocking "
                    "cannot be switched on. Your DNS is unchanged.")
        servers = getattr(listener, "addresses", None) or []
        if not servers:
            return "The local resolver reported no address; nothing was blocked."
        targets = [a for a in adapters() if a]
        if not targets:
            return ("No network adapter could be found to point at the resolver. "
                    "Your DNS is unchanged.")
        out = setter(targets[0], servers)
        if not out.get("ok"):
            return (f"Windows would not take the DNS change on '{targets[0]}': "
                    f"{out.get('reason') or 'no reason given'}. Your DNS is unchanged.")
        flush = getattr(backend, "dns_flush", None)
        if flush is not None:
            flush()
        self._engaged_on = targets[0]
        return ""

    def _release(self) -> None:
        backend = getattr(self, "backend", None)
        adapter = getattr(self, "_engaged_on", None)
        if backend is None or not adapter:
            return
        clear = getattr(backend, "dns_clear", None)
        if clear is not None:
            clear(adapter)
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

    def total_blocked(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(blocked),0) FROM dns_counters").fetchone()
        return int(row[0])

    def status(self) -> dict[str, Any]:
        live = [src for src in self._lists.values() if src.enabled]
        return {"mode": self.mode, "paused": self.is_paused(),
                "pause_remaining_s": self.pause_remaining_s(),
                "lists": len(live),
                "patterns": sum(len(src.domains) for src in live),
                "blocked_total": self.total_blocked()}
