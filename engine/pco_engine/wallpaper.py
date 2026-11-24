"""Compose a wallpaper: a scene, an optional widget layer, and finishing.

The finishing pass is what separates a generated image from a *wallpaper*. Icons
and text sit on top of whatever is behind them, so a background that is pretty on
its own is usually unreadable with thirty shortcuts on it. Three things fix that,
and all three are cheap:

  * **vignette** — darkens the edges where the desktop's chrome lives
  * **scrim** — a soft band behind where the widget column goes, so text over a
    bright aurora is still legible
  * **safe-area** — the region Windows puts icons in (left column, and on some
    setups the whole left third) is measured and the scrim respects it

Composition is deterministic: `render(spec)` is a pure function of its dict. The
spec is the same shape as a theme file, so a wallpaper and a theme are one object
and there is no second format to keep in sync.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .canvas import Canvas
from .scenes import Palette, _mix, _scale, render as render_scene
from .widgets import LIVE_ONLY, draw_layer, from_dict, validate as validate_widget

SIZES = {
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
    "ultrawide": (3440, 1440),
    "portrait": (1080, 1920),
    "laptop": (2560, 1600),
}


@dataclass
class WallpaperSpec:
    scene: str = "aurora"
    size: str = "1440p"
    seed: int = 7
    palette: Palette = None            # type: ignore[assignment]
    widgets: list[dict[str, Any]] = None       # type: ignore[assignment]
    vignette: float = 0.55
    scrim: bool = True
    icon_column: float = 0.28          # fraction of width kept clear for icons

    def resolved(self) -> "WallpaperSpec":
        return WallpaperSpec(
            scene=self.scene, size=self.size, seed=self.seed,
            palette=self.palette or Palette(),
            widgets=list(self.widgets or []),
            vignette=self.vignette, scrim=self.scrim,
            icon_column=self.icon_column)

    def dimensions(self) -> tuple[int, int]:
        """Named size or "1280x720". A silent fallback to 1440p here once made
        every gallery tile identical and 1.1 MB, which is how this was found:
        an unknown size must be an error, not a default, because the caller
        cannot tell the difference from the output."""
        if self.size in SIZES:
            return SIZES[self.size]
        if "x" in str(self.size):
            try:
                w, h = str(self.size).split("x", 1)
                wi, hi = int(w), int(h)
                if 32 <= wi <= 7680 and 32 <= hi <= 4320:
                    return (wi, hi)
            except ValueError:
                pass
        raise ValueError(f"unknown size {self.size!r}; use one of "
                         f"{', '.join(SIZES)} or WxH")


def compose(spec: dict[str, Any] | WallpaperSpec) -> Canvas:
    """Render a wallpaper spec to a Canvas."""
    if isinstance(spec, dict):
        palette = spec.get("palette") or {}
        spec = WallpaperSpec(
            scene=spec.get("scene", "aurora"),
            size=spec.get("size", "1440p"),
            seed=int(spec.get("seed", 7)),
            palette=(Palette.from_hex(**palette) if isinstance(palette, dict)
                     and palette else Palette()),
            widgets=list(spec.get("widgets") or []),
            vignette=float(spec.get("vignette", 0.55)),
            scrim=bool(spec.get("scrim", True)),
            icon_column=float(spec.get("icon_column", 0.28)),
        )
    spec = spec.resolved()
    w, h = spec.dimensions()
    c = render_scene(spec.scene, w, h, spec.palette, seed=spec.seed)

    if spec.scrim and spec.widgets:
        _scrim(c, spec)

    if spec.widgets:
        draw_layer(c, spec.widgets, spec.palette)

    if spec.vignette > 0:
        _vignette(c, spec.vignette)

    return c


def _scrim(c: Canvas, spec: WallpaperSpec) -> None:
    """A soft dark band on the side the widgets are on.

    Placement is derived from the widgets themselves: whatever side of centre
    holds more of them gets the scrim. A fixed left-hand scrim is the common
    case, and it is wrong for every theme that puts its widgets on the right.
    """
    if not spec.widgets:
        return
    left = sum(1 for wdg in spec.widgets if float(wdg.get("x", 0.5)) < 0.5)
    right = len(spec.widgets) - left
    on_left = left >= right
    band = int(c.w * max(spec.icon_column, 0.22))
    for i in range(band):
        # Strong at the edge, gone by the end of the band, and squared so the
        # falloff is invisible rather than a visible ramp.
        t = 1.0 - i / band
        alpha = int(52 * t * t)
        if alpha <= 0:
            continue
        x = i if on_left else c.w - 1 - i
        c.rect(x, 0, 1, c.h, (*_scale(spec.palette.bg, 0.02), alpha))


def _vignette(c: Canvas, strength: float) -> None:
    """Darken the corners. Integer maths in a precomputed mask, applied in place.

    Doing this per-pixel with a sqrt would triple the compose time; a separable
    square-distance weight is visually identical at wallpaper scale.
    """
    cx, cy = c.w / 2.0, c.h / 2.0
    maxd2 = cx * cx + cy * cy
    b = c.buf
    rowmul = [((y - cy) ** 2) for y in range(c.h)]
    colmul = [((x - cx) ** 2) for x in range(c.w)]
    s = max(0.0, min(1.5, strength))
    lut = [0] * 1024
    for i in range(1024):
        t = i / 1023.0
        lut[i] = int(255 * (1.0 - s * t * 0.55))
    for y in range(c.h):
        base = y * c.w * 4
        ry = rowmul[y]
        for x in range(c.w):
            d2 = (ry + colmul[x]) / maxd2
            k = lut[int(d2 * 1023.0)] if d2 < 1.0 else lut[1023]
            o = base + x * 4
            b[o] = (b[o] * k) // 255
            b[o + 1] = (b[o + 1] * k) // 255
            b[o + 2] = (b[o + 2] * k) // 255


def problems(spec: dict[str, Any]) -> list[str]:
    """Everything wrong with a wallpaper spec, before anything renders."""
    out: list[str] = []
    from .scenes import SCENES
    if spec.get("scene", "aurora") not in SCENES:
        out.append(f"unknown scene; try one of {', '.join(SCENES)}")
    if spec.get("size", "1440p") not in SIZES:
        out.append(f"unknown size; try one of {', '.join(SIZES)}")
    for i, wdg in enumerate(spec.get("widgets") or []):
        for p in validate_widget(wdg):
            out.append(f"widget {i}: {p}")
    return out


def live_warnings(spec: dict[str, Any]) -> list[str]:
    """Widgets whose value is a snapshot of something that moves.

    A clock baked into a still image is wrong within a minute. Rather than
    silently dropping it (the user asked for a clock and would see none) or
    silently baking it (they would see a stopped clock), the caller gets a
    warning it can show.
    """
    out = []
    for wdg in spec.get("widgets") or []:
        if wdg.get("type") in LIVE_ONLY:
            out.append(f"{wdg['type']} shows a snapshot in a still wallpaper — "
                       f"it will be stale until the image is re-rendered")
    return out
