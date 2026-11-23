""""What did I change?" — the journal, in plain words.

Local intent parsing only. The question is mapped to a small set of queries by
matching words, never by sending anything anywhere, and every answer cites the
journal row it came from plus the transaction that can undo it.

The interesting part is not the matching; it is that the answer is *always*
traceable. There is no generated prose that cannot be checked against a row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

INTENTS = ("what_changed", "what_broke", "undo", "why_slow", "what_installed",
           "disk", "security", "help")

VERB_HINTS = {
    "what_changed": ("what did i change", "what changed", "changes", "changed",
                     "what did you do", "history", "recent"),
    "what_broke": ("what broke", "why did it break", "broke", "crash", "broken",
                   "stopped working", "error"),
    "undo": ("undo", "revert", "roll back", "rollback", "cancel"),
    "why_slow": ("why is it slow", "slow", "lag", "laggy", "stutter", "why so slow"),
    "what_installed": ("installed", "install", "new app", "what was installed"),
    "disk": ("disk", "space", "storage", "full", "grew", "large"),
    "security": ("security", "secure", "safe", "virus", "malware", "unsafe",
                 "privacy", "tracker", "blocked", "protected"),
    "help": ("help", "how", "what can you do", "commands"),
}


def classify(question: str) -> str:
    q = question.lower().strip()
    if not q:
        return "help"
    for intent, hints in VERB_HINTS.items():
        for hint in hints:
            if hint in q:
                return intent
    return "what_changed"          # the safest default: show them the facts


@dataclass
class Answer:
    intent: str
    headline: str
    items: list[dict[str, Any]]
    undoable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"intent": self.intent, "headline": self.headline,
                "items": self.items, "undoable": self.undoable,
                "note": "Every line here comes from the journal, not from a summary."}


class Ask:
    def __init__(self, service: Any) -> None:
        self.svc = service

    def ask(self, question: str, *, limit: int = 12) -> Answer:
        intent = classify(question)
        handler = getattr(self, f"_{intent}")
        return handler(limit)

    # --------------------------------------------------------------- answers
    def _what_changed(self, limit: int) -> Answer:
        rows = self.svc.engine.history(limit)
        items = []
        for r in rows:
            entries = [e for e in self.svc.engine.journal.entries(r["id"])
                       if e.phase == "verified"]
            items.append({
                "tx_id": r["id"], "at": r["started_at"], "kind": r["kind"],
                "state": r["state"],
                "changed": [{"address": a, "to": v}
                            for e in entries for a, v in e.poststate.items()],
            })
        n = len(items)
        return Answer(intent="what_changed",
                      headline=(f"{n} change(s) recorded, newest first. "
                                f"Each has an Undo." if n else
                                "Nothing has been changed on this PC yet."),
                      items=items, undoable=bool(items))

    def _what_broke(self, limit: int) -> Answer:
        """Correlate failures and drift with the change that preceded them."""
        fails = self.svc.conn.execute(
            "SELECT tx_id,tweak_id,error,at FROM journal WHERE phase='failed' "
            "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        drift = self.svc.conn.execute(
            "SELECT address,detected_at,actor_guess FROM drift "
            "ORDER BY detected_at DESC LIMIT ?", (limit,)).fetchall()
        items = [{"kind": "change_failed", "tx_id": f[0], "what": f[1],
                  "error": f[2], "at": f[3]} for f in fails]
        items += [{"kind": "reverted_by_system", "address": d[0],
                   "at": d[1], "actor": d[2]} for d in drift]
        crashes = self.svc.sentinel.feed(limit, kinds={"crash"})
        items += [{"kind": "crash", "at": c["at"], "what": c["narrative"]}
                  for c in crashes]
        items.sort(key=lambda i: -i["at"])
        return Answer(intent="what_broke",
                      headline=("Nothing failed or was reverted." if not items else
                                f"{len(items)} event(s) worth knowing about, newest first."),
                      items=items)

    def _undo(self, limit: int) -> Answer:
        rows = [r for r in self.svc.engine.history(limit)
                if r["state"] in {"committed", "armed"}]
        items = [{"tx_id": r["id"], "what": r["kind"], "at": r["started_at"]}
                 for r in rows]
        return Answer(intent="undo",
                      headline=(f"The most recent change you can undo is "
                                f"{items[0]['tx_id']}." if items else
                                "There is nothing to undo."),
                      items=items, undoable=bool(items))

    def _why_slow(self, limit: int) -> Answer:
        st = self.svc.status()
        items: list[dict[str, Any]] = []
        ck = st.get("checkup")
        if ck:
            for f in self.svc.checker.findings_of(ck["run_id"]):
                if f["category"] == "performance":
                    items.append({"kind": "finding", "title": f["title"],
                                  "plain": f["plain"], "fix": f["fix_tweaks"]})
        sessions = self.svc.gaming.sessions(3)
        for s in sessions:
            if s["bottleneck"]:
                items.append({"kind": "game_session", "game": s["game"],
                              "limiter": s["bottleneck"],
                              "fps_1pct": s["stats"].get("fps_p1")})
        return Answer(intent="why_slow",
                      headline=("Performance findings, then what your last "
                                "gameplay sessions showed." if items else
                                "Run a checkup and watch a session, then ask again."),
                      items=items)

    def _what_installed(self, limit: int) -> Answer:
        events = self.svc.sentinel.feed(limit, kinds={"install", "autopsy"})
        autopsies = self.svc.autopsy.list(limit)
        items = [{"kind": "event", "at": e["at"], "what": e["narrative"]}
                 for e in events]
        items += [{"kind": "autopsy", "id": a["id"], "what": a["installer"],
                   "state": a["state"], "summary": a["summary"]} for a in autopsies]
        return Answer(intent="what_installed",
                      headline=(f"{len(items)} install event(s) recorded." if items
                                else "No installs recorded. Nothing to report."),
                      items=items)

    def _disk(self, limit: int) -> Answer:
        grew = self.svc.space.grew(7)
        if not grew.get("available"):
            return Answer(intent="disk", headline=grew.get("reason", "no samples"),
                          items=[])
        return Answer(intent="disk", headline=grew["summary"],
                      items=grew["changed"][:limit])

    def _security(self, limit: int) -> Answer:
        st = self.svc.status()
        dns = st.get("dns", {})
        ck = st.get("checkup")
        findings = [f for f in (self.svc.checker.findings_of(ck["run_id"]) if ck else [])
                    if f["category"] in {"security", "privacy"}]
        items = [{"kind": "finding", "title": f["title"], "plain": f["plain"]}
                 for f in findings]
        if not ck:
            # no checkup yet: say that plainly instead of implying "all clear"
            items.append({"kind": "notice",
                          "plain": "No checkup has run on this machine yet, so there is "
                                   "no security evidence to report. Run one and ask again."})
        items.append({"kind": "blocker", "mode": dns.get("mode"),
                      "blocked_total": dns.get("blocked_total")})
        return Answer(intent="security",
                      headline=(f"{len(findings)} security/privacy finding(s); "
                                f"blocker is {dns.get('mode', 'off')}."),
                      items=items)

    def _help(self, limit: int) -> Answer:
        examples = [
            "what did I change?",
            "why is it slow?",
            "what broke?",
            "undo",
            "what got installed?",
            "why is my disk full?",
            "am I secure?",
        ]
        return Answer(intent="help",
                      headline="Ask in plain words. Answers come from your own journal.",
                      items=[{"kind": "example", "question": q} for q in examples])
