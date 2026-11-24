"""The display watchdog.

This is the only feature in the app whose failure mode is "the user cannot see
the undo button". Every test here is about the guarantee that survives that:
a mode change that is not confirmed inside the window reverts itself, on a
thread that does not depend on the caller being alive.
"""
from __future__ import annotations

import threading
import time

import pytest

from pco_engine.display import DisplayError, DisplayGuard, Mode


class FakeDisplay:
    """Records every call; `can_apply` is a set of allowed modes."""

    def __init__(self, current: Mode, allowed: set[Mode] | None = None) -> None:
        self._current = current
        self._allowed = allowed if allowed is not None else {current}
        self.applied: list[Mode] = []
        self.tested: list[Mode] = []
        self.fail_apply: str | None = None

    def current(self) -> Mode:
        return self._current

    def can_apply(self, mode: Mode) -> bool:
        self.tested.append(mode)
        return mode in self._allowed

    def apply(self, mode: Mode) -> None:
        if self.fail_apply is not None:
            raise DisplayError(self.fail_apply)
        self.applied.append(mode)
        self._current = mode


FHD = Mode(1920, 1080, 60)
HD = Mode(1280, 720, 60)
HIGH = Mode(1920, 1080, 165)


def test_a_supported_mode_is_applied_after_a_test():
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=5)
    out = g.apply(HIGH)
    assert out["changed"] and out["from"] == FHD.as_dict()
    assert d.tested == [HIGH], "must CDS_TEST before applying — never blind-apply"
    assert d.applied == [HIGH]


def test_an_unsupported_mode_is_never_applied():
    """CDS_TEST is the entire point: refuse before changing anything."""
    d = FakeDisplay(FHD, allowed={FHD})
    with pytest.raises(DisplayError, match="not a supported mode"):
        DisplayGuard(d, timeout=5).apply(HIGH)
    assert d.applied == [], "nothing may be applied when the test fails"


def test_setting_the_current_mode_is_a_noop_not_an_error():
    d = FakeDisplay(FHD)
    out = DisplayGuard(d, timeout=5).apply(FHD)
    assert out["changed"] is False and d.applied == []


def test_confirm_keeps_the_mode_and_disarms_the_watchdog():
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=0.3)
    g.apply(HIGH)
    assert g.confirm()["confirmed"] is True
    time.sleep(0.5)
    assert d.applied == [HIGH], "a confirmed mode must not be reverted later"
    assert g.status() == {"pending": False}


def test_without_confirmation_the_mode_reverts_itself():
    """The whole feature. Nobody confirms; the screen must come back."""
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=0.25)
    g.apply(HIGH)
    assert d.current() == HIGH
    time.sleep(0.6)
    assert d.applied == [HIGH, FHD], "the watchdog did not restore the old mode"
    assert d.current() == FHD


def test_the_watchdog_survives_the_object_being_dropped():
    """The timer holds its own reference. Garbage collection must not disarm it.

    This is not theoretical: a request handler that returns creates exactly this
    situation, and a watchdog that only works while someone holds a reference is
    a watchdog that works in tests and not in production.
    """
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    DisplayGuard(d, timeout=0.2).apply(HIGH)
    import gc
    gc.collect()
    time.sleep(0.55)
    assert d.current() == FHD


def test_manual_revert_is_immediate_and_reports_what_it_restored():
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=30)
    g.apply(HIGH)
    out = g.revert()
    assert out["reverted"] is True and out["restored"] == FHD.as_dict()
    assert d.current() == FHD


def test_revert_after_confirm_does_nothing():
    """Confirming ends the transaction. A later revert must not undo it."""
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=30)
    g.apply(HIGH)
    g.confirm()
    assert g.revert()["reverted"] is False
    assert d.current() == HIGH


def test_a_second_change_while_one_is_pending_is_refused():
    """Two pending changes would disagree about what 'restore' means."""
    d = FakeDisplay(FHD, allowed={FHD, HIGH, HD})
    g = DisplayGuard(d, timeout=30)
    g.apply(HIGH)
    with pytest.raises(DisplayError, match="already awaiting confirmation"):
        g.apply(HD)


def test_status_reports_a_countdown_while_pending():
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=10)
    g.apply(HIGH)
    st = g.status()
    assert st["pending"] is True
    assert 0 < st["expires_in"] <= 10
    assert st["target"] == HIGH.as_dict()


def test_a_failed_apply_does_not_arm_the_watchdog():
    """If Windows rejects the apply, there is nothing to restore."""
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    d.fail_apply = "the driver said no"
    g = DisplayGuard(d, timeout=0.2)
    with pytest.raises(DisplayError):
        g.apply(HIGH)
    time.sleep(0.4)
    assert d.applied == []
    assert g.status() == {"pending": False}


def test_a_failed_revert_is_kept_visible_rather_than_swallowed():
    """If the restore fails, the record must stay so the journal can show it."""
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=0.2)
    g.apply(HIGH)
    d.fail_apply = "restore rejected"
    time.sleep(0.5)
    assert g.status()["pending"] is True, "a failed revert must not look like success"
    assert g.pending.reverted is False


def test_the_watchdog_revert_is_idempotent():
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=0.2)
    g.apply(HIGH)
    time.sleep(0.5)
    before = list(d.applied)
    time.sleep(0.3)
    assert d.applied == before, "the timer fired twice"


def test_mode_formats_readably():
    assert str(Mode(2560, 1440, 165)) == "2560x1440 @ 165Hz"


def test_guard_works_from_another_thread():
    """The console will call this from a request thread, not the main one."""
    d = FakeDisplay(FHD, allowed={FHD, HIGH})
    g = DisplayGuard(d, timeout=0.2)
    t = threading.Thread(target=lambda: g.apply(HIGH))
    t.start()
    t.join()
    time.sleep(0.5)
    assert d.current() == FHD
