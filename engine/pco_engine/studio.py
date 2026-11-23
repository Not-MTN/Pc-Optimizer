"""Desktop Studio — customisation with a measured cost.

The rule that makes this safe: a theme declares its animation budget, and the
governor refuses anything that would push total idle GPU above 3%. A beautiful
desktop is a feature; a beautiful desktop that costs 12% of a laptop's battery is
a defect wearing a nice hat.

Themes are documents (`pco.theme.v1`), so sharing is a file, not an account.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ids import new_id

IDLE_GPU_BUDGET_PCT = 3.0        # hard product rule, from MOTION.md

# what each effect costs at idle, in percentage points of GPU
COSTS = {
    "static_wallpaper": 0.0,
    "animated_wallpaper": 1.1,
    "widgets_light": 0.4,
    "widgets_heavy": 1.6,
    "taskbar_blur": 0.3,
    "taskbar_accent": 0.0,
    "icon_pack": 0.0,
    "cursor_pack": 0.0,
    "sounds": 0.0,
    "rainmeter": 1.3,
    "live_clock": 0.1,
    "spectrum": 1.9,
}

KNOWN_KEYS = set(COSTS)


@dataclass
class Theme:
    id: str
    name: str
    doc: dict[str, Any]
    cost: float = 0.0
    applied_at: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "doc": self.doc,
                "cost_pct": round(self.cost, 2), "applied_at": self.applied_at}


def validate(doc: dict[str, Any]) -> list[str]:
    problems = []
    if not isinstance(doc, dict):
        return ["theme must be an object"]
    feats = doc.get("features", {})
    if not isinstance(feats, dict):
        return ["features must be an object"]
    unknown = set(feats) - KNOWN_KEYS
    if unknown:
        problems.append(f"unknown feature(s): {', '.join(sorted(unknown))}")
    for key, value in feats.items():
        if not isinstance(value, bool) and not isinstance(value, (str, int, float)):
            problems.append(f"{key}: value must be simple")
    if "name" not in doc:
        problems.append("theme needs a name")
    return problems


def cost_of(doc: dict[str, Any]) -> float:
    feats = doc.get("features", {}) or {}
    total = 0.0
    for key, value in feats.items():
        if not value:
            continue
        total += COSTS.get(key, 0.0)
    return total


def governor(cost: float, *, budget: float = IDLE_GPU_BUDGET_PCT) -> dict[str, Any]:
    """The refusal is the feature. Say what it costs and what to turn off."""
    ok = cost <= budget
    over = max(0.0, cost - budget)
    return {"ok": ok, "cost_pct": round(cost, 2), "budget_pct": budget,
            "over_pct": round(over, 2),
            "message": ("Within budget." if ok else
                        f"This theme would idle at {cost:.1f}% GPU, "
                        f"{over:.1f} points over the {budget}% budget.")}


class Studio:
    def __init__(self, conn, clock) -> None:
        self.conn = conn
        self.clock = clock
        self._seed()

    def _seed(self) -> None:
        presets = [
            {"name": "Quiet Dark", "features": {"static_wallpaper": True,
                                                "taskbar_accent": True}},
            {"name": "Studio Rain", "features": {"animated_wallpaper": True,
                                                 "widgets_light": True}},
            {"name": "Overkill", "features": {"animated_wallpaper": True,
                                              "spectrum": True, "widgets_heavy": True}},
        ]
        with self.conn:
            for p in presets:
                if self.conn.execute(
                        "SELECT 1 FROM desktop_themes WHERE name=?", (p["name"],)).fetchone():
                    continue
                self.conn.execute(
                    "INSERT INTO desktop_themes(id,name,doc_json,cost_milli) VALUES(?,?,?,?)",
                    (new_id(), p["name"], _json(p), int(cost_of(p) * 1000)))

    # --------------------------------------------------------------------- read
    def themes(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,name,doc_json,cost_milli,applied_at FROM desktop_themes "
            "ORDER BY cost_milli").fetchall()
        return [Theme(id=r[0], name=r[1], doc=_load(r[2]), cost=r[3] / 1000,
                      applied_at=r[4]).as_dict() for r in rows]

    def active(self) -> dict[str, Any] | None:
        return next((t for t in self.themes() if t["applied_at"]), None)

    def budget(self) -> dict[str, Any]:
        cur = self.active()
        return {"budget_pct": IDLE_GPU_BUDGET_PCT,
                "active": cur["name"] if cur else None,
                "active_cost_pct": cur["cost_pct"] if cur else 0.0,
                "remaining_pct": round(IDLE_GPU_BUDGET_PCT - (cur["cost_pct"] if cur else 0), 2)}

    # -------------------------------------------------------------------- write
    def save(self, doc: dict[str, Any], *, name: str | None = None) -> dict[str, Any]:
        problems = validate(doc)
        if problems:
            raise ValueError("; ".join(problems))
        cost = cost_of(doc)
        tid = new_id()
        doc = {**doc, "name": name or doc["name"]}
        with self.conn:
            self.conn.execute(
                "INSERT INTO desktop_themes(id,name,doc_json,cost_milli) VALUES(?,?,?,?)",
                (tid, doc["name"], _json(doc), int(cost * 1000)))
        return {"id": tid, "name": doc["name"], "cost_pct": round(cost, 2),
                "governor": governor(cost)}

    def apply(self, theme_id: str, *, force: bool = False) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT name,doc_json,cost_milli FROM desktop_themes WHERE id=?",
            (theme_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown theme {theme_id}")
        cost = row[2] / 1000
        verdict = governor(cost)
        if not verdict["ok"] and not force:
            return {"applied": False, **verdict,
                    "suggest": "Turn off the heaviest feature, or force it knowingly."}
        with self.conn:
            self.conn.execute("UPDATE desktop_themes SET applied_at=NULL")
            self.conn.execute("UPDATE desktop_themes SET applied_at=? WHERE id=?",
                              (self.clock(), theme_id))
            self.conn.execute(
                "INSERT INTO desktop_state(key,value) VALUES('active_theme',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (theme_id,))
        return {"applied": True, "theme": row[0], "cost_pct": round(cost, 2),
                **{k: v for k, v in verdict.items() if k != "ok"}}

    def import_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Import is validation + a governor verdict, never a silent apply."""
        problems = validate(doc)
        if problems:
            raise ValueError("; ".join(problems))
        cost = cost_of(doc)
        return {"valid": True, "cost_pct": round(cost, 2),
                "governor": governor(cost), "doc": doc}


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load(text: str) -> Any:
    import json
    return json.loads(text)
