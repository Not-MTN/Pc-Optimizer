"""Natural language → a previewed recipe. Never executes blind.

The plan (MASTERPLAN E2) says: "clean downloads older than 30 days → a previewed
recipe. Local intent parsing, never executes blind."

This is deliberately a *narrow* grammar. It understands the sentences people
actually type about a PC, and when it does not understand one it says which part
failed rather than guessing. A wrong guess here would delete files, so the
grammar is small on purpose and every parse returns a preview before it can run.

Understood shapes:

    clean <thing> older than <n> days
    when disk is above <n> percent, clean <thing>
    when a game starts, pause the blocker
    every day at <hh:mm>, run a checkup
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

THINGS = {
    "downloads": ("{downloads}", "installers and archives in Downloads"),
    "temp files": ("{temp}", "scratch files"),
    "temp": ("{temp}", "scratch files"),
    "logs": ("{logs}", "old logs and crash dumps"),
    "cache": ("{cache}", "regenerable caches"),
    "junk": ("{temp}", "scratch files"),
}


@dataclass
class Parsed:
    understood: bool
    intent: str = ""
    recipe: dict[str, Any] | None = None
    preview: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"understood": self.understood, "intent": self.intent,
                "recipe": self.recipe, "preview": self.preview,
                "problems": self.problems,
                "confidence": round(self.confidence, 2),
                "note": "Nothing runs until you press Save and the trigger fires."}


NUM_WORDS = {"one": 1, "two": 2, "three": 3, "seven": 7, "ten": 10,
             "fourteen": 14, "thirty": 30, "sixty": 60, "ninety": 90}


def _num(token: str) -> int | None:
    token = token.strip().lower()
    if token in NUM_WORDS:
        return NUM_WORDS[token]
    if token.isdigit():
        return int(token)
    return None


def parse(sentence: str) -> Parsed:
    s = " ".join(sentence.lower().split())
    if not s:
        return Parsed(False, problems=["Say what you want to happen."])

    # ---- when disk is above N percent, clean X
    m = re.match(r"(?:when|if) (?:the )?disk (?:is )?(?:above|over|fuller than) "
                 r"(\w+) ?%?,? (?:then )?(?:clean|delete|remove) (.+)$", s)
    if m:
        pct = _num(m.group(1))
        thing = _thing(m.group(2))
        if pct is None:
            return Parsed(False, problems=[f"I could not read a percentage: {m.group(1)!r}"],
                          confidence=0.3)
        if thing is None:
            return Parsed(False, problems=[f"I do not know how to clean {m.group(2)!r}. "
                                           f"I know: {', '.join(sorted(THINGS))}"],
                          confidence=0.35)
        pattern, why = thing
        return Parsed(True, "disk_threshold", {
            "trigger": {"kind": "disk_threshold", "match": {"above_pct": pct}},
            "actions": [{"action": "clean.scan"},
                        {"action": "clean.run", "scope": pattern}],
            "name": f"Disk over {pct}% → clean {why}",
        }, preview=[f"When free space drops below {100 - pct}%",
                    f"→ clean {why}", "→ receipt per run, one undo"], confidence=0.9)

    # ---- when a game starts, pause the blocker
    m = re.match(r"(?:when|if) (?:a )?(?:game|gaming) (?:starts|launches|is "
                 r"launched),? (?:then )?(pause|turn off|disable) "
                 r"(?:the )?(?:blocker|ad ?block|dns)(?: for (\w+) ?min)?", s)
    if m:
        minutes = _num(m.group(2)) if m.group(2) else 30
        minutes = minutes or 30
        return Parsed(True, "game_pause_blocker", {
            "trigger": {"kind": "game_launch", "match": {}},
            "actions": [{"action": "dns.pause", "minutes": minutes}],
            "name": f"Game starts → pause blocker {minutes} min",
        }, preview=[f"On any game launch",
                    f"→ pause the ad blocker for {minutes} minutes",
                    "→ it turns itself back on; you can also resume manually"],
            confidence=0.85)

    # ---- every day at HH:MM, run X
    m = re.match(r"(?:every day|daily|each day) at (\d{1,2}):?(\d{2}) ?(?:am|pm)?,? "
                 r"(?:then )?(?:run|do) (.+)$", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return Parsed(False, problems=[f"{hh}:{mm:02d} is not a valid time"],
                          confidence=0.3)
        what = m.group(3).strip()
        action = _scheduled_action(what)
        if action is None:
            return Parsed(False, problems=[f"I cannot schedule {what!r} yet."],
                          confidence=0.35)
        return Parsed(True, "schedule", {
            "trigger": {"kind": "schedule", "match": {"at": f"{hh:02d}:{mm:02d}"}},
            "actions": [action],
            "name": f"Daily {hh:02d}:{mm:02d} → {what}",
        }, preview=[f"Every day at {hh:02d}:{mm:02d}",
                    f"→ {what}", "→ runs while idle; a receipt is written"],
            confidence=0.9)

    # ---- clean <thing> older than N days
    m = re.match(r"(?:clean|delete|remove) (.+?)(?: older than)?(?: (\w+) ?days?)?$", s)
    if m:
        thing = _thing(m.group(1))
        days = _num(m.group(2)) if m.group(2) else 0
        if thing is None:
            return Parsed(False,
                          problems=[f"I do not know how to clean {m.group(1)!r}. "
                                    f"I know: {', '.join(sorted(THINGS))}"],
                          confidence=0.4)
        pattern, why = thing
        older = days or 0
        return Parsed(True, "clean_now", {
            "trigger": {"kind": "manual", "match": {}},
            "actions": [{"action": "clean.scan"},
                        {"action": "clean.run", "scope": pattern,
                         "older_than_days": older}],
            "name": (f"Clean {why}" + (f" older than {older} days" if older else "")),
        }, preview=[f"Clean {why}" + (f" older than {older} days" if older else ""),
                    "→ items move to the vault for 30 days",
                    "→ nothing is deleted outright"],
            confidence=0.8 if thing else 0.0)

    return Parsed(False, problems=[
        "I understand these shapes:",
        "  clean downloads older than 30 days",
        "  when disk is above 90%, clean temp files",
        "  when a game starts, pause the blocker for 60 minutes",
        "  every day at 03:00, run a checkup",
    ])


def _thing(text: str) -> tuple[str, str] | None:
    for key, value in THINGS.items():
        if key in text:
            return value
    return None


def _scheduled_action(text: str) -> dict[str, Any] | None:
    if "checkup" in text or "check" in text:
        return {"action": "checkup.run"}
    if "clean" in text:
        return {"action": "clean.run"}
    if "bench" in text:
        return {"action": "bench.run"}
    return None
