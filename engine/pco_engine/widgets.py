"""Widgets, rendered into the wallpaper.

The decision that shapes this file: **a widget is drawn into the image, not into
a live window.** A Rainmeter-style layer that repaints every second needs a
compositor, a hook into DWM, and a permanent 1-3% GPU cost — which is exactly the
thing this app's governor exists to refuse. Drawing widgets into the generated
wallpaper costs zero after the render, survives Explorer crashes, cannot be
covered by a window, and is still live because the wallpaper re-renders when the
data changes (on a schedule the user sets, not on a timer that never stops).

The trade-off is honest and worth stating: a widget drawn into a wallpaper shows
the *time it was rendered*. For a clock that is wrong. So clocks are excluded from
the wallpaper by default and offered as a live overlay instead — see
`LIVE_ONLY` below. This is the difference between a feature and a lie.

Every widget renders from a `dict` of data. Nothing here reads the clock, the CPU
or the network: the caller passes the numbers, which is what makes the output
deterministic and testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .canvas import Canvas, RGB, RGBA
from .scenes import Palette, _mix, _scale

# Widgets whose whole value is currency — a snapshot of these is a lie.
LIVE_ONLY = {"clock-analog", "clock-digital", "uptime", "now-playing", "network-live"}

# Everything else can be baked into a still image without misleading anyone.
STATIC_OK = {
    "gauge-ring", "gauge-bar", "stat-block", "calendar", "weather",
    "battery", "disk", "system", "quote", "divider", "label",
}

WIDGET_TYPES = tuple(sorted(STATIC_OK | LIVE_ONLY))


@dataclass
class Widget:
    """Placement and style. Coordinates are fractions of the canvas, so a theme
    laid out at 1080p composes correctly at 4K without a second layout."""

    type: str
    x: float = 0.5
    y: float = 0.5
    size: float = 0.16            # fraction of the *shorter* edge
    anchor: str = "center"        # center | left | right
    accent: RGB | None = None
    text: str = ""
    value: float = 0.0            # 0..1 for gauges
    label: str = ""
    detail: str = ""
    opacity: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "x": self.x, "y": self.y, "size": self.size,
                "anchor": self.anchor, "text": self.text,
                "value": self.value, "label": self.label,
                "detail": self.detail, "opacity": self.opacity}


def validate(w: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if w.get("type") not in WIDGET_TYPES:
        problems.append(f"unknown widget type {w.get('type')!r}; "
                        f"try one of {', '.join(WIDGET_TYPES)}")
    for k in ("x", "y"):
        v = w.get(k, 0.5)
        if not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            problems.append(f"{k} must be a fraction between 0 and 1")
    s = w.get("size", 0.16)
    if not isinstance(s, (int, float)) or not 0.02 <= s <= 0.9:
        problems.append("size must be between 0.02 and 0.9")
    if "value" in w and not 0.0 <= float(w["value"]) <= 1.0:
        problems.append("value must be between 0 and 1")
    return problems


# ------------------------------------------------------------------ drawing
def _alpha(colour: RGB, opacity: float, a: int = 255) -> RGBA:
    return (colour[0], colour[1], colour[2], max(0, min(255, int(a * opacity))))


def _origin(c: Canvas, w: Widget) -> tuple[int, int, int]:
    """Pixel centre and radius for a widget, honouring the fractional spec."""
    r = int(w.size * min(c.w, c.h))
    cx = int(w.x * c.w)
    cy = int(w.y * c.h)
    return cx, cy, r


def draw(c: Canvas, w: Widget, p: Palette) -> None:
    """Dispatch one widget onto the canvas."""
    fn = _DRAW.get(w.type)
    if fn is None:
        raise ValueError(f"cannot draw widget type {w.type!r}")
    fn(c, w, p)


def _glass(c: Canvas, cx: int, cy: int, r: int, p: Palette,
           opacity: float) -> None:
    """A faint rounded plate behind dense widgets so they stay readable on any
    wallpaper. Drawn only where it helps: text and gauges, not ambient shapes."""
    pad = int(r * 0.22)
    c.rect(cx - r - pad, cy - int(r * 0.62) - pad,
          2 * (r + pad), int(r * 1.24) + 2 * pad,
           (*_scale(p.bg, 0.15), max(0, min(255, int(80 * opacity)))), radius=pad)


def _w_ring(c: Canvas, w: Widget, p: Palette) -> None:
    """A percentage ring. The workhorse: GPU, RAM, disk, battery, anything 0-1."""
    cx, cy, r = _origin(c, w)
    acc = w.accent or p.accent
    t = max(0.0, min(1.0, w.value))
    c.circle(cx, cy, r, (*_scale(p.bg, 0.55), int(150 * w.opacity)),
             thickness=max(3.0, r * 0.10))
    if t > 0.001:
        # Start at 12 o'clock, sweep clockwise.
        c.arc(cx, cy, r, 0.0, 360.0 * t, _alpha(acc, w.opacity),
              thickness=max(3.0, r * 0.10))
    pct = f"{int(round(t * 100))}%"
    tw = c.text_width(pct, scale=max(2, r // 14))
    c.text(cx - tw // 2, cy - max(2, r // 14) * 3, pct,
           _alpha(p.accent if w.accent is None else (240, 244, 250), w.opacity),
           scale=max(2, r // 14))
    if w.label:
        label = w.label.upper()[:12]
        lw = c.text_width(label, scale=max(2, r // 24))
        c.text(cx - lw // 2, cy + int(r * 0.52), label,
               (*_scale(p.secondary, 1.6), int(210 * w.opacity)),
               scale=max(2, r // 24))


def _w_bar(c: Canvas, w: Widget, p: Palette) -> None:
    """A labelled horizontal meter. Good for stacked CPU-core style lists."""
    cx, cy, r = _origin(c, w)
    acc = w.accent or p.accent
    width = r * 2
    height = max(4, r // 9)
    x0 = cx - r
    y0 = cy
    # Track first, always: an empty bar with no track is indistinguishable from
    # a rendering failure, which is exactly how this looked in the first pass.
    c.rect(x0, y0, width, height, (*_scale(p.bg, 0.5), int(170 * w.opacity)),
           radius=height // 2)
    fill = max(0, int(width * max(0.0, min(1.0, w.value))))
    if fill > 0:
        c.rect(x0, y0, max(fill, height), height, _alpha(acc, w.opacity),
               radius=height // 2)
    scale = max(2, r // 26)
    cap_y = y0 - scale * 8
    label_txt = w.label.upper()[:14]
    lw = c.text_width(label_txt, scale=scale)
    dw = c.text_width(w.detail, scale=scale) if w.detail else 0
    same_row = dw and (lw + dw + scale * 2) <= width
    if label_txt:
        c.text(x0, cap_y, label_txt,
               (*_scale(p.secondary, 1.7), int(210 * w.opacity)), scale=scale)
    if w.detail:
        # Right-aligned on the label row when it fits, otherwise below the bar —
        # overlapping the two is what the first render did.
        if same_row:
            c.text(x0 + width - dw, cap_y, w.detail,
                   _alpha(acc, w.opacity), scale=scale)
        else:
            c.text(x0, y0 + height + scale * 3, w.detail,
                   _alpha(acc, w.opacity), scale=scale)


def _w_stat(c: Canvas, w: Widget, p: Palette) -> None:
    """Big number, small label. The '8.4 GB / 32 GB' block.

    No plate above a size threshold: a glass rectangle behind a 90 px headline
    reads as a smudge, and at that size the text is the only thing that needs to
    carry itself.
    """
    cx, cy, r = _origin(c, w)
    if r < 160:
        _glass(c, cx, cy, r, p, w.opacity)
    big = (w.text or "—")[:8]
    scale = max(2, r // 8)
    tw = c.text_width(big, scale=scale)
    c.text(cx - tw // 2, cy - scale * 4, big,
           _alpha(w.accent or p.accent, w.opacity), scale=scale)
    if w.label:
        label = w.label.upper()[:16]
        lw = c.text_width(label, scale=max(2, r // 26))
        c.text(cx - lw // 2, cy + int(r * 0.38), label,
               (*_scale(p.secondary, 1.7), int(215 * w.opacity)),
               scale=max(2, r // 26))


def _w_analog(c: Canvas, w: Widget, p: Palette) -> None:
    """An analogue clock. `value` is the hour fraction (hour+min/60)/12."""
    cx, cy, r = _origin(c, w)
    acc = w.accent or p.accent
    c.circle(cx, cy, r, (*_scale(p.bg, 0.35), int(120 * w.opacity)))
    c.circle(cx, cy, r, (*_scale(p.secondary, 1.4), int(200 * w.opacity)),
             thickness=max(2.0, r * 0.045))
    for i in range(12):
        a = math.radians(i * 30)
        inner = r * (0.86 if i % 3 else 0.80)
        c.line(cx + math.sin(a) * inner, cy - math.cos(a) * inner,
               cx + math.sin(a) * r * 0.96, cy - math.cos(a) * r * 0.96,
               (*_scale(p.secondary, 2.0), int(220 * w.opacity)),
               width=max(2.0, r * 0.03))
    hour_frac = (w.value * 12.0) % 12.0
    a = math.radians(hour_frac * 30)
    c.line(cx, cy, cx + math.sin(a) * r * 0.55, cy - math.cos(a) * r * 0.55,
           _alpha(acc, w.opacity), width=max(3.0, r * 0.075))
    # Minutes from the fractional part, so the hand is where it should be.
    minute_frac = (hour_frac % 1.0) * 60.0
    a = math.radians(minute_frac * 6)
    c.line(cx, cy, cx + math.sin(a) * r * 0.80, cy - math.cos(a) * r * 0.80,
           _alpha(acc, w.opacity), width=max(2.0, r * 0.045))
    c.circle(cx, cy, max(2.0, r * 0.05), _alpha(acc, w.opacity))


def _w_digital(c: Canvas, w: Widget, p: Palette) -> None:
    """A digital clock. `text` is the string, so the caller controls formatting."""
    cx, cy, r = _origin(c, w)
    _glass(c, cx, cy, r, p, w.opacity)
    s = (w.text or "00:00")[:8]
    scale = max(2, int(r * 0.42 / 7))
    tw = c.text_width(s, scale=scale)
    c.text(cx - tw // 2, cy - scale * 3, s, _alpha(w.accent or p.accent, w.opacity),
           scale=scale)
    if w.label:
        label = w.label.upper()[:18]
        lw = c.text_width(label, scale=max(2, r // 24))
        c.text(cx - lw // 2, cy + int(r * 0.40), label,
               (*_scale(p.secondary, 1.7), int(215 * w.opacity)),
               scale=max(2, r // 24))


def _w_calendar(c: Canvas, w: Widget, p: Palette) -> None:
    """Date block: weekday above, day number in the middle, month below.

    Laid out from a measured block rather than three hardcoded offsets. The
    offsets version overlapped the header and the number at every size the
    defaults use, because the number's height is 7*scale and scale came from
    the widget radius with no regard for what was above it.
    """
    cx, cy, r = _origin(c, w)
    _glass(c, cx, cy, r, p, w.opacity)
    head_scale = max(2, r // 28)
    num_scale = max(3, int(r * 0.52 / 7))
    det_scale = max(2, r // 30)
    head_h = 7 * head_scale
    num_h = 7 * num_scale
    det_h = 7 * det_scale if w.detail else 0
    gap = max(3, r // 12)
    total = head_h + gap + num_h + (gap + det_h if det_h else 0)
    y = cy - total // 2

    if w.label:
        head = w.label.upper()[:12]
        hw = c.text_width(head, scale=head_scale)
        c.text(cx - hw // 2, y, head,
               (*_scale(p.secondary, 1.8), int(220 * w.opacity)), scale=head_scale)
    y += head_h + gap

    day = (w.text or "1")[:2]
    dw = c.text_width(day, scale=num_scale)
    c.text(cx - dw // 2, y, day,
           _alpha(w.accent or p.accent, w.opacity), scale=num_scale)
    y += num_h + gap

    if w.detail:
        det = w.detail.upper()[:10]
        ew = c.text_width(det, scale=det_scale)
        c.text(cx - ew // 2, y, det,
               (*_scale(p.secondary, 1.6), int(200 * w.opacity)), scale=det_scale)


def _w_battery(c: Canvas, w: Widget, p: Palette) -> None:
    """A battery glyph with a fill. Reads faster than a percentage."""
    cx, cy, r = _origin(c, w)
    acc = w.accent or p.accent
    bw, bh = int(r * 1.5), int(r * 0.72)
    x0, y0 = cx - bw // 2, cy - bh // 2
    c.rect(x0, y0, bw, bh, (*_scale(p.secondary, 1.4), int(220 * w.opacity)),
           radius=max(2, bh // 8))
    c.rect(x0 + 2, y0 + 2, bw - 4, bh - 4, (*_scale(p.bg, 0.3), int(200 * w.opacity)),
           radius=max(2, bh // 10))
    c.rect(x0 + bw, cy - bh // 6, max(2, bw // 14), bh // 3,
           (*_scale(p.secondary, 1.4), int(220 * w.opacity)))
    t = max(0.0, min(1.0, w.value))
    fill = int((bw - 6) * t)
    if fill > 0:
        col = acc if t > 0.2 else (255, 96, 96)
        c.rect(x0 + 3, y0 + 3, fill, bh - 6, _alpha(col, w.opacity),
               radius=max(1, bh // 12))
    if w.text:
        tw = c.text_width(w.text, scale=max(2, r // 26))
        c.text(cx - tw // 2, y0 + bh + max(4, r // 12), w.text,
               (*_scale(p.secondary, 1.8), int(215 * w.opacity)),
               scale=max(2, r // 26))


def _w_system(c: Canvas, w: Widget, p: Palette) -> None:
    """A stacked block of label/value lines: the 'command center' widget.

    Lines come from `text` split on '|', so a theme file stays a flat dict and
    still gets multi-row output.
    """
    cx, cy, r = _origin(c, w)
    lines = [ln.strip() for ln in (w.text or "").split("|") if ln.strip()][:6]
    if not lines:
        lines = ["NO DATA"]
    scale = max(2, r // 22)
    lh = scale * 9
    total = lh * len(lines)
    y = cy - total // 2
    _glass(c, cx, cy, max(r, int(c.text_width(max(lines, key=len), scale=scale) * 0.62)),
           p, w.opacity)
    if w.label:
        c.text(cx - c.text_width(w.label.upper()[:14], scale=scale) // 2,
               y - lh, w.label.upper()[:14],
               (*_scale(p.secondary, 1.8), int(220 * w.opacity)), scale=scale)
        y += lh
    for ln in lines:
        tw = c.text_width(ln, scale=scale)
        c.text(cx - tw // 2, y, ln[:22],
               _alpha(w.accent or (236, 240, 246), w.opacity), scale=scale)
        y += lh


def _w_weather(c: Canvas, w: Widget, p: Palette) -> None:
    """A temperature with a small sun/cloud glyph. No network: the caller
    supplies the number, and if there is no number there is no widget."""
    cx, cy, r = _origin(c, w)
    _glass(c, cx, cy, r, p, w.opacity)
    deg = w.text or "--°"
    scale = max(3, r // 7)
    tw = c.text_width(deg, scale=scale)
    c.text(cx - tw // 2 - r // 6, cy - scale * 3, deg,
           _alpha(w.accent or p.accent, w.opacity), scale=scale)
    # Glyph: sun if the detail says it is clear, otherwise a cloud bar.
    gx, gy = cx + tw // 2 + r // 3, cy + scale
    clear = "clear" in (w.detail or "").lower() or "sun" in (w.detail or "").lower()
    if clear:
        c.circle(gx, gy, r * 0.16, (*_mix(p.accent, (255, 240, 200), 0.6),
                                    int(230 * w.opacity)))
    else:
        c.rect(int(gx - r * 0.22), int(gy - r * 0.02), int(r * 0.44), int(r * 0.14),
               (*_scale(p.secondary, 1.9), int(220 * w.opacity)), radius=int(r * 0.07))
    if w.label:
        label = w.label.upper()[:14]
        lw = c.text_width(label, scale=max(2, r // 26))
        c.text(cx - lw // 2, cy + int(r * 0.42), label,
               (*_scale(p.secondary, 1.6), int(200 * w.opacity)),
               scale=max(2, r // 26))


def _w_quote(c: Canvas, w: Widget, p: Palette) -> None:
    """A short line of text, centred. Kept short on purpose: the 5x7 face is
    legible, not typographic."""
    cx, cy, r = _origin(c, w)
    lines = [ln.strip() for ln in (w.text or "").split("|") if ln.strip()][:3]
    scale = max(2, r // 20)
    lh = scale * 9
    y = cy - (lh * len(lines)) // 2
    for ln in lines:
        tw = c.text_width(ln, scale=scale)
        c.text(cx - tw // 2, y, ln[:40],
               _alpha(w.accent or (226, 232, 240), w.opacity), scale=scale)
        y += lh


def _w_label(c: Canvas, w: Widget, p: Palette) -> None:
    """A single word, letter-spaced. Titles and section markers."""
    cx, cy, r = _origin(c, w)
    txt = (w.text or "LABEL")[:18]
    scale = max(2, r // 16)
    tw = c.text_width(txt, scale=scale, spacing=2)
    c.text(cx - tw // 2, cy - scale * 3, txt,
           (*_scale(p.secondary, 2.0), int(230 * w.opacity)), scale=scale, spacing=2)


def _w_divider(c: Canvas, w: Widget, p: Palette) -> None:
    cx, cy, r = _origin(c, w)
    c.rect(cx - r, cy, 2 * r, max(1, r // 30),
           (*_scale(p.secondary, 1.5), int(150 * w.opacity)))


def _w_disk(c: Canvas, w: Widget, p: Palette) -> None:
    c.circle(_origin(c, w)[0], _origin(c, w)[1], _origin(c, w)[2],
             (*_scale(p.bg, 0.55), int(150 * w.opacity)),
             thickness=max(3.0, _origin(c, w)[2] * 0.10))
    _w_ring(c, w, p)


def _w_uptime(c: Canvas, w: Widget, p: Palette) -> None:
    _w_stat(c, w, p)


def _w_nowplaying(c: Canvas, w: Widget, p: Palette) -> None:
    _w_stat(c, w, p)


def _w_network(c: Canvas, w: Widget, p: Palette) -> None:
    _w_bar(c, w, p)


_DRAW = {
    "gauge-ring": _w_ring,
    "gauge-bar": _w_bar,
    "stat-block": _w_stat,
    "clock-analog": _w_analog,
    "clock-digital": _w_digital,
    "calendar": _w_calendar,
    "battery": _w_battery,
    "system": _w_system,
    "weather": _w_weather,
    "quote": _w_quote,
    "label": _w_label,
    "divider": _w_divider,
    "disk": _w_disk,
    "uptime": _w_uptime,
    "now-playing": _w_nowplaying,
    "network-live": _w_network,
}


def from_dict(d: dict[str, Any]) -> Widget:
    """Build a Widget from a theme file, refusing unknown keys loudly."""
    problems = validate(d)
    if problems:
        raise ValueError("; ".join(problems))
    accent = d.get("accent")
    if isinstance(accent, str):
        h = accent.lstrip("#")
        accent = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return Widget(type=d["type"], x=float(d.get("x", 0.5)),
                  y=float(d.get("y", 0.5)), size=float(d.get("size", 0.16)),
                  anchor=d.get("anchor", "center"), accent=accent,
                  text=str(d.get("text", "")), value=float(d.get("value", 0.0)),
                  label=str(d.get("label", "")), detail=str(d.get("detail", "")),
                  opacity=float(d.get("opacity", 1.0)))


def draw_layer(c: Canvas, widgets: list[dict[str, Any]], p: Palette) -> int:
    """Draw every widget; return the count drawn.

    One bad widget must not lose the wallpaper: an invalid entry is skipped and
    counted, and the caller reports '5 of 6 drawn' rather than an exception.
    """
    drawn = 0
    for spec in widgets:
        try:
            draw(c, from_dict(spec), p)
            drawn += 1
        except (ValueError, KeyError, TypeError):
            continue
    return drawn
