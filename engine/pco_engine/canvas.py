"""A small raster canvas, in the standard library only.

The app ships as one 12 MB exe with no Pillow, no numpy and no GPU. That is a
constraint, not an excuse: a wallpaper engine that needs a 40 MB image library to
draw a gradient is not a wallpaper engine, it is a dependency.

What this can do is enough for the thing it is for — **generated** wallpapers and
widget layers, where every pixel is arithmetic:

    canvas = Canvas(3840, 2160)
    canvas.linear((0x0B, 0x0D, 0x10), (0x12, 0x1A, 0x22), angle=115)
    canvas.glow(1900, 900, 1400, (0xB6, 0xF2, 0x4A), strength=0.22)
    canvas.save("aurora.png")

Design notes that matter more than they look:

* **Buffer is `bytearray` of RGBA.** One allocation, indexed writes, no per-pixel
  objects. A 4K frame is 33 MB; allocating a tuple per pixel would be 500 MB.
* **Everything renders in a linear-light-ish working space.** Blending sRGB
  values directly is why naive gradient + glow looks muddy; sqrt/squared
  weighting is a cheap approximation that costs nothing and reads as "gradient".
* **Glyphs are rects and arcs, not a font.** A 5x7 bitmap face at 2-6x scale is
  legible and 200 lines. Embedding a TTF would add a file, a licence and a parser.
* **`dither` on gradients.** An 8-bit dark gradient over 4K bands visibly; ±1 LSB
  ordered noise removes it for ~2% more work and is the difference between
  "generated" and "cheap".
"""
from __future__ import annotations

import math
import struct
import zlib
from typing import Iterable, Sequence

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


class Canvas:
    """An RGBA raster with the drawing operations a wallpaper actually needs."""

    __slots__ = ("w", "h", "buf")

    def __init__(self, w: int, h: int, fill: RGBA | RGB = (0, 0, 0, 255)) -> None:
        if w <= 0 or h <= 0:
            raise ValueError("canvas must have a positive size")
        self.w, self.h = int(w), int(h)
        colour = fill if len(fill) == 4 else (*fill, 255)
        self.buf = bytearray(bytes(colour) * (self.w * self.h))

    # ------------------------------------------------------------- pixels
    def px(self, x: int, y: int) -> RGBA:
        i = (y * self.w + x) * 4
        b = self.buf
        return (b[i], b[i + 1], b[i + 2], b[i + 3])

    def set(self, x: int, y: int, colour: RGBA) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 4
            self.buf[i:i + 4] = bytes(colour)

    def blend(self, x: int, y: int, colour: RGBA) -> None:
        """Source-over in integer maths. `a` is 0..255."""
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        a = colour[3]
        if a == 0:
            return
        i = (y * self.w + x) * 4
        b = self.buf
        if a == 255:
            b[i:i + 4] = bytes(colour)
            return
        ia = 255 - a
        b[i] = (colour[0] * a + b[i] * ia) // 255
        b[i + 1] = (colour[1] * a + b[i + 1] * ia) // 255
        b[i + 2] = (colour[2] * a + b[i + 2] * ia) // 255
        b[i + 3] = min(255, b[i + 3] + a)

    # ----------------------------------------------------------- gradients
    def linear(self, c0: RGB, c1: RGB, *, angle: float = 90.0,
               dither: bool = True) -> None:
        """Fill with a linear gradient.

        `angle` is degrees clockwise from "up", so 90 is left→right and 180 is
        top→bottom — the same convention CSS uses, so a theme file's numbers mean
        what a web developer already expects.
        """
        # 0 deg = up (so the ramp runs bottom->top), 90 = left->right,
        # 180 = top->bottom, matching CSS. The sign of dy is what makes 0 point
        # up rather than down; getting it backwards silently reversed every
        # gradient in every theme, which the angle test now pins.
        rad = math.radians(angle)
        dx, dy = math.sin(rad), math.cos(rad)
        # math.sin(pi) is 1.2e-16, not 0. Left alone, every "vertical" gradient
        # took the general per-pixel path and the separable fast path below was
        # dead code. The residue contributes at most 3e-13 to the ramp, far under
        # one 8-bit step, so snapping it changes no pixel.
        if abs(dx) < 1e-9:
            dx = 0.0
        if abs(dy) < 1e-9:
            dy = 0.0
        # Projection range over the four corners, so the ramp always spans fully.
        corners = [(0, 0), (self.w, 0), (0, self.h), (self.w, self.h)]
        proj = [cx * dx + cy * dy for cx, cy in corners]
        lo, hi = min(proj), max(proj)
        span = (hi - lo) or 1.0
        b = self.buf
        w = self.w

        def ramp(t: float) -> float:
            t = 0.0 if t < 0 else (1.0 if t > 1 else t)
            # Perceptual weighting: a smoothstep ramp so the midtone of a dark
            # theme does not wash out.
            return t * t * (3 - 2 * t)

        def px(tt: float) -> bytes:
            return bytes((int(c0[0] + (c1[0] - c0[0]) * tt),
                          int(c0[1] + (c1[1] - c0[1]) * tt),
                          int(c0[2] + (c1[2] - c0[2]) * tt), 255))

        # A linear gradient is separable. When it is axis-aligned one of the two
        # coordinates drops out entirely: a horizontal ramp has one row repeated
        # for every scanline, and a vertical ramp has one colour repeated across
        # every row. Building the row once and using slice assignment turns
        # ~3.7 million four-byte stores into 1440 memcpys. That is where most of
        # the seven seconds behind "Save wallpaper" was going.
        if dy == 0.0:
            row = b"".join(px(ramp((x * dx - lo) / span)) for x in range(w))
            for y in range(self.h):
                b[y * w * 4:(y + 1) * w * 4] = row
        elif dx == 0.0:
            for y in range(self.h):
                row = px(ramp((y * dy - lo) / span)) * w
                b[y * w * 4:(y + 1) * w * 4] = row
        else:
            # Diagonal. The ramp along a row is still linear in x, so one row is
            # built with a generator instead of a nested loop and each row is
            # simply that generator shifted by the y term.
            for y in range(self.h):
                base = (y * dy - lo) / span
                row = b"".join(px(ramp(base + (x * dx) / span))
                               for x in range(w))
                b[y * w * 4:(y + 1) * w * 4] = row
        if dither:
            self._dither()

    def radial(self, centre: tuple[int, int], radius: float, c_in: RGB,
               c_out: RGB) -> None:
        cx, cy = centre
        r2 = radius * radius
        b = self.buf
        for y in range(self.h):
            dy = y - cy
            row = y * self.w
            for x in range(self.w):
                dx = x - cx
                d2 = dx * dx + dy * dy
                t = 1.0 if d2 >= r2 else 1.0 - (d2 / r2)
                tt = t * t
                i = (row + x) * 4
                b[i] = int(c_out[0] + (c_in[0] - c_out[0]) * tt)
                b[i + 1] = int(c_out[1] + (c_in[1] - c_out[1]) * tt)
                b[i + 2] = int(c_out[2] + (c_in[2] - c_out[2]) * tt)
                b[i + 3] = 255

    def _dither(self) -> None:
        """±1 LSB ordered noise. Stops banding in dark gradients at 4K."""
        b = self.buf
        order = (0, 2, -1, 1)          # a tiny 2x2 Bayer-ish matrix
        for y in range(self.h):
            row = y * self.w
            oy = (y & 1) * 2
            for x in range(self.w):
                d = order[oy + (x & 1)]
                i = (row + x) * 4
                b[i] = 0 if (v := b[i] + d) < 0 else (255 if v > 255 else v)
                b[i + 1] = 0 if (v := b[i + 1] + d) < 0 else (255 if v > 255 else v)
                b[i + 2] = 0 if (v := b[i + 2] + d) < 0 else (255 if v > 255 else v)

    # --------------------------------------------------------------- glow
    def glow(self, cx: float, cy: float, radius: float, colour: RGB,
             *, strength: float = 0.3) -> None:
        """Additive falloff light. The workhorse for "aurora" style backgrounds."""
        r = int(radius)
        x0, x1 = max(0, int(cx) - r), min(self.w, int(cx) + r)
        y0, y1 = max(0, int(cy) - r), min(self.h, int(cy) + r)
        b = self.buf
        inv = 1.0 / (radius * radius)
        # Hoisted arithmetic. The original did dx*dx + dy*dy, a float compare
        # and three min() calls per pixel — 12 million min() calls for one
        # 2560x1440 wallpaper, about six of the seven seconds a "Save wallpaper"
        # click took. The dx term is constant down a column, so it lives in a
        # list; f = base - dx2i is exactly the same expression, just regrouped.
        cr, cg, cb = colour[0] * strength, colour[1] * strength, colour[2] * strength
        cols = [(x, (x - cx) * (x - cx) * inv) for x in range(x0, x1)]
        for y in range(y0, y1):
            dy = y - cy
            base = 1.0 - dy * dy * inv
            if base <= 0.0:
                continue
            row = y * self.w
            for x, dx2i in cols:
                f = base - dx2i
                if f <= 0.0:
                    continue
                k = f * f
                i = (row + x) * 4
                v = b[i] + cr * k
                b[i] = 255 if v > 255.0 else int(v)
                v = b[i + 1] + cg * k
                b[i + 1] = 255 if v > 255.0 else int(v)
                v = b[i + 2] + cb * k
                b[i + 2] = 255 if v > 255.0 else int(v)

    # -------------------------------------------------------------- shapes
    def rect(self, x: int, y: int, w: int, h: int, colour: RGBA,
             *, radius: int = 0) -> None:
        if radius <= 0:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.blend(xx, yy, colour)
            return
        r = min(radius, w // 2, h // 2)
        for yy in range(y, y + h):
            dy = 0
            if yy < y + r:
                dy = (y + r) - yy
            elif yy >= y + h - r:
                dy = yy - (y + h - r - 1)
            for xx in range(x, x + w):
                dx = 0
                if xx < x + r:
                    dx = (x + r) - xx
                elif xx >= x + w - r:
                    dx = xx - (x + w - r - 1)
                if dx and dy and dx * dx + dy * dy > r * r:
                    continue
                self.blend(xx, yy, colour)

    def circle(self, cx: float, cy: float, r: float, colour: RGBA,
               *, thickness: float = 0.0) -> None:
        r2_out = r * r
        r2_in = (r - thickness) ** 2 if thickness > 0 else -1
        for yy in range(int(cy - r) - 1, int(cy + r) + 2):
            dy = yy - cy
            for xx in range(int(cx - r) - 1, int(cx + r) + 2):
                dx = xx - cx
                d2 = dx * dx + dy * dy
                if d2 <= r2_out and d2 >= r2_in:
                    self.blend(xx, yy, colour)

    def arc(self, cx: float, cy: float, r: float, a0: float, a1: float,
            colour: RGBA, *, thickness: float = 4.0) -> None:
        """Stroke an arc. Angles in degrees, 0 = up, clockwise."""
        steps = max(8, int(abs(a1 - a0) * 1.4))
        for s in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * s / steps)
            x = cx + math.sin(a) * r
            y = cy - math.cos(a) * r
            self.circle(x, y, thickness / 2, colour)

    def line(self, x0: float, y0: float, x1: float, y1: float, colour: RGBA,
             *, width: float = 2.0) -> None:
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        steps = max(1, int(length))
        for s in range(steps + 1):
            t = s / steps
            self.circle(x0 + dx * t, y0 + dy * t, width / 2, colour)

    # --------------------------------------------------------------- text
    def text(self, x: int, y: int, s: str, colour: RGBA, *, scale: int = 3,
             spacing: int = 1, tracking: float = 1.0) -> int:
        """Draw with a 5x7 bitmap face. Returns the width drawn.

        Legible from scale 2 up. At scale 1 it is a texture, and the caller
        should use `scale>=2` for anything a person has to read.
        """
        cx = x
        for ch in s:
            glyph = GLYPHS.get(ch.upper(), GLYPHS[" "])
            for gy, rowbits in enumerate(glyph):
                for gx in range(5):
                    if rowbits & (1 << (4 - gx)):
                        self.rect(cx + gx * scale, y + gy * scale,
                                  scale, scale, colour)
            cx += (5 + spacing) * scale
            cx += int(scale * tracking) - scale
        return cx - x

    def text_width(self, s: str, *, scale: int = 3, spacing: int = 1) -> int:
        return len(s) * (5 + spacing) * scale

    # -------------------------------------------------------------- output
    def save(self, path: str) -> int:
        with open(path, "wb") as fh:
            fh.write(self.png())
        import os
        return os.path.getsize(path)

    def png(self, *, level: int = 6) -> bytes:
        """Encode RGBA as PNG. Filter type 1 (Sub) on every row: gradients and
        flat UI compress far better with it than with the default of none."""
        b = self.buf
        stride = self.w * 4
        raw = bytearray((stride + 1) * self.h)
        out = 0
        for y in range(self.h):
            raw[out] = 1                     # Sub filter
            out += 1
            base = y * stride
            raw[out:out + 4] = b[base:base + 4]
            for x in range(4, stride):
                raw[out + x] = (b[base + x] - b[base + x - 4]) & 0xFF
            out += stride

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 6, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(bytes(raw), level))
                + chunk(b"IEND", b""))


# ------------------------------------------------------------------ the face
# 5x7, one int per row, bit 4 = leftmost column. Enough for clocks, labels and
# numbers; deliberately not a text layout engine.
GLYPHS: dict[str, tuple[int, ...]] = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    "A": (0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "B": (0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110),
    "C": (0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110),
    "D": (0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110),
    "E": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111),
    "F": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000),
    "G": (0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01111),
    "H": (0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "I": (0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "J": (0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100),
    "K": (0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001),
    "L": (0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111),
    "M": (0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001),
    "N": (0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001),
    "O": (0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "P": (0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000),
    "Q": (0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101),
    "R": (0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001),
    "S": (0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110),
    "T": (0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100),
    "U": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "V": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100),
    "W": (0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001),
    "X": (0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001),
    "Y": (0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100),
    "Z": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111),
    "0": (0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110),
    "1": (0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "2": (0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111),
    "3": (0b11111, 0b00010, 0b00100, 0b00010, 0b00001, 0b10001, 0b01110),
    "4": (0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010),
    "5": (0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110),
    "6": (0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110),
    "7": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000),
    "8": (0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110),
    "9": (0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100),
    ":": (0, 0b00100, 0b00100, 0, 0b00100, 0b00100, 0),
    ".": (0, 0, 0, 0, 0, 0b00110, 0b00110),
    ",": (0, 0, 0, 0, 0b00110, 0b00100, 0b01000),
    "-": (0, 0, 0, 0b11111, 0, 0, 0),
    "+": (0, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0),
    "%": (0b11001, 0b11010, 0b00010, 0b00100, 0b01011, 0b10011, 0b00000),
    "/": (0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000),
    "°": (0b01100, 0b10010, 0b10010, 0b01100, 0, 0, 0),
    "!": (0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0, 0b00100),
    "?": (0b01110, 0b10001, 0b00001, 0b00110, 0b00100, 0, 0b00100),
    "'": (0b00100, 0b00100, 0, 0, 0, 0, 0),
    "(": (0b00010, 0b00100, 0b01000, 0b01000, 0b01000, 0b00100, 0b00010),
    ")": (0b01000, 0b00100, 0b00010, 0b00010, 0b00010, 0b00100, 0b01000),
    "&": (0b01100, 0b10010, 0b10100, 0b01000, 0b10101, 0b10010, 0b01101),
    "#": (0b01010, 0b01010, 0b11111, 0b01010, 0b11111, 0b01010, 0b01010),
    "@": (0b01110, 0b10001, 0b10111, 0b10101, 0b10111, 0b10000, 0b01110),
    "*": (0, 0b10101, 0b01110, 0b11111, 0b01110, 0b10101, 0),
    "=": (0, 0, 0b11111, 0, 0b11111, 0, 0),
}
