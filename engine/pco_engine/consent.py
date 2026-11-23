"""Consent — how much the app is allowed to do without asking.

Two independent levels, and the difference between them is the whole point:

    safe     auto-applies only findings the catalog marks `auto_safe`:
             reversible, no security posture change, no data risk.

    trusted  "I trust you": also applies *fixable* review-tier findings.
             A finding is fixable only when the catalog has a tweak for it.
             Anything with no fix — an observation like "3 crashes this week" —
             is never applied, because there is nothing to apply.

What `trusted` still never does:
  * delete files (that is the Cleaner's job, and it has a vault);
  * touch anything not already declared in the catalog;
  * skip the journal, the snapshot, or the confirmation window;
  * leave anything un-undoable.

Consent is stored per-category, because "fix my security stuff" and "move my
files around" are not the same permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

LEVELS = {"off", "safe", "trusted"}


@dataclass
class Decision:
    level: str
    categories: dict[str, str]     # category -> off|safe|trusted
    reason: str

    def as_dict(self) -> dict[str, Any]:
        # copy: a shared mutable dict here meant one profile's "trust everything"
        # silently became the module-level default for every later profile.
        return {"level": self.level, "categories": dict(self.categories),
                "reason": self.reason}


DEFAULT = Decision(
    level="safe",
    categories={"performance": "safe", "security": "safe", "privacy": "off",
                "storage": "off", "reliability": "off"},
    reason="Default: fix reversible performance and security items; never touch "
           "privacy, files, or anything without a declared fix.")


class Consent:
    def __init__(self, conn, clock) -> None:
        self.conn = conn
        self.clock = clock
        self.doc: dict[str, Any] = dict(DEFAULT.as_dict())
        self._load()

    def _load(self) -> None:
        row = self.conn.execute(
            "SELECT value_json FROM settings WHERE scope='consent' AND key='doc'").fetchone()
        if row is None:
            self.doc = dict(DEFAULT.as_dict())
            self._save()
            return
        import json
        try:
            self.doc = json.loads(row[0])
        except Exception:                      # noqa: BLE001 - never fail open
            self.doc = dict(DEFAULT.as_dict())

    def _save(self) -> None:
        import json
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings(scope,key,value_json,schema,updated_at) "
                "VALUES('consent','doc',?,'consent.v1',?) "
                "ON CONFLICT(scope,key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (json.dumps(self.doc, sort_keys=True), self.clock()))

    # -------------------------------------------------------------------- read
    def decision(self) -> Decision:
        return Decision(level=self.doc["level"], categories=dict(self.doc["categories"]),
                        reason=self.doc["reason"])

    def allows(self, category: str, *, auto_safe: bool, has_fix: bool) -> bool:
        """The single gate the checkup consults. One function, so an audit is
        reading one paragraph instead of hunting through the UI."""
        level = self.doc["categories"].get(category, "off")
        if level == "off":
            return False
        if not has_fix:
            return False                       # nothing to apply, regardless of trust
        if auto_safe:
            return True                        # safe items apply at any level above off
        return level == "trusted"               # review items need explicit trust

    # ------------------------------------------------------------------- write
    def set(self, *, level: str | None = None, categories: dict[str, str] | None = None,
            reason: str | None = None) -> Decision:
        if level is not None:
            if level not in LEVELS:
                raise ValueError(f"level must be one of {sorted(LEVELS)}")
            self.doc["level"] = level
        if categories:
            for cat, lvl in categories.items():
                if lvl not in LEVELS:
                    raise ValueError(f"{cat}: level must be one of {sorted(LEVELS)}")
                self.doc["categories"][cat] = lvl
        if reason is not None:
            self.doc["reason"] = reason
        self._save()
        return self.decision()

    def trust_everything(self) -> Decision:
        """The 'fix literally everything' switch. Equivalent to trusted on every
        category, and still bounded by what the catalog can actually fix."""
        return self.set(level="trusted",
                        categories={c: "trusted" for c in self.doc["categories"]},
                        reason="User chose: fix everything the catalog can fix, "
                               "including security and privacy items.")

    def reset(self) -> Decision:
        self.doc = dict(DEFAULT.as_dict())
        self._save()
        return self.decision()

    # ------------------------------------------------------------------ explain
    def explain(self, findings: Iterable[dict[str, Any]]) -> dict[str, Any]:
        """What the chosen level would actually do to this exact result set.

        The UI shows this before the button, so 'fix everything' is never an
        unknown quantity.
        """
        would, withheld = [], []
        for f in findings:
            has_fix = bool(f.get("fix_tweaks"))
            if self.allows(f.get("category", ""), auto_safe=bool(f.get("auto_safe")),
                           has_fix=has_fix):
                would.append(f.get("check_id"))
            else:
                why = ("no declared fix" if not has_fix
                       else f"category '{f.get('category')}' is off"
                       if self.doc["categories"].get(f.get("category"), "off") == "off"
                       else "needs the trusted level")
                withheld.append({"check_id": f.get("check_id"), "why": why})
        return {"would_apply": would, "withheld": withheld}
