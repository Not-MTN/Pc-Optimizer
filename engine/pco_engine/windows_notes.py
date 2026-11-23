"""Windows platform notes the backend acts on, kept as data.

These live in their own module because they are *claims about Windows* rather
than logic, and claims should be reviewable in one place. Every entry here is
something that is true on Windows 11 24H2 and is easy to get wrong.

The module is imported by `windows_backend` and by `shell_lab`; nothing here
imports anything, so it is safe to read from any platform.
"""
from __future__ import annotations

# --------------------------------------------------------------- registry view
# A 32-bit Python on 64-bit Windows silently reads the WOW6432Node copy of these
# keys, which for the policies below is a *different* set of values that governs
# nothing. Every read and write must pass KEY_WOW64_64KEY.
FORCE_64BIT_VIEW = True

# ---------------------------------------------------------------- explorer
# Restarting Explorer applies taskbar changes. It also closes every File Explorer
# window and, briefly, the desktop. It is never done without being asked.
EXPLORER_RESTART_CLOSES_FOLDERS = True

# Values that exist but which 24H2 accepts and then ignores. Writing one and
# reporting success would be the exact dishonesty this project forbids, so the
# app re-reads after writing and reports "did not stick" if it did not.
ACCEPTED_BUT_IGNORED_24H2 = {
    "taskbar.combine": (
        "TaskbarGlomLevel is stored, but the primary taskbar in 24H2 always "
        "groups and hides labels. It still affects secondary displays."),
}

# Things people ask for that Windows 11 genuinely removed. Each explains what it
# would have taken, so the answer is useful rather than merely negative.
REMOVED_IN_WINDOWS_11 = {
    "taskbar_position": (
        "Top/side taskbar docking was removed. The taskbar is bottom-only, and "
        "the documented workarounds are unsupported registry hacks that Explorer "
        "resets after the next update."),
    "taskbar_labels": (
        "Window titles on the primary taskbar are gone. 'Never combine' still "
        "exists for secondary monitors only."),
    "taskbar_transparency_full": (
        "There is no supported fully-transparent taskbar. Third-party tools do it "
        "by patching Explorer's XAML resources; the usual result after a Windows "
        "update is a taskbar that stops responding."),
    "start_flyout_classic": (
        "The Windows 7 / 10 style Start requires a shell replacement "
        "(StartAllBack, Open-Shell, ExplorerPatcher). That is a separate product "
        "with its own update risk and its own licence; this app will not install "
        "one silently."),
}

# ------------------------------------------------------------------- icons
# The only icons Windows lets a normal program change per-user without a shell
# extension, and where the value lives. Paths are under HKCU, so no elevation.
PER_USER_ICONS = {
    "icons.recycle_bin_full": (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\CLSID"
        r"\{645FF040-5081-101B-9F08-00AA002F954E}\DefaultIcon",
        "full"),
    "icons.recycle_bin_empty": (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\CLSID"
        r"\{645FF040-5081-101B-9F08-00AA002F954E}\DefaultIcon",
        "empty"),
    "icons.this_pc": (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\CLSID"
        r"\{20D04FE0-3AEA-1069-A2D8-08002B30309D}\DefaultIcon",
        None),
    "icons.user_folder": (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\CLSID"
        r"\{59031A47-3F72-44A7-89C5-5595FE6B30EE}\DefaultIcon",
        None),
}

# The recycle bin stores two values in one key; using the wrong one changes the
# icon that is *not* showing and looks like the feature is broken.
RECYCLE_BIN_VALUES = {"full": "Full", "empty": "Empty"}

# --------------------------------------------------------------- terminal
# Windows Terminal's settings.json. It is JSON with a schema, so the app can
# parse → edit named keys → write back, preserving everything it does not own.
TERMINAL_SETTINGS_CANDIDATES = (
    "%LOCALAPPDATA%\\Packages\\Microsoft.WindowsTerminal_8wekyb3d8bbwe"
    "\\LocalState\\settings.json",
    "%LOCALAPPDATA%\\Microsoft\\Windows Terminal\\settings.json",
)

# ------------------------------------------------------------------ prompt
# Where a shell profile can read the provider mark from. The app writes one
# environment variable, which is the least invasive way to get a logo into
# PowerShell without owning the user's profile script.
PROMPT_ENV_VARS = {
    "PCO_PROMPT_PROVIDER": "provider key, e.g. github",
    "PCO_PROMPT_GLYPH": "SVG path data for the mark",
    "PCO_PROMPT_COLOR": "hex colour that suits the detected provider",
}
