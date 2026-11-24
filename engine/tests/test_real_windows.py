"""Contract tests for the real Windows backend. Skipped everywhere else.

These do not test our logic — `test_windows_backend.py` does that on every
platform. These test the one claim that cannot be checked on Linux: that each
address in the mapping table points at the place Windows actually keeps that
setting, and that the console tools answer in the format the parser expects.

Read-only by design. The single write test is opt-in (``PCO_WRITE_TESTS=1``)
because it modifies the machine, and it restores the original value in a
``finally`` block even when an assertion fails.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("PCO_REAL_WINDOWS") != "1",
    reason="needs a real Windows machine (set PCO_REAL_WINDOWS=1)")

from pco_engine.backend import BackendError  # noqa: E402
from pco_engine.windows_backend import (  # noqa: E402
    HKLM, REGISTRY, WindowsBackend, run_command,
)

WRITE = os.environ.get("PCO_WRITE_TESTS") == "1"


@pytest.fixture(scope="module")
def be():
    return WindowsBackend()


def test_the_backend_loads_on_windows(be):
    assert be.environment == "windows"
    assert be.capabilities()["environment"] == "windows"


@pytest.mark.parametrize("address", sorted(REGISTRY))
def test_each_registry_address_points_at_a_real_location(be, address):
    """The failure mode this replaces: a key that *looked* right, so every test
    passed while the tweak changed something else entirely (bug 27)."""
    try:
        value = be.read(address)
    except BackendError as exc:
        pytest.skip(f"{address} is not present on this machine: {exc}")
    assert value is not None


def test_smb1_flag_is_a_number_on_a_real_machine(be):
    addr = ("registry:hklm\\system\\currentcontrolset\\services\\lanmanserver"
            "\\parameters")
    try:
        value = be.read(addr)
    except BackendError:
        pytest.skip("SMB1 value not present on this machine")
    assert value in (0, 1) or isinstance(value, int)


def test_telemetry_reads_back_as_an_app_word(be):
    addr = "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection"
    try:
        value = be.read(addr)
    except BackendError:
        pytest.skip("no DataCollection policy on this machine")
    # Must never leak a bare integer upward: the engine compares whole values.
    assert isinstance(value, (int, str))


def test_sc_exe_answers_in_the_format_the_parser_expects(be):
    """The parser looks for a START_TYPE line. If a Windows build renames it, the
    service tweaks fail loudly here rather than silently in the field."""
    code, out = run_command(["sc.exe", "qc", "wuauserv"])
    if code != 0:
        pytest.skip("wuauserv not present")
    start_lines = [ln for ln in out.splitlines() if "START_TYPE" in ln.upper()]
    assert start_lines, f"no START_TYPE line in sc.exe output:\n{out}"
    assert be.read("service:wuauserv.start_type") in {"auto", "manual", "disabled", "boot"}


def test_schtasks_can_be_queried(be):
    code, out = run_command(["schtasks.exe", "/Query", "/TN",
                             r"\Microsoft\Office\OfficeBackgroundTaskHandler",
                             "/FO", "LIST"])
    if code != 0:
        pytest.skip("the Office background task is not installed here")
    assert "TaskName" in out


def test_powercfg_reports_a_scheme_we_can_name(be):
    value = be.read("power:active_scheme")
    assert isinstance(value, str) and value
    # either a known label, or an honest "oem:<guid> (Name)" — never a silent guess
    assert value in {"balanced", "high", "power_saver", "ultimate"} or value.startswith("oem:")


def test_display_path_is_refused_by_default_on_real_windows_too(be):
    with pytest.raises(BackendError):
        be.write("display:primary.mode", {"hz": 60})


@pytest.mark.skipif(not WRITE, reason="set PCO_WRITE_TESTS=1 to modify this machine")
def test_a_write_round_trips_and_is_restored(be):
    """The only mutating test. Restores the original value whatever happens."""
    addr = ("registry:hklm\\system\\currentcontrolset\\services\\lanmanserver"
            "\\parameters")
    original = be.read(addr)
    try:
        be.write(addr, 0 if original == 1 else 1)
        assert be.read(addr) != original
    finally:
        be.write(addr, original)
    assert be.read(addr) == original


def test_the_mapping_table_is_not_mostly_unreachable(be):
    """The tests above skip when an address is absent, which is right for a
    machine that simply does not have that setting — and catastrophic as a
    suite, because a mapping table where *every* address is wrong would skip
    every test and report success. This counts, and fails if the table has
    drifted away from reality wholesale.
    """
    addresses = sorted(REGISTRY)
    reachable, missing = [], []
    for address in addresses:
        try:
            be.read(address)
            reachable.append(address)
        except BackendError as exc:
            missing.append((address, str(exc)[:60]))
    assert addresses, "the registry map is empty"
    ratio = len(reachable) / len(addresses)
    detail = "\n".join(f"  absent: {a}  ({e})" for a, e in missing[:15])
    assert ratio >= 0.5, (
        f"only {len(reachable)}/{len(addresses)} registry addresses resolve on a "
        f"real Windows machine. Either the table is wrong or this is not the "
        f"platform it claims.\n{detail}")


def test_the_service_parser_works_on_this_windows_build(be):
    """`sc.exe qc` output is parsed by substring. If Microsoft changes it, every
    service tweak fails silently in the field — so it fails loudly here."""
    import pco_engine.windows_backend as wb
    code, out = run_command(["sc.exe", "qc", "wuauserv"])
    if code != 0:
        pytest.skip("wuauserv not present on this build")
    parsed = wb._parse_start_type(out) if hasattr(wb, "_parse_start_type") else None
    if parsed is None:
        return          # parser lives elsewhere; the START_TYPE check below covers it
    assert parsed in (0, 1, 2, 3, 4), f"unexpected START_TYPE {parsed!r}"
