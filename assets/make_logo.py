"""Renders the Keel logo to PNG and ICO with no third-party libraries.

Why hand-rolled: the build must not depend on Pillow, Cairo or an SVG renderer, and
an icon is the one asset that has to exist before the first line of packaging runs.
Everything here is stdlib (`zlib`, `struct`).

The mark
--------
A **keel** is the fin under a hull: invisible from deck, and the only reason the
boat does not capsize. That is the promise of this app — the safety core is the
part you never see, and it is the reason you can undo anything.

Drawn as: an ink rounded-square, a lime keel blade hanging below a light
waterline, and the blade's leading edge cut as a single arc. Two shapes, one
idea. At 16 px the blade and the line still read; the wordmark is dropped.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

# ------------------------------------------------------------------ palette
INK = (11, 13, 16, 255)          # --bg from the console, so the app is one object
LIME = (182, 242, 74, 255)       # the accent the console already uses
MIST = (90, 100, 120, 255)       # the waterline: quiet, structural
CLEAR = (0, 0, 0, 0)


# ------------------------------------------------------------------ geometry
def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 48):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
            for i in range(n + 1)]


def _rounded_rect(x: float, y: float, w: float, h: float, r: float):
    """A rounded square as a closed polygon, in 0..1 space."""
    pts = []
    pts += _arc(x + w - r, y + r, r, -90, 0)
    pts += _arc(x + w - r, y + h - r, r, 0, 90)
    pts += _arc(x + r, y + h - r, r, 90, 180)
    pts += _arc(x + r, y + r, r, 180, 270)
    return pts


def keel_blade() -> list[tuple[float, float]]:
    """A swept centreboard, cut as one closed curve.

    An earlier version was a symmetric taper, which at 32 px reads as a pen nib
    or a thermometer. A real keel is *asymmetric*: it attaches under the hull,
    sweeps aft, and ends in a point. That asymmetry is what makes the silhouette
    unmistakable at small sizes.
    """
    # attach under the hull, sweep down-and-right, taper to a point
    root_l, root_r = 0.395, 0.565
    tip = (0.735, 0.855)
    pts = []
    # leading edge: root-left → tip, a long shallow sweep
    for i in range(41):
        t = i / 40
        # ease so the curve is gentle at the root and tight at the tip
        e = t ** 1.35
        x = root_l + (tip[0] - root_l) * e
        y = 0.455 + (tip[1] - 0.455) * t
        pts.append((x, y))
    # trailing edge: tip → root-right, bowing inward so the fin has a belly
    for i in range(41):
        t = i / 40
        x = tip[0] + (root_r - tip[0]) * t
        y = tip[1] + (0.455 - tip[1]) * t
        belly = 0.030 * math.sin(math.pi * t)
        pts.append((x - belly, y))
    return pts


def hull_line() -> list[tuple[float, float]]:
    """The waterline: two strokes that stop short of the blade, so they read as
    water parting around it rather than as one bar through it."""
    y, h = 0.432, 0.030
    # The gap follows the fin's *root*, not its centre: the water parts where the
    # blade attaches, which is what sells the metaphor at any size.
    left_end, right_start = 0.365, 0.595
    return [
        [(0.115, y), (left_end, y), (left_end, y + h), (0.115, y + h)],
        [(right_start, y), (0.885, y), (0.885, y + h), (right_start, y + h)],
    ]


# ------------------------------------------------------------------ raster
def _inside(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    """Even-odd scanline test. Shapes here are small; clarity beats speed."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xin = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xin:
                inside = not inside
    return inside


def render(size: int, *, background: bool = True, ss: int = 4) -> bytes:
    """RGBA pixels, supersampled ss× and box-filtered down.

    Supersampling is the difference between an icon that looks cheap and one that
    looks drawn — 4× is enough at 16 px and cheap enough at 256.
    """
    w = h = size * ss
    buf = [[(0, 0, 0, 0)] * w for _ in range(h)]

    shapes: list[tuple[list[tuple[float, float]], tuple[int, int, int, int]]] = []
    if background:
        shapes.append((_rounded_rect(0.02, 0.02, 0.96, 0.96, 0.225), INK))
    shapes.append((keel_blade(), LIME))
    for stroke in hull_line():
        shapes.append((stroke, MIST if background else INK))

    for poly, color in shapes:
        # Scale ONCE, into pixel space. The first version scaled the bounds but
        # then passed normalised coordinates to the point-in-polygon test, so
        # every pixel failed the test and the icon rendered blank.
        poly = [(px * w, py * h) for px, py in poly]
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        y0, y1 = max(0, int(min(ys))), min(h, int(max(ys)) + 1)
        x0, x1 = max(0, int(min(xs))), min(w, int(max(xs)) + 1)
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                if _inside(xx + 0.5, yy + 0.5, poly):
                    buf[yy][xx] = color

    # box filter
    out = bytearray()
    for y in range(size):
        out.append(0)                       # PNG filter: none
        for x in range(size):
            acc = [0, 0, 0, 0]
            for dy in range(ss):
                for dx in range(ss):
                    r, g, b, a = buf[y * ss + dy][x * ss + dx]
                    acc[0] += r
                    acc[1] += g
                    acc[2] += b
                    acc[3] += a
            n = ss * ss
            out.extend(tuple(v // n for v in acc))
    return bytes(out)


def png(size: int, *, background: bool = True) -> bytes:
    raw = render(size, background=background)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)   # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    """A multi-resolution .ico. Windows Vista and later accept PNG-compressed
    entries for every size, which keeps this function small and honest."""
    images = [(s, png(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory, blob = b"", b""
    for size, data in images:
        directory += struct.pack("<BBBBHHII",
                                 0 if size >= 256 else size,   # 256 is stored as 0
                                 0 if size >= 256 else size,
                                 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blob += data
    return header + directory + blob


def write_all(out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "keel-256.png": png(256),
        "keel-128.png": png(128),
        "keel-64.png": png(64),
        "keel-32.png": png(32),
        "keel-16.png": png(16),
        "keel-mark-clear.png": png(256, background=False),
        "keel.ico": ico(),
    }
    written = {}
    for name, data in files.items():
        (out / name).write_bytes(data)
        written[name] = len(data)
    return written


if __name__ == "__main__":
    target = Path(__file__).resolve().parent
    for name, size in write_all(target).items():
        print(f"  {name:<24} {size:>7,} bytes")
