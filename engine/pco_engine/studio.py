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

# The policy lives in cost.py so the Studio, the theme gallery and any future
# shell all obey one table. This module used to hold its own copy, which is how
# two different answers to "what does this cost" appeared in one app.
from .cost import (  # noqa: E402
    ALTERNATIVES, COSTS, IDLE_GPU_BUDGET_PCT, TIERS, alternatives_for,
    cost_of, tier_of, verify as _verify, zero_cost_variant,
)


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


KNOWN_KEYS = set(COSTS)


def validate(doc: dict[str, Any]) -> list[str]:
    """Problems with a Studio feature document (not a theme document).

    Kept separate from `themes.validate` because the two formats are different:
    this one is a flat feature map, a theme is a full look. Mixing the two is what
    made the gallery appear to cost 0% — see `Studio.themes`.
    """
    problems: list[str] = []
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


def governor(cost: float, *, budget: float = IDLE_GPU_BUDGET_PCT) -> dict[str, Any]:
    """Deprecated shim. A cost alone cannot decide anything now.

    `apply` previously refused anything above 3% unless forced. That treated a
    product rule as the user's budget. The replacement reports and recommends;
    see `cost.verify`. This shim stays so an old caller gets a verdict-shaped
    dict instead of an AttributeError.
    """
    verdict = _verify({"features": {"_": cost}})
    return {"ok": True, "cost_pct": round(cost, 2), "budget_pct": budget,
            "over_pct": round(max(0.0, cost - budget), 2),
            "blocked": False,
            "message": ("Nothing is blocked. "
                        + ("This is above the advisory "
                           f"{budget:.0f}% figure." if cost > budget else
                           "Within the advisory figure."))}


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
        """Only Studio-format rows.

        `desktop_themes` holds two document shapes: the Studio's feature maps and
        the theme gallery's full looks. Reading one as the other is not a
        cosmetic bug — `cost_of` returns 0.0 for a theme document, so the whole
        gallery appeared to cost nothing. Filtering by shape is the fix.
        """
        rows = self.conn.execute(
            "SELECT id,name,doc_json,cost_milli,applied_at FROM desktop_themes "
            "WHERE doc_json NOT LIKE '%\"meta\"%' ORDER BY cost_milli").fetchall()
        return [Theme(id=r[0], name=r[1], doc=_load(r[2]), cost=r[3] / 1000,
                      applied_at=r[4]).as_dict() for r in rows]

    def active(self) -> dict[str, Any] | None:
        return next((t for t in self.themes() if t["applied_at"]), None)

    def budget(self) -> dict[str, Any]:
        """The cost picture. No budget to exceed — a number to see."""
        cur = self.active()
        active_cost = cur["cost_pct"] if cur else 0.0
        return {
            "advisory_pct": IDLE_GPU_BUDGET_PCT,
            "active": cur["name"] if cur else None,
            "active_cost_pct": active_cost,
            "tier": tier_of(active_cost),
            "tier_meaning": TIERS[tier_of(active_cost)],
            "zero_cost_available": True,
            "note": ("Nothing is blocked and nothing is forced. A still "
                     "wallpaper costs 0% and is always available."),
        }

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
                "cost": _verify(doc).as_dict()}

    def apply(self, theme_id: str, *, force: bool = False) -> dict[str, Any]:
        """Apply a theme. `force` is accepted and ignored — kept for API
        compatibility, because there is nothing left to force past.

        A cost is not a permission slip. This applies, and returns what it will
        cost so the UI can show it and offer the zero-cost variant.
        """
        row = self.conn.execute(
            "SELECT name,doc_json,cost_milli FROM desktop_themes WHERE id=?",
            (theme_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown theme {theme_id}")
        cost = row[2] / 1000
        verdict = _verify({"features": {"_": cost}})
        with self.conn:
            self.conn.execute("UPDATE desktop_themes SET applied_at=NULL")
            self.conn.execute("UPDATE desktop_themes SET applied_at=? WHERE id=?",
                              (self.clock(), theme_id))
            self.conn.execute(
                "INSERT INTO desktop_state(key,value) VALUES('active_theme',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (theme_id,))
        return {"applied": True, "theme": row[0], "cost_pct": round(cost, 2),
                "cost": verdict.as_dict()}

    def import_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Import is validation + a governor verdict, never a silent apply."""
        problems = validate(doc)
        if problems:
            raise ValueError("; ".join(problems))
        cost = cost_of(doc)
        return {"valid": True, "cost_pct": round(cost, 2),
                "cost": _verify(doc).as_dict(), "doc": doc,
                "zero_cost": zero_cost_variant(doc)}


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load(text: str) -> Any:
    import json
    return json.loads(text)
