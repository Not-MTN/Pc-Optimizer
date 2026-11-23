"""Display mode changes, with the part that can blind you tested separately.

Changing a display mode is the one operation in this app that can leave a screen
black with no way back. The failure is not "the setting was wrong" — it is
"the user cannot see the window that would undo it". So the safety property is
not a confirmation dialog. It is a **watchdog**:

    1. read the current mode and keep it
    2. CDS_TEST the new mode — Windows answers whether it can, without applying
    3. apply it
    4. start a timer; if nobody confirms within N seconds, revert, unconditionally

Step 4 runs on a thread that does not depend on the UI, the HTTP server, or the
user's eyesight. If the console dies, the watchdog still reverts. Ten seconds of
a black screen is survivable; a black screen that stays is not.

This module contains the state machine and nothing else. The ctypes calls live
behind the `Display` protocol and are supplied by `windows_backend`, which is
why the decisions here run on Linux in CI on every commit while the actual
`ChangeDisplaySettingsExW` call is exercised only on Windows.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


class DisplayError(RuntimeError):
    """A display operation Windows refused, or one the watchdog prevented."""


@dataclass(frozen=True)
class Mode:
    width: int
    height: int
    hz: int

    def as_dict(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height, "hz": self.hz}

    def __str__(self) -> str:                       # "1920x1080 @ 144Hz"
        return f"{self.width}x{self.height} @ {self.hz}Hz"


class Display(Protocol):
    """The three calls a display change needs. Deliberately tiny."""

    def current(self) -> Mode: ...

    def can_apply(self, mode: Mode) -> bool:
        """CDS_TEST — does Windows accept this mode, without applying it?"""

    def apply(self, mode: Mode) -> None: ...


@dataclass
class Pending:
    """A change that has been applied and is awaiting confirmation."""

    previous: Mode
    target: Mode
    armed_at: float
    timeout: float
    confirmed: bool = False
    reverted: bool = False
    _timer: threading.Timer | None = field(default=None, repr=False)

    @property
    def expires_in(self) -> float:
        return max(0.0, self.armed_at + self.timeout - time.monotonic())


class DisplayGuard:
    """Apply a mode, and guarantee it is undone unless someone says it is fine.

    `timeout` is deliberately short. A long countdown does not make a black
    screen safer; it makes it last longer.
    """

    def __init__(self, display: Display, *, timeout: float = 12.0,
                 clock=time.monotonic) -> None:
        self._display = display
        self._timeout = timeout
        self._clock = clock
        self._pending: Pending | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- state
    @property
    def pending(self) -> Pending | None:
        return self._pending

    def status(self) -> dict[str, Any]:
        p = self._pending
        if p is None:
            return {"pending": False}
        return {"pending": True, "target": p.target.as_dict(),
                "previous": p.previous.as_dict(),
                "expires_in": round(p.expires_in, 1)}

    # ------------------------------------------------------------- apply
    def apply(self, mode: Mode) -> dict[str, Any]:
        with self._lock:
            if self._pending is not None and not self._pending.confirmed:
                raise DisplayError(
                    "a mode change is already awaiting confirmation — confirm or "
                    "revert it before starting another, or the watchdog and the "
                    "second change will disagree about what 'restore' means")

            previous = self._display.current()
            if (mode.width, mode.height, mode.hz) == (
                    previous.width, previous.height, previous.hz):
                return {"changed": False, "reason": "already at that mode",
                        "mode": previous.as_dict()}

            if not self._display.can_apply(mode):
                raise DisplayError(
                    f"{mode} is not a supported mode on this display. Windows "
                    f"rejected it at CDS_TEST, before anything was changed.")

            self._display.apply(mode)

            p = Pending(previous=previous, target=mode,
                        armed_at=self._clock(), timeout=self._timeout)
            timer = threading.Timer(self._timeout, self._on_timeout_id)
            timer.daemon = True
            p._timer = timer
            self._pending = p
            timer.start()

            return {"changed": True, "from": previous.as_dict(),
                    "to": mode.as_dict(), "confirm_within": self._timeout,
                    "note": ("If you cannot see this, the previous mode is "
                             "restored automatically.")}

    # --------------------------------------------------- confirm / revert
    def confirm(self) -> dict[str, Any]:
        with self._lock:
            p = self._pending
            if p is None:
                return {"confirmed": False, "reason": "nothing is pending"}
            p.confirmed = True
            if p._timer is not None:
                p._timer.cancel()
            self._pending = None
            return {"confirmed": True, "kept": p.target.as_dict()}

    def revert(self, *, reason: str = "reverted on request") -> dict[str, Any]:
        with self._lock:
            p = self._pending
            if p is None:
                return {"reverted": False, "reason": "nothing is pending"}
            p._timer and p._timer.cancel()
            self._display.apply(p.previous)
            p.reverted = True
            self._pending = None
            return {"reverted": True, "restored": p.previous.as_dict(),
                    "reason": reason}

    # ------------------------------------------------------------- watchdog
    def _on_timeout_id(self) -> None:
        """The timer body. Never depends on the caller, the UI, or a thread pool."""
        with self._lock:
            p = self._pending
            if p is None or p.confirmed:
                return
            try:
                self._display.apply(p.previous)
            except Exception:                      # noqa: BLE001
                # If the revert itself fails there is nothing better to do than
                # leave the record in place: the journal will still show what was
                # attempted, which is the difference between a bad night and a
                # machine that has to be reinstalled.
                p.reverted = False
                self._pending = p
                return
            p.reverted = True
            self._pending = None
