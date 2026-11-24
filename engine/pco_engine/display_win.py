"""The ctypes half of display control. Small on purpose.

Everything that can be decided is decided in `display.py`; this file only makes
the three calls that need a real Windows desktop. Keeping it this thin means the
part that cannot be tested on Linux is also the part with almost no logic in it.

Implemented against EnumDisplaySettingsExW / ChangeDisplaySettingsExW, the
documented Win32 API, with CDS_TEST used as a pre-flight. `CDS_UPDATEREGISTRY`
is deliberately *not* passed: a mode the user is about to lose should not be
written into the registry as their preference.
"""
from __future__ import annotations

import sys

from .display import DisplayError, Mode


class WinDisplay:
    """`Display` protocol over Win32. Raises DisplayError, never a bare OSError."""

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise DisplayError(
                "display control needs Windows; this build was imported on "
                f"{sys.platform}. Nothing was changed.")
        self._user32 = self._load()

    # ------------------------------------------------------------- loading
    @staticmethod
    def _load():
        import ctypes
        from ctypes import wintypes

        class DEVMODE(ctypes.Structure):
            _fields_ = [
                ("dmDeviceName", wintypes.WCHAR * 32),
                ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("dmPositionX", wintypes.LONG),
                ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", wintypes.SHORT),
                ("dmDuplex", wintypes.SHORT),
                ("dmYResolution", wintypes.SHORT),
                ("dmTTOption", wintypes.SHORT),
                ("dmCollate", wintypes.SHORT),
                ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD),
                ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD),
                ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD),
                ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD),
                ("dmPanningHeight", wintypes.DWORD),
            ]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.EnumDisplaySettingsExW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODE), wintypes.DWORD]
        user32.EnumDisplaySettingsExW.restype = wintypes.BOOL
        user32.ChangeDisplaySettingsExW.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(DEVMODE), wintypes.HWND,
            wintypes.DWORD, wintypes.LPVOID]
        user32.ChangeDisplaySettingsExW.restype = wintypes.LONG
        return user32, DEVMODE, ctypes

    # ------------------------------------------------------------- protocol
    def current(self) -> Mode:
        user32, DEVMODE, ctypes = self._user32
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        if not user32.EnumDisplaySettingsExW(None, -1, ctypes.byref(dm), 0):
            raise DisplayError("Windows could not report the current display mode")
        return Mode(int(dm.dmPelsWidth), int(dm.dmPelsHeight),
                    int(dm.dmDisplayFrequency))

    def can_apply(self, mode: Mode) -> bool:
        return self._change(mode, cds=0x00000002) == 0      # CDS_TEST

    def apply(self, mode: Mode) -> None:
        # CDS_FULLSCREEN is not used: the request is a mode change, not a
        # temporary fullscreen switch, and the watchdog owns the revert.
        code = self._change(mode, cds=0x00000000)
        if code != 0:
            raise DisplayError(_explain(code))

    def _change(self, mode: Mode, *, cds: int) -> int:
        user32, DEVMODE, ctypes = self._user32
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        dm.dmPelsWidth = mode.width
        dm.dmPelsHeight = mode.height
        dm.dmDisplayFrequency = mode.hz
        dm.dmFields = 0x00080000 | 0x00100000 | 0x00400000   # WIDTH|HEIGHT|FREQ
        return int(user32.ChangeDisplaySettingsExW(None, ctypes.byref(dm), None,
                                                   cds, None))


_DISP_CHANGE = {
    -1: "DISP_CHANGE_FAILED — the driver refused the mode",
    -2: "DISP_CHANGE_BADMODE — the mode is not supported by this display",
    -3: "DISP_CHANGE_NOTUPDATED — the mode could not be written",
    -4: "DISP_CHANGE_BADFLAGS",
    -5: "DISP_CHANGE_BADPARAM",
    -6: "DISP_CHANGE_BADDUALVIEW — a multi-monitor driver rejected it",
}


def _explain(code: int) -> str:
    return _DISP_CHANGE.get(code, f"Windows returned {code}")
