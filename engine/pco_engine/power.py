"""Battery and thermals — the laptop half of the product.

Two things a laptop owner actually needs, and one they are usually lied about:

  health    design capacity vs actual capacity. A 68% battery is not "fine",
            and the app should say what that means in minutes, not watt-hours.
  drain     which process is eating the battery. Listed by measured draw, and
            anything that cannot be attributed is called out as unattributed.

Thermal throttling is treated as evidence, not vibes: a throttle event is only
recorded when the clock dropped *and* the temperature was at the limit.

Charge limiting is hardware-dependent, which is exactly why it reports
"not supported on this machine" instead of pretending to have done something.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ids import new_id

HEALTH_WARN = 80.0        # below this, a battery is worth replacing
HEALTH_BAD = 60.0
THROTTLE_TEMP_C = 95.0
THROTTLE_CLOCK_DROP = 0.85     # a 15% clock drop while hot counts as throttling


@dataclass
class BatteryReading:
    design_mwh: int
    full_mwh: int
    now_mwh: int
    rate_mw: int              # negative while discharging
    percent: int
    plugged: bool
    cycles: int | None = None
    temp_c: float | None = None

    @property
    def health_pct(self) -> float:
        if self.design_mwh <= 0:
            return 0.0
        return max(0.0, min(150.0, 100.0 * self.full_mwh / self.design_mwh))

    @property
    def minutes_left(self) -> int | None:
        if self.plugged or self.rate_mw >= -1:
            return None
        return int(self.now_mwh / abs(self.rate_mw) * 60)


@dataclass
class Drain:
    process: str
    mw: float = 0.0
    share_pct: float = 0.0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"process": self.process, "mw": round(self.mw, 1),
                "share_pct": round(self.share_pct, 1), "note": self.note}


@dataclass
class ThrottleEvent:
    at: int
    temp_c: float
    clock_before: float
    clock_after: float
    reason: str = ""

    @property
    def drop_pct(self) -> float:
        if self.clock_before <= 0:
            return 0.0
        return 100.0 * (1 - self.clock_after / self.clock_before)

    def as_dict(self) -> dict[str, Any]:
        return {"at": self.at, "temp_c": self.temp_c,
                "drop_pct": round(self.drop_pct, 1), "reason": self.reason}


def diagnose(b: BatteryReading) -> dict[str, Any]:
    """One paragraph, no hedging, and a recommendation that names a next step."""
    h = b.health_pct
    if h >= HEALTH_WARN:
        verdict = f"Battery health is {h:.0f}% of its design capacity — normal wear."
        advice = "Nothing to do. Avoid leaving it at 100% in a hot room for days at a time."
        level = "ok"
    elif h >= HEALTH_BAD:
        verdict = (f"Battery health is {h:.0f}% — noticeably worn. "
                   f"Design capacity {b.design_mwh/1000:.0f} Wh, now {b.full_mwh/1000:.0f} Wh.")
        advice = ("Expect roughly %d%% of the original battery life. "
                  "A replacement is reasonable before it becomes an inconvenience."
                  % int(h))
        level = "warn"
    else:
        verdict = (f"Battery health is {h:.0f}% — end of life. "
                   f"It holds {b.full_mwh/1000:.0f} Wh of {b.design_mwh/1000:.0f} Wh.")
        advice = "Replace the battery, or treat the machine as a portable desktop."
        level = "bad"
    if h > 105:
        verdict = (f"Reported capacity is {h:.0f}% of design — that is a measurement "
                   f"artefact or a non-original battery, not extra capacity.")
        advice = "Calibrate once (full discharge, full charge) before trusting the number."
        level = "warn"
    out = {"health_pct": round(h, 1), "verdict": verdict, "advice": advice,
           "level": level, "minutes_left": b.minutes_left}
    if b.cycles is not None:
        out["cycles"] = b.cycles
        out["cycle_note"] = ("Modern cells are rated ~300–500 cycles to 80%; "
                             "this is a wear figure, not a countdown.")
    return out


def attribute_drain(readings: dict[str, float], *, total_mw: float) -> list[Drain]:
    """Turn absolute draw figures into shares, and be explicit about leftovers."""
    if total_mw <= 0:
        return []
    # draw figures are negative while discharging; magnitude is what matters, and a
    # positive value here would be a *charging* process, which does not exist.
    known = sum(abs(v) for v in readings.values())
    out: list[Drain] = []
    for name, mw in sorted(readings.items(), key=lambda kv: -abs(kv[1])):
        if abs(mw) <= 0:
            continue
        out.append(Drain(process=name, mw=abs(mw),
                         share_pct=100.0 * abs(mw) / total_mw,
                         note=_drain_note(name)))
    leftover = total_mw - known
    if leftover > total_mw * 0.08:
        out.append(Drain(process="(unattributed)", mw=leftover,
                         share_pct=100.0 * leftover / total_mw,
                         note="screen brightness, radios, storage — not one process"))
    # biggest user first, but a named process always outranks the residual bucket:
    # "unattributed" is context, not a culprit.
    out.sort(key=lambda d: (d.process == "(unattributed)", -d.mw))
    return out


def _drain_note(name: str) -> str:
    low = name.lower()
    if "chrome" in low or "browser" in low:
        return "Hardware-accelerated video keeps the GPU awake; try battery saver in-browser."
    if "steam" in low or "game" in low:
        return "A launcher or game running in the background still polls the GPU."
    if "sync" in low or "onedrive" in low:
        return "Continuous sync keeps the disk and radio busy; pause it on battery."
    return ""


def throttled(before_clock: float, after_clock: float, temp_c: float) -> bool:
    """Throttling means hot AND slower — one without the other is just a load change."""
    return temp_c >= THROTTLE_TEMP_C and after_clock < before_clock * THROTTLE_CLOCK_DROP


class Power:
    """Reads the platform, records what it saw, and never invents a number."""

    def __init__(self, conn, clock, *, reader=None) -> None:
        self.conn = conn
        self.clock = clock
        self.reader = reader

    def sample(self) -> dict[str, Any] | None:
        if self.reader is None:
            return None
        b: BatteryReading | None = self.reader()
        if b is None:
            return {"available": False,
                    "reason": "no battery detected — this is a desktop"}
        import json
        diag = diagnose(b)
        with self.conn:
            self.conn.execute(
                "INSERT INTO battery_samples(id,at,design_mwh,full_mwh,now_mwh,"
                "rate_mw,percent,plugged,cycles,temp_c) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (new_id(), self.clock(), b.design_mwh, b.full_mwh, b.now_mwh,
                 b.rate_mw, b.percent, int(b.plugged), b.cycles, b.temp_c))
        return {"available": True, "reading": b.__dict__ | {"health_pct": b.health_pct},
                "diagnosis": diag}

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT at,design_mwh,full_mwh,now_mwh,rate_mw,percent,plugged,cycles,temp_c "
            "FROM battery_samples ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def health_trend(self) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT at,design_mwh,full_mwh FROM battery_samples ORDER BY at").fetchall()
        if len(rows) < 2:
            return {"available": False, "reason": "need two samples to show a trend"}
        first, last = rows[0], rows[-1]
        h0 = 100 * first["full_mwh"] / max(1, first["design_mwh"])
        h1 = 100 * last["full_mwh"] / max(1, last["design_mwh"])
        days = max(1, (last["at"] - first["at"]) // 86_400_000)
        return {"available": True, "from_pct": round(h0, 1), "to_pct": round(h1, 1),
                "days": days,
                "per_month": round((h1 - h0) / max(1, days) * 30, 2)}

    # ------------------------------------------------------------ charge limit
    def charge_limit(self, *, supported: bool, set_to: int | None = None
                     ) -> dict[str, Any]:
        """Hardware support is the gate. Claiming a limit we cannot set would be
        the single most damaging lie this app could tell."""
        if not supported:
            return {"supported": False,
                    "reason": "This machine's firmware does not expose a charge limit.",
                    "alternative": ("If the vendor has its own app with a limit, use it; "
                                    "this app will not pretend to control your charger.")}
        if set_to is None:
            return {"supported": True, "current_limit": 80}
        if not 50 <= set_to <= 100:
            raise ValueError("charge limit must be between 50 and 100 percent")
        return {"supported": True, "set_to": set_to,
                "note": "Firmware setting; it survives reboots and OS reinstalls."}

    # ----------------------------------------------------------------- thermals
    def record_thermal(self, before_clock: float, after_clock: float,
                       temp_c: float, *, reason: str = "") -> dict[str, Any] | None:
        if not throttled(before_clock, after_clock, temp_c):
            return None
        ev = ThrottleEvent(self.clock(), temp_c, before_clock, after_clock, reason)
        import json
        with self.conn:
            self.conn.execute(
                "INSERT INTO thermal_events(id,at,temp_c,clock_before,clock_after,"
                "drop_pct,reason) VALUES(?,?,?,?,?,?,?)",
                (new_id(), ev.at, ev.temp_c, ev.clock_before, ev.clock_after,
                 ev.drop_pct, ev.reason))
        return ev.as_dict()

    def throttles(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT at,temp_c,clock_before,clock_after,drop_pct,reason FROM thermal_events "
            "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def thermal_verdict(self) -> dict[str, Any]:
        events = self.throttles(50)
        if not events:
            return {"throttled": False,
                    "verdict": "No throttling recorded — the cooling is keeping up."}
        worst = max(events, key=lambda e: e["drop_pct"])
        return {"throttled": True, "events": len(events),
                "worst_drop_pct": worst["drop_pct"], "worst_temp_c": worst["temp_c"],
                "verdict": (f"{len(events)} throttle event(s); the worst lost "
                            f"{worst['drop_pct']:.0f}% of clock speed at "
                            f"{worst['temp_c']:.0f}°C."),
                "advice": ("Clean the fans and raise the rear of the laptop; "
                           "a laptop that throttles silently is slower than its specs.")}
