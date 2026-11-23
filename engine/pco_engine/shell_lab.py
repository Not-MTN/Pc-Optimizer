"""Desktop, taskbar, icon and prompt customization — the *taste* layer.

Three rules shaped this module, and they outrank "make it look cool":

1. **Nothing is irreversible.** Every customization is a registry value or a
   file the app created. The values are journaled through the same Engine, so
   one click undoes it, and nothing here touches anything the user made.

2. **Windows 11 is explicit about what it will not do.** A fully transparent
   taskbar, a taskbar moved to the top of the screen, Windows 7-style flyouts —
   Explorer in 24H2 refuses all of them, and every "exploit" that forced them
   recreates a control panel that stops working on the next update. This module
   lists those requests and refuses them *by name, with the reason*, rather than
   offering a toggle that quietly does nothing.

3. **A theme is data, never advice.** An imported theme is validated before it
   can be applied, and an unknown key is an error rather than a shrug — an
   import that half-works is how a desktop ends up in a state nobody planned.

What is genuinely possible on Windows 11 24H2 is narrower than the marketing of
third-party "taskbar tools", and that is the honest product: the things below
actually change Explorer, because each one is a documented registry value.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# ============================================================ what is possible
# Each entry is a real, documented registry value under the current user's hive.
# `needs_restart` is Explorer, not the machine — that distinction matters because
# restarting Explorer closes every File Explorer window the user has open.
DEFAULT_HIVE = "hcru"          # placeholder replaced below; kept for clarity
USER_HIVE = "hkcu"

# --------------------------------------------------------------------- taskbar
TASKBAR = {
    "taskbar.align": {
        "value": "TaskbarAl",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"left": 0, "center": 1},
        "default": "center",
        "plain": "Where the icons sit. Left is what Windows 10 muscle memory wants.",
        "restart": "explorer",
    },
    "taskbar.search": {
        "value": "SearchboxTaskbarMode",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Search",
        "options": {"hidden": 0, "icon": 1, "box": 2, "label": 3},
        "default": "box",
        "plain": "The search box takes ~180 px of the bar. Icon-only keeps the shortcut.",
        "restart": "explorer",
    },
    "taskbar.widgets": {
        "value": "TaskbarDa",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"off": 0, "on": 1},
        "default": "on",
        "plain": "The weather widget. Off by default in this app because it wakes a task.",
        "restart": "explorer",
    },
    "taskbar.chat": {
        "value": "TaskbarMn",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"off": 0, "on": 1},
        "default": "on",
        "plain": "The Teams/Chat button. Off reclaims space; Teams still opens normally.",
        "restart": "explorer",
    },
    "taskbar.taskview": {
        "value": "ShowTaskViewButton",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"off": 0, "on": 1},
        "default": "on",
        "plain": "The Task View button. Win+Tab does the same thing without the pixels.",
        "restart": "explorer",
    },
    "taskbar.combine": {
        "value": "TaskbarGlomLevel",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        # Win11 no longer honours this for window grouping, but it still governs
        # how labels behave on secondary monitors in 24H2. Offered with that caveat.
        "options": {"always": 0, "when_full": 1, "never": 2},
        "default": "always",
        "plain": ("Group windows and hide labels. On 24H2 this only affects "
                  "secondary displays — the main taskbar ignores it."),
        "restart": "explorer",
        "caveat": "Windows 11 ignores this on the primary taskbar.",
    },
    "taskbar.transparency": {
        "value": "EnableTransparency",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "options": {"off": 0, "on": 1},
        "default": "on",
        "plain": "The acrylic wash behind the bar, Start and Action Center.",
        "restart": "none",
    },
    "taskbar.accent": {
        "value": "ColorPrevalence",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "options": {"off": 0, "on": 1},
        "default": "off",
        "plain": "Tint the taskbar with your accent colour instead of the material.",
        "restart": "none",
    },
    "taskbar.small_icons": {
        "value": "TaskbarSmallIcons",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"off": 0, "on": 1},
        "default": "off",
        "plain": "Smaller icons, a shorter bar. On 24H2 this needs the Explorer restart.",
        "restart": "explorer",
        "caveat": "Microsoft has been trying to remove this; it may stop working.",
    },
}

# ------------------------------------------------------------------------ shell
SHELL = {
    "shell.show_extensions": {
        "value": "HideFileExt",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"show": 0, "hide": 1},
        "default": "hide",
        "plain": ("Show file extensions. A PDF that is really an .exe is the oldest "
                  "trick there is, and Windows hides the evidence by default."),
        "restart": "explorer",
    },
    "shell.show_hidden": {
        "value": "Hidden",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"show": 1, "hide": 2},
        "default": "hide",
        "plain": "Reveal hidden files. Useful, and a good way to delete something vital — be careful.",
        "restart": "explorer",
    },
    "shell.dark_mode": {
        "value": "AppsUseLightTheme",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        # inverted: the value is "use light", the option is "dark"
        "options": {"dark": 0, "light": 1},
        "default": "light",
        "plain": "Dark mode for apps. Matches this console, which is not a coincidence.",
        "restart": "none",
    },
    "shell.system_dark": {
        "value": "SystemUsesLightTheme",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "options": {"dark": 0, "light": 1},
        "default": "light",
        "plain": "Dark mode for the taskbar, Start and File Explorer chrome.",
        "restart": "explorer",
    },
    "shell.snap_layouts": {
        "value": "EnableSnapAssistFlyout",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"off": 0, "on": 1},
        "default": "on",
        "plain": "Hovering maximise shows the snap grid. Off is for people who hate it.",
        "restart": "explorer",
    },
    "shell.animations": {
        "value": "TaskbarAnimations",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "options": {"off": 0, "on": 1},
        "default": "on",
        "plain": "Taskbar animations. Off is a small, real saving on older GPUs.",
        "restart": "explorer",
    },
    "shell.bing_search": {
        "value": "BingSearchEnabled",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Search",
        "options": {"off": 0, "on": 1},
        "default": "on",
        "plain": "Stop Start-menu search sending your query to Bing.",
        "restart": "explorer",
    },
}

# ------------------------------------------------------- terminal / command line
#
# Windows Terminal reads one JSON file. Editing it is safe *because* it is JSON
# with a known schema, and because this module only touches the keys it names and
# keeps everything else byte-for-byte by round-tripping the parsed document.
TERMINAL = {
    "cmd.default_profile": {
        "plain": "Which shell opens. PowerShell 7 if installed, else Windows PowerShell.",
    },
    "cmd.font_face": {
        "plain": "Cascadia Code ships with Terminal and has the ligatures people want.",
    },
    "cmd.font_size": {
        "plain": "Point size. 11 reads well at 1440p and above.",
    },
    "cmd.cursor_shape": {
        "plain": "bar, vintage (underscore) or filledBox.",
    },
    "cmd.opacity": {
        "plain": "Acrylic-ish translucency, 0–100. Below 60 gets hard to read.",
    },
    "cmd.acrylic": {
        "plain": "The blur behind the terminal. It costs a little GPU time.",
    },
}

# Requests Windows 11 genuinely refuses. Naming them is the feature: a settings
# page that hides these leaves the user hunting a third-party tool that will
# break their Explorer on the next update.
REFUSED = {
    "taskbar.transparent": (
        "A fully transparent taskbar needs Explorer patching (the old "
        "'TransparentTB' hacks). Windows 11 24H2 reverts or breaks under them, and "
        "the usual result is a bar that stops responding. What is possible: "
        "taskbar.transparency off + a dark wallpaper, which reads as clean."),
    "taskbar.position": (
        "Moving the taskbar to the top or side is gone in Windows 11. The "
        "supported alternative is auto-hide."),
    "taskbar.autohide": (
        "Supported, but it trades a strip of screen for a moving target. Not "
        "offered here because it is a preference, not a fix — set it in Settings."),
    "taskbar.labels": (
        "Window labels on the primary taskbar were removed in Windows 11 and no "
        "supported value brings them back."),
    "startmenu.win7": (
        "Replacing Start needs a shell replacement (StartAllBack, Open-Shell). "
        "That is a different program with its own update risk; this app will not "
        "install one silently."),
}

# ---------------------------------------------------------------------- icons
# "Special icons" done honestly: these are the four things Windows actually lets
# a program change per-user without shipping a shell extension.
ICONS = {
    "icons.recycle_bin_full": {"plain": "A custom recycle-bin (full) icon."},
    "icons.recycle_bin_empty": {"plain": "A custom recycle-bin (empty) icon."},
    "icons.this_pc": {"plain": "The This PC icon — the one on the desktop."},
    "icons.user_folder": {"plain": "Your user folder icon in Explorer."},
}
ICON_KEYS = {
    # CLSID / namespace, the *value name* inside it, and the Windows default.
    # The recycle bin keeps both icons as two named values in ONE key: writing
    # `(Default)` changes whichever one is showing and silently leaves the other,
    # which reads as "the feature half-works".
    "icons.recycle_bin_full": (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\CLSID"
        r"\{645FF040-5081-101B-9F08-00AA002F954E}\DefaultIcon",
        "Full", r"%SystemRoot%\System32\imageres.dll,-54"),
    "icons.recycle_bin_empty": (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\CLSID"
        r"\{645FF040-5081-101B-9F08-00AA002F954E}\DefaultIcon",
        "Empty", r"%SystemRoot%\System32\imageres.dll,-55"),
}

# A Windows icon reference is `some.ico` or `some.dll,-54` — the comma and the
# index are part of the syntax, not decoration. Anchoring on `$` rejected every
# library reference, which is how most system icons are actually specified.
VALID_ICON_SUFFIX = re.compile(r"\.(ico|dll|exe)(,-?\d+)?$", re.IGNORECASE)


# ==================================================================== the module
@dataclass
class Change:
    key: str
    label: str
    address: str
    value: Any
    previous: Any
    plain: str = ""
    restart: str = "none"
    caveat: str = ""
    # Explorer keeps ten of these in one registry key, so the value name is part
    # of the change, not a detail. Dropping it silently writes the wrong setting.
    value_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "address": self.address,
                "value": self.value, "previous": self.previous,
                "plain": self.plain, "restart": self.restart,
                "caveat": self.caveat, "value_name": self.value_name}


class ShellLab:
    """Reads, validates and applies desktop customization.

    It never writes directly: it produces the exact (address, value) pairs and
    hands them to the Engine, so a theme lands in the journal with before/after
    values like every other change in the app.
    """

    def __init__(self, backend, clock) -> None:
        self.backend = backend
        self.clock = clock

    # ------------------------------------------------------------- discovery
    def options(self) -> list[dict[str, Any]]:
        """Everything settable, with the current value read from the machine."""
        out = []
        for group, table in (("taskbar", TASKBAR), ("shell", SHELL)):
            for key, spec in table.items():
                address = self._address(spec)
                current = None
                try:
                    raw = self._read(spec)
                    current = next((n for n, v in spec["options"].items()
                                    if v == raw), raw)
                except Exception:            # noqa: BLE001 - absent is a real answer
                    current = None
                out.append({
                    "key": key, "group": group, "label": key.split(".", 1)[1]
                    .replace("_", " "),
                    "plain": spec["plain"], "current": current,
                    "default": spec["default"],
                    "options": list(spec["options"]),
                    "supported": True,
                    "caveat": spec.get("caveat", ""),
                    "restart": spec.get("restart", "none"),
                    "address": address,
                })
        for key, why in REFUSED.items():
            out.append({"key": key, "group": key.split(".", 1)[0],
                        "label": key.split(".", 1)[1].replace("_", " "),
                        "plain": why, "current": None, "default": None,
                        "options": [], "supported": False,
                        "caveat": "", "restart": "none", "address": ""})
        return out

    def plan(self, desired: dict[str, str]) -> dict[str, Any]:
        """Validate a set of choices and return the writes, or the reasons not to."""
        changes: list[Change] = []
        problems: list[str] = []
        refused: list[dict[str, str]] = []

        for key, choice in desired.items():
            if key in REFUSED:
                refused.append({"key": key, "reason": REFUSED[key]})
                continue
            spec = TASKBAR.get(key) or SHELL.get(key)
            if spec is None:
                problems.append(
                    f"{key}: not a setting this app knows. Refusing rather than "
                    f"writing a registry value nobody has verified.")
                continue
            if choice not in spec["options"]:
                problems.append(
                    f"{key}: '{choice}' is not one of {sorted(spec['options'])}")
                continue
            address = self._address(spec)
            previous = None
            try:
                previous = self._read(spec)
            except Exception:                # noqa: BLE001 - creating is fine here
                previous = None
            value = spec["options"][choice]
            if previous == value:
                continue                     # already correct: not a change
            changes.append(Change(
                key=key, label=key.split(".", 1)[1].replace("_", " "),
                address=address, value=value, previous=previous,
                plain=spec["plain"], restart=spec.get("restart", "none"),
                caveat=spec.get("caveat", ""),
                value_name=spec.get("value")))

        restarts = {c.restart for c in changes if c.restart != "none"}
        return {
            "ok": not problems and bool(changes),
            "changes": [c.as_dict() for c in changes],
            "problems": problems,
            "refused": refused,
            "explorer_restart_needed": "explorer" in restarts,
            "addresses": [c.address for c in changes],
            "summary": self._summary(changes, problems, refused),
        }

    # ---------------------------------------------------------------- themes
    def validate_theme(self, doc: Any) -> list[str]:
        """A theme is a dict of our own keys. Unknown keys are errors."""
        if not isinstance(doc, dict):
            return ["a theme must be a JSON object"]
        problems: list[str] = []
        if not isinstance(doc.get("name"), str) or not doc["name"].strip():
            problems.append("a theme needs a non-empty \"name\"")
        settings = doc.get("settings", {})
        if not isinstance(settings, dict):
            return problems + ["\"settings\" must be an object of key → choice"]
        known = set(TASKBAR) | set(SHELL)
        for key, choice in settings.items():
            if key in REFUSED:
                problems.append(
                    f"{key}: {REFUSED[key]}")
                continue
            spec = TASKBAR.get(key) or SHELL.get(key)
            if spec is None:
                problems.append(f"{key}: unknown setting")
            elif choice not in spec["options"]:
                problems.append(
                    f"{key}: '{choice}' is not one of {sorted(spec['options'])}")
        # a theme that touches nothing is not a theme
        if not problems and not settings:
            problems.append("this theme changes nothing")
        return problems

    def theme_document(self, name: str, desired: dict[str, str]) -> dict[str, Any]:
        return {"name": name, "v": 1, "settings": dict(desired)}

    # ---------------------------------------------------------------- icons
    def plan_icon(self, key: str, path: str) -> dict[str, Any]:
        """Set one of the four per-user icons Windows supports. Path is validated.

        A wrong icon path does not corrupt anything — Explorer falls back to the
        default — which is exactly why this is the safest customization here.
        """
        if key not in ICON_KEYS:
            return {"ok": False,
                    "problems": [f"{key}: only these can be changed per-user: "
                                 f"{sorted(ICON_KEYS)}"]}
        if not VALID_ICON_SUFFIX.search(path or ""):
            return {"ok": False,
                    "problems": ["icon must be a .ico, or a .dll/.exe with an "
                                 "optional ,-index"]}
        registry_path, value_name, default = ICON_KEYS[key]
        # Registry addresses in this app are lowercase and hive-prefixed. The
        # value name travels in the payload because `write(address, value)` has
        # no place for it — the backend's address table resolves it.
        address = "registry:" + USER_HIVE + "\\" + registry_path.lower()
        return {"ok": True, "address": address, "value": path,
                "registry_value_name": value_name,
                "default": default,
                "plain": ICONS[key]["plain"],
                "problems": [], "summary": f"{key} → {path}"}

    # ------------------------------------------------------------- terminal
    def terminal_settings(self) -> dict[str, Any]:
        """The keys this app would set in Windows Terminal's settings.json."""
        return {
            "defaultProfile": "auto (PowerShell 7 if installed)",
            "profiles.defaults": {
                "font": {"face": "Cascadia Code", "size": 11.0},
                "cursorShape": "bar",
                "opacity": 92,
                "useAcrylic": True,
                "padding": "8, 4",
            },
            "note": ("This app writes only these keys, into a copy of your "
                     "settings.json. Everything else is preserved because the "
                     "document is parsed, not rebuilt."),
        }

    def _address(self, spec: dict[str, Any]) -> str:
        return "registry:" + USER_HIVE + "\\" + spec["path"].lower()

    def _read(self, spec: dict[str, Any]) -> Any:
        """Read through the value name, so ten settings in one key stay distinct."""
        try:
            return self.backend.read(self._address(spec), value_name=spec["value"])
        except TypeError:
            # A backend that predates value_name: fall back rather than crash.
            return self.backend.read(self._address(spec))

    @staticmethod
    def _summary(changes: list[Change], problems: list[str],
                 refused: list[dict[str, str]]) -> str:
        if problems:
            return f"{len(problems)} problem(s) — nothing would be changed."
        if refused and not changes:
            return "Nothing applied: every request here is one Windows refuses."
        parts = []
        if changes:
            parts.append(f"{len(changes)} change(s)")
        if refused:
            parts.append(f"{len(refused)} refused by Windows")
        return " · ".join(parts) if parts else "Nothing to change."
