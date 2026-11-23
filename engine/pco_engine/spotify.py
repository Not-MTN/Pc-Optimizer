"""Spotify — colour themes, a live preview, and an honest story about safety.

Three separate things live here, and the separation is the whole design:

  1. THEMES. A look is a named palette (glassy, cool, warm, high-contrast …)
     plus the structural decisions that make it feel like *that* look — blur,
     border radius, sidebar treatment. These are ours; we author them, so there
     is no licence question and no third-party code runs.

  2. PREVIEW. A real raster of what the look does to a Spotify-shaped window,
     drawn with the same Canvas the wallpaper engine uses. This is the feature
     that always works, on every machine, with no Spotify installed: you can
     see and compare every look before you decide. It is also the only part of
     this module that is fully within our control.

  3. APPLY. Optional, and deliberately quarantined.

Why APPLY is not "just run Spicetify":

  * Spotify broke client modification. Version 1.2.87 (April 2026) closed the
    webpack seam Spicetify injected through, the maintainers said there was no
    ETA for a fix, and downgrading Spotify to a working build stops playback
    because the client refuses to stream until it is updated.
  * Spicetify rewrites Spotify's own installation. That is a real, invasive
    operation on someone else's signed binary, and doing it silently from a PC
    optimizer would be exactly the kind of thing this app exists to criticise.
  * So: we never patch Spotify ourselves. We write a theme in Spicetify's own
    documented format into its own themes folder, and we run its own CLI. If
    Spicetify is missing, we say so and stop — we do not download and execute
    third-party installers.

What this means for a user, stated plainly:

    the preview always works; the apply needs Spicetify; Spicetify needs a
    Spotify version it supports, and that may not be the one you have.

That is a worse promise than "it just works", and it is the true one.
"""
from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canvas import Canvas
from .ids import new_id

# ---------------------------------------------------------------- the palette
# Spotify's own UI is a dark shell with one saturated accent. Every look here
# keeps that structure and varies the two things a person actually notices: how
# the surfaces separate (flat, layered, glassy) and what the accent does.

SURFACE_KEYS = ("main", "sidebar", "player", "card", "shadow", "main-secondary",
                "highlight", "notification", "notification-error", "misc")
TEXT_KEYS = ("text", "subtext", "nav-active-text")
ACCENT_KEYS = ("button", "button-active", "button-secondary", "button-disabled",
               "play-button", "playback-bar", "nav-active", "tab-active",
               "selected-row")


@dataclass
class Look:
    """One Spotify theme: a palette plus the structural choices that sell it."""

    id: str
    name: str
    blurb: str
    # surfaces
    main: str
    sidebar: str
    player: str
    card: str
    text: str
    subtext: str
    accent: str
    # structure
    radius: int = 8
    blur: bool = False
    glass: bool = False
    dense: bool = False
    scheme_name: str = "Keel"

    # ---------------------------------------------------------------- palette
    def _mix(self, a: str, b: str, t: float) -> str:
        """Blend two hex colours. t=0 gives a, t=1 gives b."""
        t = max(0.0, min(1.0, t))
        ar, ag, ab = (int(a[i:i + 2], 16) for i in (0, 2, 4))
        br, bg, bb = (int(b[i:i + 2], 16) for i in (0, 2, 4))
        return "%02x%02x%02x" % (round(ar + (br - ar) * t),
                                 round(ag + (bg - ag) * t),
                                 round(ab + (bb - ab) * t))

    def _lift(self, hexcolour: str, amount: float) -> str:
        """Raise a colour toward white — how a card sits above a background."""
        return self._mix(hexcolour, "ffffff", amount)

    def _sink(self, hexcolour: str, amount: float) -> str:
        return self._mix(hexcolour, "000000", amount)

    def colors(self) -> dict[str, str]:
        """The colour.ini scheme, in Spicetify's documented key names.

        Every key Spicetify's own themes use is present. A missing key is not a
        crash — it silently falls back to a Spotify default, which produces a
        theme that is *almost* right, and almost-right is harder to debug than
        broken. So: complete.
        """
        return {
            "text": self.text,
            "subtext": self.subtext,
            "nav-active-text": self.text,
            "main": self.main,
            "sidebar": self.sidebar,
            "player": self.player,
            "card": self.card,
            "shadow": self._sink(self.main, 0.65),
            "main-secondary": self._lift(self.main, 0.04),
            "main-elevated": self._lift(self.main, 0.07),
            "highlight": self._lift(self.main, 0.05),
            "highlight-elevated": self._lift(self.main, 0.1),
            "selected-row": self._mix(self.main, self.accent, 0.18),
            "button": self.accent,
            "button-active": self._lift(self.accent, 0.12),
            "button-secondary": self._sink(self.accent, 0.22),
            "button-disabled": self._mix(self.card, self.subtext, 0.35),
            "nav-active": self._mix(self.main, self.accent, 0.12),
            "tab-active": self._lift(self.main, 0.06),
            "notification": self._mix(self.main, self.accent, 0.1),
            "notification-error": "#eb4034"[1:],
            "play-button": self.accent,
            "playback-bar": self.accent,
            "misc": self.text,
        }

    # ------------------------------------------------------------------- css
    def user_css(self) -> str:
        """The structural half — blur, radius, density.

        Kept small and readable on purpose. A theme that ships 400 lines of CSS
        against Spotify's undocumented class names is a theme that breaks on the
        next Spotify release; these selectors are the ones Spicetify's own
        documentation and long-lived themes still use, and nothing here depends
        on a hashed class name.
        """
        lines = [
            f"/* Keel — {self.name} */",
            f":root {{ --keel-radius: {self.radius}px; }}",
        ]
        if self.glass:
            lines += [
                "/* Glass: translucent panels over the album art. */",
                ".Root__main-view, .Root__nav-bar, .Root__now-playing-bar {",
                "  backdrop-filter: blur(18px) saturate(1.25);",
                "}",
                ".Root__main-view { background-color: rgba(0, 0, 0, 0.18) !important; }",
            ]
        if self.blur:
            lines += [".main-entityHeader-container { backdrop-filter: blur(12px); }"]
        if self.radius:
            lines += [
                f".main-card-card, .main-trackList-trackListRow, "
                f".main-nowPlayingBar-nowPlayingBar, .main-navBar-navBar {{",
                f"  border-radius: {self.radius}px !important;",
                "}",
                f".main-card-imageContainer, .main-image-image "
                f"{{ border-radius: max(0px, {self.radius - 2}px) !important; }}",
                ".main-card-card:hover { transform: translateY(-2px); }",
            ]
        if self.dense:
            lines += [
                "/* Dense: more rows per screen without shrinking the type. */",
                ".main-trackList-trackListRow { min-height: 38px !important; }",
                ".main-topBar-topbarContent { min-height: 52px; }",
            ]
        return "\n".join(lines) + "\n"

    def color_ini(self) -> str:
        """A complete color.ini. Two schemes: this look, and its light inverse."""
        out = [f"[{self.scheme_name}]"]
        for key, value in self.colors().items():
            out.append(f"{key} = {value}")
        out.append("")
        out.append("[Keel Light]")
        light = dict(self.colors())
        light["main"] = self._lift(self.main, 0.86)
        light["sidebar"] = self._lift(self.sidebar, 0.9)
        light["player"] = self._lift(self.player, 0.8)
        light["card"] = self._lift(self.card, 0.78)
        light["text"] = "111318"
        light["subtext"] = "5a6478"
        light["nav-active-text"] = "111318"
        for key, value in light.items():
            out.append(f"{key} = {value}")
        return "\n".join(out) + "\n"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "blurb": self.blurb,
            "accent": self.accent, "main": self.main, "card": self.card,
            "radius": self.radius, "glass": self.glass, "blur": self.blur,
            "dense": self.dense, "scheme": self.scheme_name,
            "swatches": [self.main, self.card, self.accent, self.text],
        }


# --------------------------------------------------------------- the library
def _looks() -> list[Look]:
    """Twelve looks, built from a small number of honest ideas rather than
    twelve variations of the same gradient.

    A look is a *position* on three axes: how separate the surfaces are, how
    saturated the accent is, and how much glass. Naming the axes is what stops a
    theme gallery from becoming a wall of arbitrary colours.
    """
    return [
        # --- class: glass -----------------------------------------------------
        Look("glass", "Glass", "Spotify behind frosted glass. The album art leaks through everything.",
             main="101318", sidebar="0b0e12", player="151a21", card="1c232c",
             text="eef2f8", subtext="9aa7bb", accent="b6f24a",
             radius=16, blur=True, glass=True, scheme_name="Glass"),
        Look("frost", "Frost", "Colder glass, blue-tinted, very low contrast on purpose.",
             main="0e141b", sidebar="0a0f15", player="111823", card="18212d",
             text="e3ecf7", subtext="8fa0b8", accent="7dd3fc",
             radius=18, blur=True, glass=True, scheme_name="Frost"),
        Look("smoke", "Smoke", "Neutral grey glass. The one that disappears into a dark desktop.",
             main="141517", sidebar="101113", player="181a1d", card="202327",
             text="eceef1", subtext="979ca3", accent="d4d8dd",
             radius=14, glass=True, scheme_name="Smoke"),
        # --- class: cool ------------------------------------------------------
        Look("arctic", "Arctic", "Ice blue on near-black. Cool without being cold.",
             main="0a0f16", sidebar="070b11", player="0d141d", card="141d29",
             text="e6f0fa", subtext="8ba3bf", accent="38bdf8",
             radius=10, scheme_name="Arctic"),
        Look("mint", "Mint", "Soft mint green. The calm one.",
             main="0c1311", sidebar="080e0c", player="0f1816", card="16211e",
             text="e4f5ef", subtext="8fb8ad", accent="5eead4",
             radius=12, scheme_name="Mint"),
        Look("deep", "Deep", "Deep indigo. For late sessions with the lights off.",
             main="0a0a14", sidebar="07070f", player="0d0d1a", card="14142a",
             text="e8e6ff", subtext="9b98c4", accent="818cf8",
             radius=10, scheme_name="Deep"),
        # --- class: warm ------------------------------------------------------
        Look("ember", "Ember", "Warm amber on charcoal. The only one that feels lit.",
             main="12100c", sidebar="0d0b08", player="17140f", card="211c15",
             text="f7f0e3", subtext="bfae94", accent="fbbf24",
             radius=10, scheme_name="Ember"),
        Look("rose", "Rose", "Dusty pink accents, dark and quiet.",
             main="140f12", sidebar="0f0b0e", player="191318", card="241b21",
             text="fae8ef", subtext="c4a2b0", accent="fb7185",
             radius=14, scheme_name="Rose"),
        Look("sand", "Sand", "Paper-warm dark. Reads like a book, not a screen.",
             main="14120e", sidebar="100e0b", player="191612", card="231f19",
             text="f5efe4", subtext="bcb09b", accent="d6b370",
             radius=8, scheme_name="Sand"),
        # --- class: loud ------------------------------------------------------
        Look("acid", "Acid", "Maximum contrast, lime accent. Not subtle, not trying to be.",
             main="07090a", sidebar="040506", player="0a0d0f", card="111619",
             text="ffffff", subtext="a8b4bd", accent="b6f24a",
             radius=6, scheme_name="Acid"),
        Look("candy", "Candy", "Saturated violet. Loud on purpose.",
             main="0f0817", sidebar="0a0510", player="140b1e", card="1d1229",
             text="f6ecff", subtext="b6a3cc", accent="c084fc",
             radius=16, scheme_name="Candy"),
        # --- class: the free one ----------------------------------------------
        Look("stock", "Stock", "Spotify's own colours, with the corners rounded. 0 % change to the design.",
             main="121212", sidebar="000000", player="181818", card="282828",
             text="ffffff", subtext="b3b3b3", accent="1db954",
             radius=8, scheme_name="Stock"),
    ]


LOOKS: dict[str, Look] = {look.id: look for look in _looks()}
CATEGORIES = {
    "glass": ("Glassy", ["glass", "frost", "smoke"]),
    "cool": ("Cool", ["arctic", "mint", "deep"]),
    "warm": ("Warm", ["ember", "rose", "sand"]),
    "loud": ("Loud", ["acid", "candy"]),
    "stock": ("Unchanged", ["stock"]),
}


# ------------------------------------------------------------------- preview
def render_preview(look: Look, width: int = 720, height: int = 460) -> Canvas:
    """Draw a Spotify-shaped window wearing this look.

    Drawn from the palette rather than screenshotted, so it cannot lie about
    what the theme contains. Deliberately not a pixel-perfect clone of Spotify —
    claiming otherwise would be dishonest about what a preview is.
    """
    w, h = int(width), int(height)
    c = Canvas(w, h, _hex_to_rgb(look.main))

    bar = int(h * 0.10)               # top bar
    side = int(w * 0.22)              # sidebar
    player = int(h * 0.13)            # now-playing bar

    # --- sidebar ------------------------------------------------------------
    c.rect(0, 0, side, h - player, _hex_to_rgb(look.sidebar))
    for i in range(9):
        y = int(bar + 16 + i * (h - player - bar) / 11)
        row_w = int(side * (0.62 if i % 3 else 0.44))
        c.rect(int(side * 0.1), y, row_w, 7,
               _hex_to_rgb(look.accent, 210) if i == 0
               else _hex_to_rgb(look.subtext, 90))
    c.rect(int(side * 0.08), int(bar + 10), int(side * 0.3), 8,
           _hex_to_rgb(look.text, 230))

    # --- top bar ------------------------------------------------------------
    c.rect(side, 0, w - side, bar, _hex_to_rgb(look.main))
    c.rect(int(w * 0.55), int(bar * 0.3), int(w * 0.24), int(bar * 0.36),
           _hex_to_rgb(look.card), radius=6)
    c.circle(int(w * 0.83) + 11, int(bar * 0.28) + 11, 11,
             _hex_to_rgb(look.accent, 220))

    # --- hero ---------------------------------------------------------------
    # The grid below is sized from the space that is actually left, not from a
    # fixed aspect ratio. The first version left a dead band above the player bar
    # because the cards were sized first and the leftovers were simply empty.
    content_top = bar + int(h * 0.04)
    content_bottom = h - player - int(h * 0.03)
    hero_y = content_top
    hero_h = int((content_bottom - content_top) * 0.34)
    _grad_rect(c, side + 24, hero_y, w - side - 48, hero_h,
               _hex_to_rgb(look.card), _hex_to_rgb(look.main), 90.0)
    c.rect(side + 40, hero_y + hero_h - 30, int((w - side) * 0.28), 12,
           _hex_to_rgb(look.text, 225), radius=3)
    c.rect(side + 40, hero_y + hero_h - 14, int((w - side) * 0.18), 7,
           _hex_to_rgb(look.subtext, 150), radius=3)
    c.circle(side + 62, hero_y + hero_h - 62, 17, _hex_to_rgb(look.accent))

    # --- card grid ----------------------------------------------------------
    gap = 16
    gx0 = side + 24
    gy0 = hero_y + hero_h + 24
    grid_w = w - side - 48
    cols = 4
    rows = 2
    row_space = content_bottom - gy0
    card_h = max(48, (row_space - gap * (rows - 1)) // rows)
    card_w = min((grid_w - gap * (cols - 1)) // cols, int(card_h / 0.82))
    for row in range(rows):
        for col in range(cols):
            x = gx0 + col * (card_w + gap)
            y = gy0 + row * (card_h + gap)
            if y + card_h > h - player - 8:
                continue
            box = (_lighten(look.card, 0.1, 170) if look.glass
                   else _hex_to_rgb(look.card))
            c.rect(x, y, card_w, card_h, box, radius=look.radius)
            art = int(card_h * 0.62)
            c.rect(x + 8, y + 8, card_w - 16, art,
                   _hex_to_rgb(look.sidebar if row == 0 else look.main),
                   radius=max(2, look.radius - 3))
            if row == 0 and col == 0:
                c.circle(x + 8 + art // 2, y + 8 + art // 2, art // 5,
                         _hex_to_rgb(look.accent, 200))
            c.rect(x + 10, y + art + 16, int((card_w - 20) * 0.7), 6,
                   _hex_to_rgb(look.text, 225), radius=3)
            c.rect(x + 10, y + art + 28, int((card_w - 20) * 0.45), 5,
                   _hex_to_rgb(look.subtext, 140), radius=2)

    # --- now playing --------------------------------------------------------
    py = h - player
    c.rect(0, py, w, player, _hex_to_rgb(look.player))
    c.rect(18, py + player // 2 - 18, 36, 36, _hex_to_rgb(look.card), radius=5)
    c.rect(66, py + player // 2 - 12, 110, 6, _hex_to_rgb(look.text, 220), radius=3)
    c.rect(66, py + player // 2 - 2, 70, 5, _hex_to_rgb(look.subtext, 140), radius=2)
    mid = w // 2
    c.circle(mid, py + player // 2 - 6, 12, _hex_to_rgb(look.accent))
    c.rect(mid - 90, py + player // 2 + 14, 180, 4,
           _hex_to_rgb(look.subtext, 110), radius=2)
    c.rect(mid - 90, py + player // 2 + 14, 62, 4, _hex_to_rgb(look.accent), radius=2)
    c.circle(mid - 28, py + player // 2 + 16, 4, _hex_to_rgb(look.accent))

    if look.glass:
        _glass_veil(c, w, h, player)
    return c


def _glass_veil(c: Canvas, w: int, h: int, player: int) -> None:
    """A cheap frosted impression: a soft vertical sheen over the top third.

    Real blur in a preview would mean sampling neighbours for every pixel, which
    at tile sizes costs more than the whole rest of the render. A diagonal sheen
    reads as glass at thumbnail scale and is honest about being an impression.
    """
    for y in range(0, int(h * 0.55)):
        a = int(26 * (1.0 - y / (h * 0.55)))
        if a <= 0:
            continue
        for x in range(0, w, 2):
            c.blend(x, y, (255, 255, 255, a))


def _hex_to_rgb(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Hex to the Canvas's RGBA. The fourth channel is the one that matters here:
    most of the mock UI is semi-transparent, exactly like the real thing."""
    v = value.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), alpha)


def _lighten(value: str, amount: float, alpha: int = 255):
    r, g, b, _ = _hex_to_rgb(value)
    return (min(255, round(r + (255 - r) * amount)),
            min(255, round(g + (255 - g) * amount)),
            min(255, round(b + (255 - b) * amount)), alpha)


def _grad_rect(c, x, y, w, h, c0, c1, angle=90.0):
    """A gradient confined to a rectangle, built from horizontal scanlines.

    Canvas.linear fills the whole surface, which is right for a wallpaper and
    wrong for a hero card, so this draws row by row."""
    import math
    rad = math.radians(angle)
    dx, dy = math.sin(rad), -math.cos(rad)      # CSS convention: 0 deg = up
    span = abs(dx) * w + abs(dy) * h or 1.0
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            t = ((xx - x) * dx + (yy - y) * dy) / span + 0.5
            t = max(0.0, min(1.0, t))
            colour = (round(c0[0] + (c1[0] - c0[0]) * t),
                      round(c0[1] + (c1[1] - c0[1]) * t),
                      round(c0[2] + (c1[2] - c0[2]) * t),
                      round(c0[3] + (c1[3] - c0[3]) * t))
            c.blend(xx, yy, colour)


# -------------------------------------------------------------------- status
@dataclass
class SpotifyStatus:
    """What is actually true about this machine, right now."""

    installed: bool = False
    spicetify: bool = False
    spicetify_version: str | None = None
    spotify_version: str | None = None
    supported: bool | None = None       # None = unknown, do not guess
    themes_dir: str | None = None
    can_apply: bool = False
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "spicetify": self.spicetify,
            "spicetify_version": self.spicetify_version,
            "spotify_version": self.spotify_version,
            "supported": self.supported,
            "themes_dir": self.themes_dir,
            "can_apply": self.can_apply,
            "reasons": self.reasons,
        }


def default_themes_dir() -> Path:
    """Spicetify's documented Windows location.

    We do not invent a path: if the user has Spicetify, its themes live here,
    and writing anywhere else means the theme exists but Spicetify cannot see it.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "spicetify" / "Themes"
    return Path.home() / ".config" / "spicetify" / "Themes"


def detect() -> SpotifyStatus:
    """Probe the machine. Never installs, never guesses, never patches.

    Deliberately conservative: `supported` stays None unless a version could be
    read, because a wrong "supported: yes" is worse than an honest "unknown" —
    it sends the user to a `spicetify apply` that will silently do nothing.
    """
    status = SpotifyStatus(themes_dir=str(default_themes_dir()))

    exe = shutil.which("spicetify") or shutil.which("spicetify.exe")
    if exe:
        status.spicetify = True
        try:
            out = subprocess.run([exe, "--version"], capture_output=True,
                                 text=True, timeout=15)
            status.spicetify_version = (out.stdout or out.stderr or "").strip() or None
        except (OSError, subprocess.SubprocessError):
            status.spicetify_version = None

    # Spotify itself: the Store install and the classic installer differ.
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
    ]
    status.installed = any(p.exists() for p in candidates if str(p).strip())
    if not status.installed:
        status.reasons.append(
            "Spotify was not found in its usual install locations. If it is "
            "installed, the Microsoft Store version keeps its files somewhere "
            "this build does not look — you can still copy a theme file by hand.")

    if not status.spicetify:
        status.reasons.append(
            "Spicetify is not installed. Keel will not download and run "
            "third-party installers for you; install it yourself from "
            "spicetify.app if you want to apply a theme.")

    status.reasons.append(
        "Spotify 1.2.87 (April 2026) closed the injection point Spicetify used. "
        "The maintainers reported no ETA for a fix, and downgrading Spotify "
        "stops playback because the client refuses to stream until updated. If "
        "your Spotify is newer than your Spicetify supports, applying will do "
        "nothing — that is Spotify's decision, not a setting you can turn off.")

    status.can_apply = status.spicetify and status.installed
    if status.spicetify and status.spotify_version is None:
        status.supported = None
    return status


# ------------------------------------------------------------------- writing
def write_theme(look: Look, *, themes_dir: Path | None = None) -> dict[str, Any]:
    """Write this look as a Spicetify theme folder.

    Two files, in Spicetify's own format: color.ini and user.css. Nothing here
    touches Spotify's installation. If the theme folder already exists we do not
    overwrite it silently — a user may have edited it.
    """
    base = Path(themes_dir) if themes_dir else default_themes_dir()
    folder = base / f"Keel {look.name}"
    theme_id = new_id()

    if folder.exists():
        existing = (folder / "color.ini").exists() or (folder / "user.css").exists()
        if existing:
            return {
                "ok": False, "id": theme_id, "name": look.name,
                "path": str(folder), "wrote": False,
                "reason": ("That theme folder already exists and has files in it. "
                           "Keel does not overwrite a theme you may have edited — "
                           "rename or remove the old folder first."),
            }

    try:
        # exist_ok=False, and catch FileExistsError: the check above and the
        # mkdir here are two steps, and between them another thread (or another
        # copy of Keel) can create the folder. With exist_ok=True both writers
        # proceed and interleave their two files, so a theme can end up with one
        # look's colour.ini and another look's user.css — which renders as a
        # half-applied theme nobody can explain.
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return {
            "ok": False, "id": theme_id, "name": look.name,
            "path": str(folder), "wrote": False,
            "reason": ("That theme folder already exists. Keel does not "
                       "overwrite a theme you may have edited — rename or "
                       "remove the old folder first."),
        }
    except OSError as exc:
        return {"ok": False, "id": theme_id, "name": look.name,
                "path": str(folder), "wrote": False,
                "reason": f"could not create {folder}: {exc.strerror or exc}"}

    # Written after the folder is exclusively ours. Two writes, not one temp
    # file: if the second fails the folder holds an incomplete theme, and the
    # error says so rather than leaving a folder that looks finished.
    try:
        (folder / "color.ini").write_text(look.color_ini(), encoding="utf-8")
        (folder / "user.css").write_text(look.user_css(), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "id": theme_id, "name": look.name,
                "path": str(folder), "wrote": False,
                "reason": f"could not write to {folder}: {exc.strerror or exc}"}

    return {
        "ok": True, "id": theme_id, "name": look.name, "path": str(folder),
        "wrote": True, "files": ["color.ini", "user.css"],
        "scheme": look.scheme_name,
        "how_to_apply": (
            f'spicetify config current_theme "Keel {look.name}" '
            f'color_scheme "{look.scheme_name}" && spicetify apply'
        ),
        "note": ("Written to Spicetify's theme folder. Nothing was applied and "
                 "Spotify was not modified. The command above does both — and it "
                 "only works if your Spicetify supports your Spotify version."),
    }


def export_bundle(look: Look) -> dict[str, Any]:
    """The two files as text, so a look can be shared without a filesystem."""
    return {
        "name": look.name,
        "files": {
            "color.ini": look.color_ini(),
            "user.css": look.user_css(),
        },
        "install": [
            "Create a folder named 'Keel " + look.name + "' inside",
            "%APPDATA%\\spicetify\\Themes",
            "Put both files in it, then run the two spicetify commands.",
        ],
    }
