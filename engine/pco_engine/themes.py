"""Themes: one file that is the whole look.

A theme bundles a wallpaper spec, a widget layout, shell preferences and a
terminal palette into one JSON document. Two consequences follow, and both are
the point:

* **A look is shareable.** `export()` gives a string a person can paste to a
  friend; `import_()` validates it and shows what would change before applying.
* **A look is reversible.** Applying a theme journals it like any other change,
  so "put it back the way it was" is one click and not a memory exercise.

The fifteen built-in themes are not fifteen colour swaps. Each one is a
different *structure* — a different scene, a different widget layout, a different
density — because a gallery of recoloured gradients is a colour picker wearing a
gallery's clothes.

Honesty rules that this module enforces:
  * Unknown keys are an error, not ignored. A theme file with a typo silently
    doing nothing is the failure mode this whole app is built against.
  * A widget that cannot render is reported by index, and the rest still draw.
  * `apply` returns the list of what changed, and nothing claims to have changed
    if the value was already equal.
"""
from __future__ import annotations

from .absent import Absent

import json
from dataclasses import dataclass, field
from typing import Any

from .scenes import SCENES, Palette
from .wallpaper import SIZES, live_warnings, problems as wallpaper_problems
from .widgets import WIDGET_TYPES

SCHEMA = 1

KNOWN_TOP = {"meta", "palette", "wallpaper", "widgets", "shell", "terminal", "schema"}
# Derived from shell_lab rather than hand-written. The first version of this
# module invented flat names ("taskbar_align") while the engine used namespaced
# ones ("taskbar.align"), so every theme applied cleanly and changed nothing.
from .shell_lab import SHELL as _SHELL, TASKBAR as _TASKBAR

KNOWN_SHELL = set(_TASKBAR) | set(_SHELL)
KNOWN_TERMINAL = {"foreground", "background", "cursor", "selection", "font", "opacity"}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = str(value).lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected #rrggbb, got {value!r}")
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"not a hex colour: {value!r}") from exc


# --------------------------------------------------------------- built-ins
def _w(type_: str, x: float, y: float, size: float, **kw: Any) -> dict[str, Any]:
    return {"type": type_, "x": x, "y": y, "size": size, **kw}


BUILTIN: dict[str, dict[str, Any]] = {
    "keel": {
        "meta": {"name": "Keel", "author": "built-in", "version": 1,
                 "note": "The house look: ink, lime, a fin of light."},
        "palette": {"bg": "#0B0D10", "accent": "#B6F24A",
                    "secondary": "#5A6478", "tint": "#2A3A55"},
        "wallpaper": {"scene": "aurora", "size": "1440p", "seed": 7,
                      "scrim": True, "vignette": 0.55},
        "widgets": [
            _w("stat-block", 0.15, 0.17, 0.085, text="23:41", label="MONDAY STUDIO"),
            _w("gauge-ring", 0.15, 0.40, 0.075, value=0.42, label="CPU"),
            _w("gauge-ring", 0.15, 0.60, 0.075, value=0.68, label="RAM", accent="#8FD14F"),
            _w("gauge-bar", 0.15, 0.79, 0.075, value=0.31, label="DISK",
               detail="118/512 GB"),
        ],
        "shell": {"taskbar.align": "center", "shell.dark_mode": "dark",
                  "taskbar.transparency": "on", "shell.show_extensions": "show"},
        "terminal": {"foreground": "#DCE3EE", "background": "#0B0D10",
                     "cursor": "#B6F24A", "font": "Cascadia Code", "opacity": 0.94},
    },
    "aurora-glass": {
        "meta": {"name": "Aurora Glass", "author": "built-in", "version": 1,
                 "note": "Bright curtains, floating taskbar, everything translucent."},
        "palette": {"bg": "#070A12", "accent": "#7DE2FF",
                    "secondary": "#6C7A96", "tint": "#1E3A5F"},
        "wallpaper": {"scene": "aurora", "size": "1440p", "seed": 19,
                      "scrim": True, "vignette": 0.62},
        "widgets": [
            _w("clock-digital", 0.5, 0.12, 0.10, text="23:41", label="AURORA GLASS"),
            _w("weather", 0.82, 0.16, 0.075, text="7°", label="ISTANBUL", detail="clear"),
            _w("gauge-bar", 0.82, 0.34, 0.08, value=0.55, label="CPU", detail="55%"),
            _w("gauge-bar", 0.82, 0.44, 0.08, value=0.72, label="RAM", detail="72%"),
            _w("quote", 0.5, 0.91, 0.09,
               text="LIGHT RUNS BEFORE THE WEATHER DOES"),
        ],
        "shell": {"taskbar.align": "center", "taskbar.widgets": "on",
                  "shell.dark_mode": "dark", "taskbar.transparency": "on",
                  "shell.show_extensions": "show"},
        "terminal": {"foreground": "#DCEEFF", "background": "#070A12",
                     "cursor": "#7DE2FF", "font": "Cascadia Code", "opacity": 0.90},
    },
    "synthwave": {
        "meta": {"name": "Synthwave", "author": "built-in", "version": 1,
                 "note": "The horizon, the sun, the grid. No apologies."},
        "palette": {"bg": "#0A0714", "accent": "#FF4FD8",
                    "secondary": "#8E6FD0", "tint": "#2B1055"},
        "wallpaper": {"scene": "synth", "size": "1440p", "seed": 3,
                      "scrim": False, "vignette": 0.45},
        "widgets": [
            _w("clock-digital", 0.5, 0.10, 0.11, text="23:41", label="NIGHT DRIVE"),
            _w("system", 0.14, 0.16, 0.11, label="TELEMETRY",
               text="CPU 34% | GPU 88% | RAM 62% | NET 4.2 MB/S"),
        ],
        "shell": {"taskbar.align": "left", "shell.dark_mode": "dark",
                  "taskbar.combine": "never", "taskbar.transparency": "off"},
        "terminal": {"foreground": "#FFE6FA", "background": "#0A0714",
                     "cursor": "#FF4FD8", "font": "Cascadia Code", "opacity": 1.0},
    },
    "topo": {
        "meta": {"name": "Topo", "author": "built-in", "version": 1,
                 "note": "Contour lines. Looks surveyed; is arithmetic."},
        "palette": {"bg": "#0C1110", "accent": "#9CE8A0",
                    "secondary": "#54705C", "tint": "#17301F"},
        "wallpaper": {"scene": "topo", "size": "1440p", "seed": 21,
                      "scrim": True, "vignette": 0.50},
        "widgets": [
            _w("gauge-ring", 0.17, 0.22, 0.08, value=0.42, label="CPU"),
            _w("gauge-ring", 0.17, 0.44, 0.08, value=0.68, label="RAM"),
            _w("divider", 0.17, 0.57, 0.08),
            _w("system", 0.17, 0.72, 0.11, label="FIELD NOTES",
               text="DISK 31% | TEMP 54°C | FANS 820 RPM"),
        ],
        "shell": {"taskbar.align": "center", "shell.dark_mode": "dark",
                  "taskbar.combine": "never", "shell.show_extensions": "show"},
        "terminal": {"foreground": "#E2F5E5", "background": "#0C1110",
                     "cursor": "#9CE8A0", "font": "Cascadia Mono", "opacity": 0.96},
    },
    "dusk": {
        "meta": {"name": "Dusk", "author": "built-in", "version": 1,
                 "note": "Warm horizon, quiet widgets. The one that survives icons."},
        "palette": {"bg": "#120E14", "accent": "#FFB877",
                    "secondary": "#7A6472", "tint": "#3A2436"},
        "wallpaper": {"scene": "dusk", "size": "1440p", "seed": 31,
                      "scrim": True, "vignette": 0.40},
        "widgets": [
            _w("calendar", 0.16, 0.20, 0.10, label="NOVEMBER", text="23", detail="SUNDAY"),
            _w("gauge-bar", 0.16, 0.42, 0.08, value=0.42, label="CPU", detail="42%"),
            _w("gauge-bar", 0.16, 0.53, 0.08, value=0.68, label="RAM", detail="68%"),
            _w("battery", 0.16, 0.68, 0.08, value=0.74, text="74% 3H10M"),
        ],
        "shell": {"taskbar.align": "center", "shell.dark_mode": "dark",
                  "taskbar.transparency": "on"},
        "terminal": {"foreground": "#F4E7DC", "background": "#120E14",
                     "cursor": "#FFB877", "font": "Cascadia Code", "opacity": 0.95},
    },
    "orbit": {
        "meta": {"name": "Orbit", "author": "built-in", "version": 1,
                 "note": "Concentric rings. A target you can live inside."},
        "palette": {"bg": "#080A0F", "accent": "#8FD14F",
                    "secondary": "#4E5A70", "tint": "#1B2A44"},
        "wallpaper": {"scene": "orbit", "size": "1440p", "seed": 17,
                      "scrim": True, "vignette": 0.58},
        "widgets": [
            _w("clock-analog", 0.5, 0.5, 0.30, value=0.86, label=""),
            _w("label", 0.5, 0.86, 0.07, text="ORBIT / LOCAL ONLY"),
        ],
        "shell": {"taskbar.align": "center", "shell.dark_mode": "dark",
                  "taskbar.taskview": "off", "taskbar.widgets": "off"},
        "terminal": {"foreground": "#E6F2D8", "background": "#080A0F",
                     "cursor": "#8FD14F", "font": "Cascadia Mono", "opacity": 1.0},
    },
    "grain-paper": {
        "meta": {"name": "Grain", "author": "built-in", "version": 1,
                 "note": "Almost nothing. Behind a hundred icons, that is the point."},
        "palette": {"bg": "#111315", "accent": "#E6E8EC",
                    "secondary": "#5E646C", "tint": "#22262B"},
        "wallpaper": {"scene": "grain", "size": "1440p", "seed": 5,
                      "scrim": False, "vignette": 0.35},
        "widgets": [
            _w("clock-digital", 0.5, 0.06, 0.085, text="23:41", label=""),
            _w("divider", 0.5, 0.14, 0.10),
        ],
        "shell": {"taskbar.align": "center", "shell.dark_mode": "dark",
                  "taskbar.transparency": "off", "shell.show_extensions": "hide"},
        "terminal": {"foreground": "#D8DCE2", "background": "#111315",
                     "cursor": "#E6E8EC", "font": "Cascadia Mono", "opacity": 1.0},
    },
    "loft": {
        "meta": {"name": "Loft", "author": "built-in", "version": 1,
                 "note": "Hard diagonals. A poster that happens to be a wallpaper."},
        "palette": {"bg": "#0D0C0A", "accent": "#F2C14A",
                    "secondary": "#7A6A4E", "tint": "#2C2418"},
        "wallpaper": {"scene": "loft", "size": "1440p", "seed": 13,
                      "scrim": False, "vignette": 0.42},
        "widgets": [
            _w("label", 0.78, 0.12, 0.06, text="LOFT / 01"),
            _w("stat-block", 0.78, 0.80, 0.09, text="42", label="DEGREES"),
        ],
        "shell": {"taskbar.align": "left", "shell.dark_mode": "dark",
                  "taskbar.combine": "never", "taskbar.widgets": "on"},
        "terminal": {"foreground": "#F5EFE2", "background": "#0D0C0A",
                     "cursor": "#F2C14A", "font": "Cascadia Mono", "opacity": 1.0},
    },
    "mesh-mint": {
        "meta": {"name": "Mesh Mint", "author": "built-in", "version": 1,
                 "note": "Four colour poles, no straight lines anywhere."},
        "palette": {"bg": "#07100E", "accent": "#5FF2C2",
                    "secondary": "#4A8B84", "tint": "#10463F"},
        "wallpaper": {"scene": "mesh", "size": "1440p", "seed": 11,
                      "scrim": True, "vignette": 0.45},
        "widgets": [
            _w("gauge-bar", 0.16, 0.18, 0.085, value=0.55, label="CPU", detail="55%"),
            _w("gauge-bar", 0.16, 0.29, 0.085, value=0.72, label="RAM", detail="72%"),
            _w("stat-block", 0.16, 0.50, 0.09, text="23:41", label="MINT"),
        ],
        "shell": {"taskbar.align": "center", "shell.dark_mode": "dark",
                  "taskbar.transparency": "on"},
        "terminal": {"foreground": "#DFFFF6", "background": "#07100E",
                     "cursor": "#5FF2C2", "font": "Cascadia Code", "opacity": 0.92},
    },
    "terminal-green": {
        "meta": {"name": "Terminal", "author": "built-in", "version": 1,
                 "note": "Phosphor, but with the contrast fixed."},
        "palette": {"bg": "#040806", "accent": "#3BFF8F",
                    "secondary": "#2E7A52", "tint": "#0B2A18"},
        "wallpaper": {"scene": "grain", "size": "1440p", "seed": 9,
                      "scrim": False, "vignette": 0.30},
        "widgets": [
            _w("system", 0.16, 0.20, 0.10, label="SHELL",
               text="BOOT 3.2S | CPU 34% | RAM 62%"),
            _w("label", 0.5, 0.93, 0.06, text="READY"),
        ],
        "shell": {"taskbar.align": "left", "shell.dark_mode": "dark",
                  "taskbar.combine": "never", "shell.show_extensions": "show"},
        "terminal": {"foreground": "#3BFF8F", "background": "#040806",
                     "cursor": "#3BFF8F", "font": "Cascadia Mono", "opacity": 1.0},
    },
}

# The full gallery: built-ins plus a couple of variations generated at import so
# the count is real rather than padded.
for _name, _scene, _seed, _accent, _bg in (
    ("pulse", "orbit", 41, "#FF7A6B", "#0F0A0A"),
    ("paper", "mesh", 55, "#E8E2D4", "#12100C"),
    ("nocturne", "aurora", 77, "#C9A7FF", "#0A0812"),
    ("gridline", "synth", 90, "#66E0FF", "#06090F"),
    ("sandstone", "topo", 64, "#E0B884", "#12100C"),
):
    BUILTIN[_name] = {
        "meta": {"name": _name.title(), "author": "built-in", "version": 1,
                 "note": "Generated variation of the "
                         f"{_scene} scene."},
        "palette": {"bg": _bg, "accent": _accent, "secondary": "#6C6C74",
                    "tint": "#2A2A33"},
        "wallpaper": {"scene": _scene, "size": "1440p", "seed": _seed,
                      "scrim": True, "vignette": 0.5},
        "widgets": [
            _w("gauge-ring", 0.17, 0.22, 0.075, value=0.42, label="CPU"),
            _w("gauge-ring", 0.17, 0.42, 0.075, value=0.68, label="RAM"),
            _w("clock-digital", 0.17, 0.60, 0.085, text="23:41", label=""),
        ],
        "shell": {"taskbar.align": "center", "shell.dark_mode": "dark",
                  "taskbar.transparency": "on"},
        "terminal": {"foreground": "#E8E8EE", "background": _bg,
                     "cursor": _accent, "font": "Cascadia Code", "opacity": 0.95},
    }


# ------------------------------------------------------------------ checks
def validate(doc: dict[str, Any]) -> list[str]:
    """Every reason this document is not a theme. Empty list means it is.

    Strict on purpose. A theme file that half-applies is worse than one that
    refuses: the user cannot tell which half worked.
    """
    out: list[str] = []
    if not isinstance(doc, dict):
        return ["a theme must be a JSON object"]
    # Returning ten thousand lines of "this is wrong" is its own failure: the
    # dialog cannot show them and the useful ones scroll away. Report the first
    # few precisely and say how many more there were.
    limit = 12
    unknown = set(doc) - KNOWN_TOP
    if unknown:
        out.append(f"unknown top-level key(s): {', '.join(sorted(unknown))}")
    if doc.get("schema", SCHEMA) != SCHEMA:
        out.append(f"unsupported schema {doc.get('schema')!r} "
                   f"(this build reads {SCHEMA})")

    meta = doc.get("meta")
    if not isinstance(meta, dict) or not str(meta.get("name", "")).strip():
        out.append("meta.name is required")
    else:
        # The name is rendered into the gallery and the page title. A 100 KB
        # name is not a theme, and control characters would let it forge lines
        # in any place that logs it.
        name = str(meta.get("name"))
        if len(name) > 80:
            out.append(f"meta.name is {len(name)} characters; the limit is 80")
        elif any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
            out.append("meta.name contains control characters")
        for field in ("author", "description"):
            value = meta.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                out.append(f"meta.{field} must be text")
            elif len(value) > (120 if field == "author" else 600):
                out.append(f"meta.{field} is too long "
                           f"({len(value)}; limit {120 if field == 'author' else 600})")

    pal = doc.get("palette")
    if pal is not None:
        if not isinstance(pal, dict):
            out.append("palette must be an object")
        else:
            for key, value in pal.items():
                if key not in ("bg", "accent", "secondary", "tint"):
                    out.append(f"unknown palette key {key!r}")
                    continue
                try:
                    _hex_to_rgb(value)
                except ValueError as exc:
                    out.append(f"palette.{key}: {exc}")

    wall = doc.get("wallpaper")
    if wall is not None:
        if not isinstance(wall, dict):
            out.append("wallpaper must be an object")
        else:
            scene = wall.get("scene", "aurora")
            if scene not in SCENES:
                out.append(f"wallpaper.scene {scene!r} is not one of "
                           f"{', '.join(SCENES)}")
            size = wall.get("size", "1440p")
            # Named sizes, or explicit WxH — `compose` accepts both, so refusing
            # one here would reject a theme the renderer handles happily.
            ok = size in SIZES
            if not ok and isinstance(size, str) and "x" in size:
                try:
                    w, h = (int(v) for v in size.split("x", 1))
                    ok = 32 <= w <= 7680 and 32 <= h <= 4320
                except (ValueError, TypeError):
                    ok = False
            if not ok:
                out.append(f"wallpaper.size {size!r} is not one of "
                           f"{', '.join(SIZES)} or WxH")
            for key in ("seed", "vignette"):
                if key in wall and not isinstance(wall[key], (int, float)):
                    out.append(f"wallpaper.{key} must be a number")

    widgets = doc.get("widgets")
    if widgets is not None:
        if not isinstance(widgets, list):
            out.append("widgets must be a list")
        else:
            for i, wdg in enumerate(widgets):
                if not isinstance(wdg, dict):
                    out.append(f"widget {i}: must be an object")
                elif wdg.get("type") not in WIDGET_TYPES:
                    out.append(f"widget {i}: unknown type {wdg.get('type')!r}")

    shell = doc.get("shell")
    if shell is not None:
        if not isinstance(shell, dict):
            out.append("shell must be an object")
        else:
            bad = set(shell) - KNOWN_SHELL
            if bad:
                out.append(f"unknown shell key(s): {', '.join(sorted(bad))}")

    term = doc.get("terminal")
    if term is not None:
        if not isinstance(term, dict):
            out.append("terminal must be an object")
        else:
            bad = set(term) - KNOWN_TERMINAL
            if bad:
                out.append(f"unknown terminal key(s): {', '.join(sorted(bad))}")
            for key in ("foreground", "background", "cursor"):
                if key in term:
                    try:
                        _hex_to_rgb(term[key])
                    except ValueError as exc:
                        out.append(f"terminal.{key}: {exc}")

    if len(out) > limit:
        extra = len(out) - limit
        return out[:limit] + [f"and {extra} more problem{'s' if extra != 1 else ''}"]
    return out


def palette_of(doc: dict[str, Any]) -> Palette:
    pal = doc.get("palette") or {}
    return Palette(
        bg=_hex_to_rgb(pal.get("bg", "#0B0D10")),
        accent=_hex_to_rgb(pal.get("accent", "#B6F24A")),
        secondary=_hex_to_rgb(pal.get("secondary", "#5A6478")),
        tint=_hex_to_rgb(pal.get("tint", "#2A3A55")),
    )


def gallery() -> list[dict[str, Any]]:
    """Every theme, with a one-line summary the UI can show."""
    out = []
    for key, doc in BUILTIN.items():
        wall = doc.get("wallpaper", {})
        out.append({
            "id": key,
            "name": doc["meta"]["name"],
            "note": doc["meta"].get("note", ""),
            "scene": wall.get("scene"),
            "accent": (doc.get("palette") or {}).get("accent", "#B6F24A"),
            "bg": (doc.get("palette") or {}).get("bg", "#0B0D10"),
            "widgets": len(doc.get("widgets") or []),
        })
    return sorted(out, key=lambda t: t["name"])


def get(theme_id: str) -> dict[str, Any]:
    if theme_id not in BUILTIN:
        raise Absent(f"unknown theme {theme_id!r}; try {', '.join(sorted(BUILTIN))}")
    return json.loads(json.dumps(BUILTIN[theme_id]))


def export(doc: dict[str, Any]) -> str:
    """A theme as a pasteable string. Compact separators: people share these."""
    problems = validate(doc)
    if problems:
        raise ValueError("; ".join(problems))
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False)


def import_(text: str) -> tuple[dict[str, Any], list[str]]:
    """Parse and check a shared theme. Returns (doc, problems); never raises
    on bad input, because the caller has to show the user why."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, [f"not valid JSON: {exc.msg} at line {exc.lineno}"]
    return doc, validate(doc)


def diff(current: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """What applying `new` would change, in a form a preview can render.

    Only leaves are compared; a whole section that is equal produces no entries.
    This is what makes "show me before you apply" true rather than decorative.
    """
    out: list[dict[str, Any]] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                walk(a.get(key), b.get(key), f"{path}.{key}" if path else key)
        elif a != b:
            out.append({"key": path, "from": a, "to": b})

    walk(current, new, "")
    return out


def widget_warnings(doc: dict[str, Any]) -> list[str]:
    """Anything about the widget layer a user should know before applying."""
    return live_warnings(doc.get("wallpaper") or {"widgets": doc.get("widgets") or []})


def summary(doc: dict[str, Any]) -> str:
    """One line, for a receipt or a toast."""
    name = (doc.get("meta") or {}).get("name", "theme")
    wall = doc.get("wallpaper") or {}
    return (f"{name}: {wall.get('scene', 'aurora')} @ {wall.get('size', '1440p')}, "
            f"{len(doc.get('widgets') or [])} widgets, "
            f"{(doc.get('palette') or {}).get('accent', '#B6F24A')}")


def wallpaper_problems_for(doc: dict[str, Any]) -> list[str]:
    """Validate the wallpaper section as a standalone spec."""
    wall = dict(doc.get("wallpaper") or {})
    wall["widgets"] = doc.get("widgets") or []
    return wallpaper_problems(wall)
