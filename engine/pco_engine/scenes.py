"""Wallpaper scenes: the things the canvas is for.

Each scene is a pure function of (size, palette, seed) so the same theme file
always produces the same image — a wallpaper you cannot reproduce is a wallpaper
you cannot version, and the theme format promises to be versionable.

Eight scenes, deliberately different in structure rather than in hue. A "wallpaper
engine" that offers twelve recolourings of one gradient is a colour picker:

    aurora      vertical light curtains, additive
    mesh        soft multi-point colour field — the modern marketing gradient
    synth       horizon grid with a sun disk; the retro look, drawn not traced
    topo        contour lines from a scalar field — looks hand-drawn, is maths
    grain       flat colour + photographed-looking film grain
    loft        diagonal bands, hard edges, Bauhaus-ish
    orbit       concentric rings with a bright core
    dusk        banded sky gradient with a sun and a silhouetted horizon

Everything is deterministic from `seed`. Nothing samples the clock, the network,
or the machine's state: a wallpaper that changes when you re-render it is a bug,
not a feature (the animated version is a separate, later pass).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .canvas import Canvas, RGB


@dataclass(frozen=True)
class Palette:
    """Three colours and a background. The same shape a theme file stores."""

    bg: RGB = (0x0B, 0x0D, 0x10)
    accent: RGB = (0xB6, 0xF2, 0x4A)
    secondary: RGB = (0x5A, 0x64, 0x78)
    tint: RGB = (0x2A, 0x3A, 0x55)

    @classmethod
    def from_hex(cls, **kw: str) -> "Palette":
        def parse(h: str) -> RGB:
            h = h.lstrip("#")
            if len(h) != 6:
                raise ValueError(f"expected #rrggbb, got #{h}")
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        base = cls()
        return cls(**{k: (parse(v) if isinstance(v, str) else v)
                      for k, v in kw.items()}) if kw else base


SCENES = ("aurora", "mesh", "synth", "topo", "grain", "loft", "orbit", "dusk")


# --------------------------------------------------------------------- helpers
def _mix(a: RGB, b: RGB, t: float) -> RGB:
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def _scale(c: RGB, k: float) -> RGB:
    return (min(255, int(c[0] * k)), min(255, int(c[1] * k)), min(255, int(c[2] * k)))


def _value_noise(w: int, h: int, rng: random.Random, cells: int,
                 octaves: int = 1) -> list[float]:
    """Fractal value noise, `cells` lattice points across the long edge.

    The argument is a *cell count*, not a cell size, because that is what makes a
    scene look right: topo needs ~6 cells across the frame, grain needs ~40, and
    passing pixels to this function is how you get the confetti that shipped in
    the first draft. Multiple octaves add detail without moving the large shapes.
    """
    out = [0.0] * (w * h)
    amp_total = 0.0
    amp = 1.0
    for o in range(max(1, octaves)):
        n = max(2, cells * (2 ** o))
        cell = max(1, w // max(1, n))
        gw, gh = w // cell + 2, h // cell + 2
        grid = [[rng.random() for _ in range(gw)] for _ in range(gh)]
        amp_total += amp
        for y in range(h):
            gy, fy = divmod(y, cell)
            sy = fy / cell
            row0, row1 = grid[gy], grid[gy + 1]
            uy = sy * sy * (3 - 2 * sy)
            base = y * w
            for x in range(w):
                gx, fx = divmod(x, cell)
                sx = fx / cell
                ux = sx * sx * (3 - 2 * sx)
                a = row0[gx] + (row0[gx + 1] - row0[gx]) * ux
                b = row1[gx] + (row1[gx + 1] - row1[gx]) * ux
                out[base + x] += (a + (b - a) * uy) * amp
        amp *= 0.5
    inv = 1.0 / (amp_total or 1.0)
    return [v * inv for v in out]


# --------------------------------------------------------------------- scenes
def aurora(w: int, h: int, p: Palette, seed: int = 7) -> Canvas:
    """Aurora curtains.

    Three drafts went into this, and each failure named the rule:
      1. soft radial glows      -> fog. Light needs an edge.
      2. a bright lower bound   -> a blob. Curtains are many thin things.
      3. a 4 px block lattice   -> a visible grid of dots, and too dim.
    The shape that works is a per-row horizontal gaussian *drawn as spans*, at
    full resolution, additive, with the ray's brightness decaying upward from a
    bright lower edge. Narrow rays, one bright edge, additive blending.
    """
    rng = random.Random(seed)
    c = Canvas(w, h)
    c.linear(_scale(p.bg, 0.45), _mix(p.bg, p.tint, 0.30), angle=180)
    c.glow(w * 0.5, h * 1.06, w * 0.6, p.tint, strength=0.11)

    # Brightness accumulates here, then gets written once. Drawing 40 additive
    # glows directly would band badly in 8-bit.
    acc = [0.0] * (w * h)

    curtains = rng.randint(3, 4)
    for ci in range(curtains):
        jr = random.Random(seed * 7919 + ci * 977)
        base = jr.uniform(0.12, 0.88) * w
        spread = jr.uniform(0.12, 0.26) * w
        top = jr.uniform(0.00, 0.22) * h
        bottom = jr.uniform(0.66, 0.98) * h
        rays = jr.randint(18, 30)
        for ri in range(rays):
            rr = random.Random(seed * 131 + ci * 7919 + ri * 37)
            t = ri / max(1, rays - 1) - 0.5
            # Rays fan out at the bottom of the curtain, converge at the top —
            # that is what gives an aurora its pleated look.
            x_base = base + t * 2 * spread
            # Only slight convergence. At 0.3 the rays merged into a spotlight:
            # narrow rays overlap, additive light piles up, and the top of the
            # frame blows out to white regardless of the brightness curve.
            x_top = base + t * 2 * spread * rr.uniform(0.72, 0.94)
            width = rr.uniform(3.0, 9.0) * max(1.0, w / 1600)
            strength = rr.uniform(0.5, 1.0) * (1.0 - 0.5 * abs(t * 2))
            r_top = top + rr.uniform(0.0, 0.18) * h
            r_bot = bottom - rr.uniform(0.0, 0.28) * h
            if r_bot <= r_top:
                continue
            for y in range(int(r_top), int(min(h, r_bot)) + 1):
                ty = (y - r_top) / (r_bot - r_top)
                # Bright at the bottom edge, thin upward. The (1-ty)^2 term is
                # the "falling light"; the sin term puts the hot edge low.
                v = ((1.0 - ty) ** 2.2
                     * (0.25 + 0.75 * max(0.0, math.sin(math.pi * min(1.0, ty * 1.1))) ** 0.5))
                if v <= 0.01:
                    continue
                x0 = x_top + (x_base - x_top) * ty
                amp = v * strength * 0.85
                lo = max(0, int(x0 - width * 3.2))
                hi = min(w, int(x0 + width * 3.2) + 1)
                row = y * w
                inv = 1.0 / (width * width)
                for x in range(lo, hi):
                    d = x - x0
                    g = math.exp(-d * d * inv * 1.4) * amp
                    if g > 0.004:
                        acc[row + x] += g

    # Composite additively, shifting toward white as intensity rises.
    b = c.buf
    hot = _mix(p.accent, (255, 255, 255), 0.35)
    for i, v in enumerate(acc):
        if v <= 0.004:
            continue
        k = v if v < 1.0 else 1.0
        o = i * 4
        # Soft knee: 1-exp(-v) compresses the overlap without a hard clip, so
        # the brightest part of a curtain stays a colour instead of turning white.
        k = 1.0 - math.exp(-v * 1.55)
        b[o] = min(255, int(b[o] + hot[0] * k * 0.80))
        b[o + 1] = min(255, int(b[o + 1] + hot[1] * k * 0.80))
        b[o + 2] = min(255, int(b[o + 2] + hot[2] * k * 0.80))

    # Stars, only where the sky is dark, so they do not sit on top of a curtain.
    star = random.Random(seed + 991)
    for _ in range(max(70, w * h // 9000)):
        # max(1, ...) because randrange(0) raises: at h=1 the "sky" is zero rows,
        # and a starfield over zero rows is not a starfield, it is a crash.
        sx, sy = star.randrange(w), star.randrange(max(1, int(h * 0.60)))
        if acc[sy * w + sx] > 0.02:
            continue
        bright = star.randint(55, 200)
        c.set(sx, sy, (bright, bright, min(255, bright + 25), 255))
    return c


def mesh(w: int, h: int, p: Palette, seed: int = 11) -> Canvas:
    """A four-pole colour field.

    Inverse-distance weighting with a normalised sum flattens every colour toward
    grey, which is what the first version did — a mesh gradient has *saturated
    pockets* and a neutral valley between them. Using a sharper falloff (power 3)
    and letting the weights sum to whatever they sum to (clamped, not normalised)
    keeps the poles reading as colour while the gaps stay dark.
    """
    rng = random.Random(seed)
    c = Canvas(w, h, (*_scale(p.bg, 0.85), 255))

    poles = [
        (rng.uniform(0.10, 0.34) * w, rng.uniform(0.08, 0.34) * h,
         _mix(p.accent, (255, 255, 255), 0.10), 1.00),
        (rng.uniform(0.66, 0.92) * w, rng.uniform(0.04, 0.30) * h,
         _mix(p.secondary, p.tint, 0.45), 0.85),
        (rng.uniform(0.58, 0.92) * w, rng.uniform(0.62, 0.94) * h,
         _mix(p.tint, p.accent, 0.30), 0.80),
        (rng.uniform(0.08, 0.42) * w, rng.uniform(0.68, 0.98) * h,
         _mix(p.accent, p.secondary, 0.55), 0.65),
    ]
    scale = max(w, h) * 0.33
    b = c.buf
    for y in range(h):
        row = y * w
        for x in range(w):
            r = g = bl = 0.0
            for (cx, cy, col, weight) in poles:
                dx, dy = (x - cx) / scale, (y - cy) / scale
                d2 = dx * dx + dy * dy
                # Normalised so a pole contributes 1.0, not 16.7 — the first
                # version's 1/0.06 saturation is why the whole frame went white.
                k = weight / (1.0 + d2 * 3.0) ** 2.2
                r += col[0] * k
                g += col[1] * k
                bl += col[2] * k
            # A soft knee per channel: saturated pockets, no clipping to white,
            # and the gaps between poles fall back to the background.
            o = (row + x) * 4
            b[o] = min(255, int(255 * (1.0 - math.exp(-(r * 0.85) / 255)) * 0.94))
            b[o + 1] = min(255, int(255 * (1.0 - math.exp(-(g * 0.85) / 255)) * 0.94))
            b[o + 2] = min(255, int(255 * (1.0 - math.exp(-(bl * 0.85) / 255)) * 0.94))
            b[o + 3] = 255
    c._dither()
    return c


def _upscale(src: Canvas, w: int, h: int) -> Canvas:
    out = Canvas(w, h)
    sw, sh = src.w, src.h
    sb = src.buf
    ob = out.buf
    xmap = [min(sw - 1, (x * sw) // w) * 4 for x in range(w)]
    for y in range(h):
        sy = min(sh - 1, (y * sh) // h)
        srow = sy * sw * 4
        orow = y * w * 4
        for x in range(w):
            si = srow + xmap[x]
            oi = orow + x * 4
            ob[oi:oi + 4] = sb[si:si + 4]
    return out


def synth(w: int, h: int, p: Palette, seed: int = 3) -> Canvas:
    """Horizon grid. The retro look: a sun, a vanishing-point grid, hard lines."""
    c = Canvas(w, h)
    horizon = max(1, min(h - 1, int(h * 0.58)))
    # Sky
    sky = Canvas(w, max(1, horizon))
    sky.linear(_scale(p.bg, 0.35), _mix(p.bg, p.accent, 0.22), angle=180)
    sky.glow(w * 0.5, horizon * 0.92, w * 0.42, p.accent, strength=0.16)
    # Blit sky
    c.buf[0:horizon * w * 4] = sky.buf[0:horizon * w * 4]

    # Sun: a disk with bands cut out of the bottom half (the classic look).
    cx, cy, r = w * 0.5, horizon - h * 0.10, min(w, h) * 0.20
    for yy in range(int(cy - r), int(cy + r)):
        if not (0 <= yy < horizon):
            continue
        dy = yy - cy
        half = math.sqrt(max(0.0, r * r - dy * dy))
        if dy > r * 0.15:                       # bands across the lower part
            band = int((dy - r * 0.15) / (r * 0.10)) % 2
            if band:
                continue
        shade = 0.55 + 0.45 * (1 - max(0.0, dy) / r)
        col = _mix(p.accent, (255, 255, 255), 0.25 * shade)
        c.rect(int(cx - half), yy, int(half * 2), 1, (*col, 255))

    # Floor
    floor = Canvas(w, max(1, h - horizon))
    floor.linear(_scale(p.bg, 1.6), _scale(p.bg, 0.5), angle=180)
    c.buf[horizon * w * 4:] = floor.buf

    # Grid: vertical lines converging, horizontal lines accelerating away.
    vx, vy = w * 0.5, horizon
    for i in range(-14, 15):
        x_end = vx + i * (w / 12)
        c.line(vx, vy, x_end, h, (*_mix(p.accent, p.bg, 0.55), 150), width=2)
    depth = 1.0
    y = vy
    while y < h - 2:
        c.line(0, y, w, y, (*_mix(p.accent, p.bg, 0.6), int(160 * (1 - (y - vy) / h))),
               width=2)
        depth *= 0.72
        y += max(6.0, (h - vy) * (1 - depth) * 0.18) + 4
    return c


def topo(w: int, h: int, p: Palette, seed: int = 21) -> Canvas:
    """Contour lines from a fractal noise field.

    The first draft passed a *pixel* size to the noise, which produced the
    confetti in the screenshot: every band boundary was a few pixels wide, and a
    scalar field sampled at that frequency has no continuous contour to draw.
    Real topographic lines come from a field with 4-8 features across the frame,
    so that is what the cell count says, and two octaves add the fine wiggle
    without moving the shapes.
    """
    rng = random.Random(seed)
    c = Canvas(w, h)
    c.linear(_mix(p.bg, p.tint, 0.18), _scale(p.bg, 0.7), angle=140)
    field = _value_noise(w, h, rng, cells=5, octaves=3)
    b = c.buf
    line = _mix(p.accent, p.bg, 0.20)
    # 22 bands across the field. Band width in field units, so the line width
    # stays constant regardless of resolution.
    bands = 22.0
    for i in range(w * h):
        v = field[i] * bands
        frac = v - int(v)
        if frac < 0.06:
            k = 0.30 + 0.70 * (1.0 - frac / 0.06)
            o = i * 4
            b[o] = min(255, int(b[o] + line[0] * k * 0.7))
            b[o + 1] = min(255, int(b[o + 1] + line[1] * k * 0.7))
            b[o + 2] = min(255, int(b[o + 2] + line[2] * k * 0.7))
    return c


def grain(w: int, h: int, p: Palette, seed: int = 5) -> Canvas:
    """Flat colour with film grain. The quiet option: survives being behind
    icons, and costs nothing on the GPU because there is no GPU."""
    rng = random.Random(seed)
    c = Canvas(w, h)
    c.linear(_scale(p.bg, 1.25), _scale(p.bg, 0.8), angle=165)
    b = c.buf
    # Base grain at full resolution, then a coarse blotch layer.
    for i in range(0, w * h * 4, 4):
        n = rng.randint(-7, 7)
        o = i
        b[o] = max(0, min(255, b[o] + n))
        b[o + 1] = max(0, min(255, b[o + 1] + n))
        b[o + 2] = max(0, min(255, b[o + 2] + n))
    blotch = _value_noise(w, h, rng, cells=14, octaves=2)
    tint = _mix(p.bg, p.tint, 0.5)
    for i in range(w * h):
        k = (blotch[i] - 0.5) * 0.30
        o = i * 4
        b[o] = max(0, min(255, int(b[o] + tint[0] * k)))
        b[o + 1] = max(0, min(255, int(b[o + 1] + tint[1] * k)))
        b[o + 2] = max(0, min(255, int(b[o + 2] + tint[2] * k)))
    return c


def loft(w: int, h: int, p: Palette, seed: int = 13) -> Canvas:
    """Hard-edged diagonal bands.

    Filled column-first, not row-first: a band edge at 34 degrees is almost
    vertical, so scanning rows means computing y-intercepts that go to infinity
    and leaving a quad uncut in the corner — which is exactly what the first
    version did. Columns make the same geometry trivially bounded.
    """
    rng = random.Random(seed)
    c = Canvas(w, h, (*p.bg, 255))
    angle = math.radians(rng.choice((26, 32, 38, -32)))
    dx, dy = math.cos(angle), math.sin(angle)
    # Projection along the band direction's normal.
    nx, ny = -dy, dx
    proj = [x * nx + y * ny for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    lo, hi = min(proj), max(proj)
    span = hi - lo
    bands = rng.randint(5, 7)
    palette = [
        _scale(p.bg, 0.75),
        _mix(p.bg, p.tint, 0.75),
        _mix(p.bg, p.tint, 0.30),
        _scale(p.bg, 0.45),
        _mix(p.accent, p.bg, 0.86),
    ]
    edges = [0.0] + sorted(rng.uniform(0.06, 0.94) for _ in range(bands - 1)) + [1.0]
    colour_of = [(*palette[i % len(palette)], 255) for i in range(len(edges) - 1)]

    for x in range(w):
        for bi in range(len(edges) - 1):
            a0 = lo + span * edges[bi]
            a1 = lo + span * edges[bi + 1]
            # y where this column crosses the two band edges
            if abs(ny) < 1e-6:
                y0, y1 = 0, h
            else:
                y0 = (a0 - x * nx) / ny
                y1 = (a1 - x * nx) / ny
                if y0 > y1:
                    y0, y1 = y1, y0
                y0 = max(0, int(y0))
                y1 = min(h, int(y1) + 1)
            if y1 > y0:
                c.rect(x, y0, 1, y1 - y0, colour_of[bi])

    # One accent hairline on a chosen cut.
    cut = edges[len(edges) // 2]
    a = lo + span * cut
    if abs(ny) > 1e-6:
        for x in range(w):
            y = int((a - x * nx) / ny)
            c.rect(x, y, 1, 3, (*p.accent, 225))
    return c


def orbit(w: int, h: int, p: Palette, seed: int = 17) -> Canvas:
    """Concentric rings with a hot core. Draws over a radial base."""
    rng = random.Random(seed)
    c = Canvas(w, h)
    cx, cy = w * rng.uniform(0.35, 0.65), h * rng.uniform(0.35, 0.62)
    c.radial((int(cx), int(cy)), max(w, h) * 0.75,
             _mix(p.bg, p.tint, 0.7), _scale(p.bg, 0.5))
    rings = rng.randint(6, 11)
    base_r = min(w, h) * 0.06
    for i in range(rings):
        r = base_r * (1.42 ** i)
        if r > max(w, h):
            break
        fade = max(0.08, 1.0 - r / (max(w, h) * 0.95))
        col = _mix(p.accent, p.secondary, i / max(1, rings - 1))
        c.circle(cx, cy, r, (*col, int(170 * fade)),
                 thickness=max(1.0, 2.5 + 5 * fade))
    c.circle(cx, cy, base_r * 0.55, (*p.accent, 235))
    c.glow(cx, cy, base_r * 5, p.accent, strength=0.10)
    return c


def dusk(w: int, h: int, p: Palette, seed: int = 31) -> Canvas:
    """Banded sky with a sun and a dark ridge. A landscape with no photograph."""
    rng = random.Random(seed)
    c = Canvas(w, h)
    horizon = max(1, min(h - 1, int(h * rng.uniform(0.60, 0.72))))
    sky = Canvas(w, horizon)
    top = _scale(p.bg, 0.45)
    mid = _mix(p.bg, p.tint, 0.85)
    low = _mix(p.accent, (255, 190, 120), 0.55)
    sky.linear(top, mid, angle=180)
    # Warm glow near the horizon, the thing that makes it read as dusk.
    glow = Canvas(w, horizon)
    glow.linear(mid, low, angle=180)
    c.buf[0:horizon * w * 4] = sky.buf[0:horizon * w * 4]
    # Blend the lower third toward the warm colour.
    start = int(horizon * 0.62)
    gb = glow.buf
    cb = c.buf
    for y in range(start, horizon):
        t = (y - start) / max(1, horizon - start)
        t = t * t
        row = y * w * 4
        for x in range(w):
            o = row + x * 4
            cb[o] = int(cb[o] + (gb[o] - cb[o]) * t)
            cb[o + 1] = int(cb[o + 1] + (gb[o + 1] - cb[o + 1]) * t)
            cb[o + 2] = int(cb[o + 2] + (gb[o + 2] - cb[o + 2]) * t)

    sun_x, sun_y, sun_r = w * rng.uniform(0.25, 0.75), horizon * 0.86, min(w, h) * 0.075
    c.glow(sun_x, sun_y, sun_r * 7, (255, 214, 150), strength=0.09)
    c.circle(sun_x, sun_y, sun_r, (255, 236, 200, 245))

    # Ridge: noise sampled along x, filled down to the horizon.
    noise = _value_noise(w, 8, rng, cells=9, octaves=2)
    ridge = [horizon - int(noise[x] * h * 0.10) for x in range(w)]
    for x in range(w):
        c.rect(x, ridge[x], 1, horizon - ridge[x] + 1, (*_scale(p.bg, 0.35), 255))
    # Foreground: flat and darker.
    c.rect(0, horizon, w, h - horizon, (*_scale(p.bg, 0.5), 255))
    return c


SCENE_FUNCS = {
    "aurora": aurora, "mesh": mesh, "synth": synth, "topo": topo,
    "grain": grain, "loft": loft, "orbit": orbit, "dusk": dusk,
}


def render(scene: str, w: int, h: int, palette: Palette | None = None,
           *, seed: int = 7) -> Canvas:
    """Render a named scene. Unknown names raise with the list, not a blank image."""
    fn = SCENE_FUNCS.get(scene)
    if fn is None:
        raise ValueError(f"unknown scene {scene!r}; try one of {', '.join(SCENES)}")
    return fn(w, h, palette or Palette(), seed)
