"""Peripheral Lab — decisive answers about failing hardware.

Everything here answers a question a person actually asks, and every verdict is
backed by evidence they can screenshot:

  mouse double-click   "is my switch dying?"      → interval histogram
  keyboard chatter     "why do I type 'thee'?"    → duplicate-key timing
  sensor jitter        "is my aim drifting?"      → variance at a fixed speed
  monitor dead pixel   "is it the panel?"         → colour field walk
  polling rate         "is my mouse really 1000?" → intervals per second

Design rule: a test never says "looks fine" unless it measured something. With
no input available it says so, instead of inventing a pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .ids import new_id

# ------------------------------------------------------------------- thresholds
DOUBLE_CLICK_MS = 80        # two clicks closer than this cannot be intentional
CHATTER_MS = 45             # the same key repeating this fast is a hardware fault
JITTER_DEG = 1.5            # sensor drift above this is visible in game


@dataclass
class TestResult:
    device: str
    kind: str
    verdict: str            # pass | fail | inconclusive
    headline: str
    evidence: dict[str, Any] = field(default_factory=dict)
    advice: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id())

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "device": self.device, "kind": self.kind,
                "verdict": self.verdict, "headline": self.headline,
                "evidence": self.evidence, "advice": self.advice}


# ------------------------------------------------------------------- mouse tests
def double_click_test(clicks_ms: Sequence[int], *, device: str = "Mouse") -> TestResult:
    """Intervals between distinct presses. A dying switch fires two for one."""
    intervals = [int(x) for x in clicks_ms]
    if not intervals:
        return TestResult(device, "mouse", "inconclusive",
                          "No clicks recorded — press the button 20 times.",
                          {"clicks": 0})
    ghosts = [i for i in intervals if i < DOUBLE_CLICK_MS]
    rate = 100.0 * len(ghosts) / len(intervals)
    hist = {
        "under_20ms": len([i for i in intervals if i < 20]),
        "20_80ms": len([i for i in intervals if 20 <= i < DOUBLE_CLICK_MS]),
        "80_250ms": len([i for i in intervals if DOUBLE_CLICK_MS <= i < 250]),
        "over_250ms": len([i for i in intervals if i >= 250]),
    }
    if ghosts:
        return TestResult(
            device, "mouse", "fail",
            f"{len(ghosts)} of {len(intervals)} clicks were double-fires "
            f"({rate:.0f}%) — this switch is failing.",
            {"intervals": hist, "ghost_click_pct": round(rate, 1)},
            ["RMA the mouse if it is under warranty — this is a hardware fault, not a setting.",
             "As a stopgap, raise the double-click speed in Windows so the ghost is ignored.",
             "Cleaning helps only if the switch is dirty; a worn switch comes back in days."])
    return TestResult(device, "mouse", "pass",
                      f"No double-fires in {len(intervals)} clicks.",
                      {"intervals": hist, "ghost_click_pct": 0.0},
                      ["Record at least 50 clicks to raise confidence."])


def sensor_jitter_test(samples: Sequence[tuple[float, float]], *,
                       device: str = "Mouse", axis: str = "x") -> TestResult:
    """Move the mouse at one steady speed; the readings should be a straight line.

    A clean sensor gives near-zero spread around the fit. A failing one wanders,
    which players experience as "my aim drifts".
    """
    pts = list(samples)
    if len(pts) < 10:
        return TestResult(device, "mouse", "inconclusive",
                          "Need at least 10 samples while moving at a steady speed.",
                          {"samples": len(pts)})
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1e-9
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    residuals = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    rms = (sum(r * r for r in residuals) / n) ** 0.5
    spread_deg = rms * 0.5          # rough degrees at a typical 400 counts/deg
    verdict = "fail" if spread_deg > JITTER_DEG else "pass"
    return TestResult(
        device, "mouse", verdict,
        (f"Sensor jitter {spread_deg:.2f}° — beyond what a healthy sensor shows."
         if verdict == "fail" else
         f"Sensor tracks cleanly ({spread_deg:.2f}° jitter)."),
        {"axis": axis, "samples": n, "slope": round(slope, 3),
         "jitter_deg": round(spread_deg, 3), "threshold_deg": JITTER_DEG},
        ["Retest on a cloth pad — a scratched desk gives false jitter.",
         "If it persists on two surfaces, the sensor or its lens is dirty or damaged."]
        if verdict == "fail" else
        ["Retest after cleaning the lens if you ever feel drift."])


def polling_rate_test(intervals_us: Sequence[int], *, device: str = "Mouse") -> TestResult:
    """USB polling is 125/250/500/1000 Hz. The measured rate is a fact."""
    vals = sorted(int(v) for v in intervals_us if v > 0)
    if len(vals) < 20:
        return TestResult(device, "polling", "inconclusive",
                          "Need at least 20 movement packets.", {"packets": len(vals)})
    median = vals[len(vals) // 2]
    hz = 1_000_000 / median if median else 0
    nearest = min((125, 250, 500, 1000, 2000, 4000, 8000),
                  key=lambda r: abs(r - hz))
    off = abs(hz - nearest) / nearest * 100
    below = hz < nearest
    verdict = "pass" if off < 15 else "fail"
    return TestResult(
        device, "polling", verdict,
        f"Measured {hz:.0f} Hz (nearest setting {nearest} Hz).",
        {"median_interval_us": median, "measured_hz": round(hz, 1),
         "nominal_hz": nearest, "deviation_pct": round(off, 1)},
        (["Check the USB port: a hub or a shared controller can cap the rate.",
          "Wireless mice drop to a lower rate in power-saving mode — check the vendor app."]
         if below else
         ["The rate is higher than the setting you chose — the device is reporting a "
          "different mode than its software shows.",
          "Replug it directly into the rear I/O panel and retest."])
        if verdict == "fail" else
        ["Polling is stable. Higher rates cost CPU; 1000 Hz is the practical sweet spot."])


# ---------------------------------------------------------------- keyboard tests
def chatter_test(key_times_ms: Sequence[tuple[str, int]], *,
                 device: str = "Keyboard") -> TestResult:
    """A key that repeats faster than a human can is chattering."""
    chatter: dict[str, int] = {}
    last: dict[str, int] = {}
    for key, t in key_times_ms:
        prev = last.get(key)
        if prev is not None and 0 < t - prev < CHATTER_MS:
            chatter[key] = chatter.get(key, 0) + 1
        last[key] = t
    if not key_times_ms:
        return TestResult(device, "keyboard", "inconclusive",
                          "No keystrokes recorded.", {"keys": 0})
    if chatter:
        worst = max(chatter, key=chatter.get)  # type: ignore[arg-type]
        return TestResult(
            device, "keyboard", "fail",
            f"{sum(chatter.values())} chatter event(s); worst key: {worst!r}.",
            {"chatter_by_key": chatter, "threshold_ms": CHATTER_MS},
            [f"{worst!r} is double-firing — this is the switch, not Windows.",
             "Swappable switches: replace that one. Soldered: RMA or use a spare key.",
             "Do not 'fix it' with a key remapper; the fault spreads."])
    return TestResult(device, "keyboard", "pass",
                      f"No chatter across {len(key_times_ms)} keystrokes.",
                      {"keys": len(key_times_ms), "threshold_ms": CHATTER_MS},
                      ["Test the keys you use most, at least 30 presses each."])


# ----------------------------------------------------------------- display tests
def monitor_pattern(kind: str = "dead_pixel", *, width: int = 1920,
                    height: int = 1080) -> dict[str, Any]:
    """The UI draws this; the server only defines the sequence.

    Dead-pixel hunting is a full-screen walk through solid RGB fields: a dead
    pixel is invisible on black and obvious on white, which is why one colour is
    never enough.
    """
    if kind not in {"dead_pixel", "uniformity", "gradient"}:
        raise ValueError(f"unknown monitor test {kind!r}")
    if kind == "dead_pixel":
        steps = ["#000000", "#ffffff", "#ff0000", "#00ff00", "#0000ff",
                 "#00ffff", "#ff00ff", "#ffff00"]
        note = "Look for a dot that never changes colour."
    elif kind == "uniformity":
        steps = ["#202020", "#404040", "#808080", "#c0c0c0"]
        note = "Look for patches of different brightness on one flat field."
    else:
        steps = ["#000000", "#ffffff"]
        note = "Look for banding steps rather than a smooth ramp."
    return {"kind": kind, "width": width, "height": height, "steps": steps,
            "note": note, "duration_s_per_step": 4}


# ------------------------------------------------------------------- the lab
class PeripheralLab:
    def __init__(self, conn, clock) -> None:
        self.conn = conn
        self.clock = clock

    def record(self, res: TestResult) -> TestResult:
        import json
        with self.conn:
            self.conn.execute(
                "INSERT INTO peripheral_tests(id,device,kind,at,verdict,detail_json) "
                "VALUES(?,?,?,?,?,?)",
                (res.id, res.device, res.kind, self.clock(), res.verdict,
                 json.dumps(res.as_dict(), sort_keys=True)))
        return res

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        import json
        rows = self.conn.execute(
            "SELECT id,device,kind,at,verdict,detail_json FROM peripheral_tests "
            "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "device": r[1], "kind": r[2], "at": r[3],
                 "verdict": r[4], "detail": json.loads(r[5])} for r in rows]

    def summary(self) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT verdict, COUNT(*) FROM peripheral_tests GROUP BY verdict").fetchall()
        counts = {r[0]: r[1] for r in rows}
        failing = counts.get("fail", 0)
        return {"tests_run": sum(counts.values()), "counts": counts,
                "verdict": ("No hardware faults detected." if not failing else
                            f"{failing} test(s) found a hardware fault.")}
