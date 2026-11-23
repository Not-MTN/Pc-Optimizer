"""What a visual feature costs, and who decides whether to spend it.

The policy, stated once so it cannot drift:

    **Nothing is forced. The zero-cost option always exists and is always
    selectable. Everything above zero is opt-in, and every effect declares its
    cost before you turn it on.**

The app used to *refuse* a theme over a 3% idle-GPU budget. That was wrong in a
specific way: it treated a product rule as the user's budget. If someone wants an
animated wallpaper on a desktop machine that is plugged in, refusing is not
safety, it is paternalism. What actually matters is:

  * the cost is **known** before the choice (a measured estimate, not a guess)
  * the cost is **shown** after the choice (real counters where available)
  * the cheapest option is **always available** and never hidden
  * nothing is applied **silently**

So `verify()` reports and recommends; it never blocks. The only hard block left
is on a machine where a choice would measurably hurt — a battery-powered laptop
asked to run a 60 fps scene — and even that is a warning with the alternative
named, not a refusal.

Costs are percentage points of GPU at idle. They are *estimates from a fixed
table*, and the table says so: measured numbers replace them where the platform
provides counters (see `measured=False` on every verdict).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------- the table
# Percentage points of GPU at idle. Estimates, deliberately conservative.
#
# "static" is 0.0 and that is a promise, not an optimisation: a still image is
# composited by DWM whether the app exists or not, so the app adds nothing.
COSTS: dict[str, float] = {
    "static_wallpaper": 0.0,
    "generated_wallpaper": 0.0,       # rendered once to a PNG; then it is static
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
    "wallpaper_video": 2.2,
    "wallpaper_shader": 2.8,
    "widgets_live": 1.2,
    "hdr_overlay": 0.0,
}

# Effects that cannot be zero, and what the zero-cost alternative is. Having this
# next to the table is the difference between "we warn you" and "here is the
# thing you probably want instead".
ALTERNATIVES: dict[str, str] = {
    "animated_wallpaper": "a generated still wallpaper (0%)",
    "wallpaper_video": "a generated still wallpaper (0%)",
    "wallpaper_shader": "a generated still wallpaper (0%)",
    "widgets_heavy": "baked widgets in the wallpaper (0%)",
    "widgets_live": "baked widgets in the wallpaper (0%)",
    "rainmeter": "baked widgets in the wallpaper (0%)",
    "spectrum": "a static accent bar (0%)",
    "live_clock": "no clock, or the tray clock",
    "taskbar_blur": "accent colour on the taskbar (0%)",
}

# What each tier means. Used in the UI so the choice is a sentence, not a slider.
TIERS: dict[str, str] = {
    "zero": "0% — a still image. Nothing is running after the render.",
    "light": "up to 0.5% — small live touches.",
    "balanced": "up to 1.5% — animation or a live layer, one of them.",
    "full": "over 1.5% — everything on. Your call; it is measured and shown.",
}

IDLE_GPU_BUDGET_PCT = 3.0        # the historical product rule, now advisory only


def cost_of(doc: dict[str, Any]) -> float:
    """Total estimated idle-GPU cost of a document.

    Accepts a document with a `features` map (the Studio format) or a theme
    document carrying a `features` map inside it.
    """
    feats = doc.get("features") or {}
    if not isinstance(feats, dict):
        return 0.0
    total = 0.0
    for key, value in feats.items():
        if not value:
            continue
        total += COSTS.get(key, 0.0)
    return round(total, 3)


def tier_of(cost: float) -> str:
    if cost <= 0.0:
        return "zero"
    if cost <= 0.5:
        return "light"
    if cost <= 1.5:
        return "balanced"
    return "full"


def alternatives_for(doc: dict[str, Any]) -> list[dict[str, str]]:
    """The cheaper version of each costly thing this document turns on."""
    out: list[dict[str, str]] = []
    for key, value in (doc.get("features") or {}).items():
        if value and key in ALTERNATIVES:
            out.append({"feature": key, "instead": ALTERNATIVES[key],
                        "saves_pct": f"{COSTS.get(key, 0.0):.1f}%"})
    return sorted(out, key=lambda a: a["feature"])


@dataclass(frozen=True)
class Verdict:
    """The answer to "what does this cost, and should I care?"."""

    cost_pct: float
    tier: str
    measured: bool = False
    over_advisory_budget: bool = False
    recommendations: tuple[str, ...] = ()
    alternatives: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "cost_pct": round(self.cost_pct, 2),
            "tier": self.tier,
            "tier_meaning": TIERS[self.tier],
            "measured": self.measured,
            "blocked": False,
            "over_advisory_budget": self.over_advisory_budget,
            "advisory_budget_pct": IDLE_GPU_BUDGET_PCT,
            "recommendations": list(self.recommendations),
            "alternatives": [{"feature": f, "instead": i}
                             for f, i in self.alternatives],
            "note": ("Nothing is blocked. The zero-cost option is always "
                     "available; this is what the choice costs."),
        }


def verify(doc: dict[str, Any], *, on_battery: bool | None = None,
           plugged_in: bool = True) -> Verdict:
    """Report the cost of a document and what would make it cheaper.

    Never raises, never blocks, never returns `ok=False`. A caller that wants a
    gate has to add one; the default is that the user decides.
    """
    cost = cost_of(doc)
    recs: list[str] = []
    if cost >= 1.0:
        recs.append(f"{cost:.1f}% of GPU at idle is real on a laptop; "
                    f"it is negligible on a desktop.")
    if on_battery and cost > 0:
        recs.append("On battery this will shorten the charge. "
                    "The static variant costs nothing.")
    if cost > IDLE_GPU_BUDGET_PCT:
        recs.append(f"This is above the {IDLE_GPU_BUDGET_PCT:.0f}% figure the "
                    f"app previously refused outright. It is no longer a "
                    f"refusal — it is a number you get to see.")
    alts = tuple((a["feature"], a["instead"]) for a in alternatives_for(doc))
    return Verdict(cost_pct=cost, tier=tier_of(cost),
                   over_advisory_budget=cost > IDLE_GPU_BUDGET_PCT,
                   recommendations=tuple(recs), alternatives=alts)


def zero_cost_variant(doc: dict[str, Any]) -> dict[str, Any]:
    """The same document with every costly feature turned off.

    Exists so "make it cheaper" is one call and the result is a document, not a
    list of instructions for the user to follow.
    """
    import copy
    out = copy.deepcopy(doc)
    feats = out.get("features")
    if isinstance(feats, dict):
        for key in list(feats):
            if value_of(key) > 0:
                feats[key] = False
    return out


def value_of(feature: str) -> float:
    return COSTS.get(feature, 0.0)
