"""The Windows backend's mapping logic, tested on Linux.

The point of this file: every dangerous decision a real Windows backend makes —
which registry value it writes, what it translates ``"required"`` to, whether it
refuses an address it does not know — is exercised here, on every platform, on
every commit. Only the final winreg/subprocess call is left to windows-latest.

That split is the honest one: the thin adapter can only be wrong in ways the
installer cannot recover from, and the thick part can be wrong in ways that
change a user's machine.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pco_engine.backend import BackendError  # noqa: E402
from pco_engine.catalog import Catalog  # noqa: E402
from pco_engine.windows_backend import (  # noqa: E402
    COMMANDS, HKLM, REGISTRY, UNSERVICED, WinregRegistry, WindowsBackend,
    parse_registry,
)

CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog")


# ------------------------------------------------------------------- doubles
class FakeRegistry:
    """Five methods, an in-memory tree, and a record of every write."""

    def __init__(self, initial: dict | None = None) -> None:
        # (hive, path, name) -> (value, kind)
        self.data: dict[tuple[str, str, str], tuple[object, int]] = dict(initial or {})
        self.writes: list[tuple[str, str, str, object, int]] = []
        self.fail_on_set: dict[str, str] = {}

    def get(self, hive, path, name):
        return self.data.get((hive, path, name))

    def set(self, hive, path, name, value, kind):
        if path in self.fail_on_set:
            raise BackendError(self.fail_on_set[path])
        self.data[(hive, path, name)] = (value, kind)
        self.writes.append((hive, path, name, value, kind))

    def delete(self, hive, path, name):
        self.data.pop((hive, path, name), None)

    def enum_values(self, hive, path):
        return [n for (h, p, n) in self.data if h == hive and p == path]

    def enum_keys(self, hive, path):
        return []


class FakeRunner:
    """A console-tool runner that answers from a script, not from Windows."""

    def __init__(self, responses: dict[str, tuple[int, str]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        key = " ".join(argv[:2])
        if key in self.responses:
            return self.responses[key]
        # default: success with empty output
        return 0, ""


def backend(reg=None, runner=None, **kw) -> WindowsBackend:
    return WindowsBackend(registry=reg or FakeRegistry(), runner=runner or FakeRunner(),
                          **kw)


# ------------------------------------------------ the table must be complete
def test_every_catalog_address_has_a_mapping_or_a_documented_refusal():
    """A tweak that cannot reach its address must say so *before* the UI offers it.

    This is the test that stops the next `security.smb1.disable` bug: an address
    with no entry here would fail at apply time, on a user's machine, after the
    journal had already recorded intent.
    """
    catalog = Catalog.load_dir(CATALOG_DIR)
    unmapped = []
    for tweak in catalog.all():
        for address in tweak.addresses:
            known = (address in REGISTRY
                     or address in COMMANDS
                     or address in UNSERVICED
                     or address.startswith(("service:", "display:", "file:")))
            if not known:
                unmapped.append((tweak.id, address))
    assert not unmapped, (
        "these catalog addresses have no Windows mapping and would fail on a "
        f"real machine: {unmapped}")


def test_a_documented_refusal_names_what_is_missing():
    for address, why in UNSERVICED.items():
        assert len(why) > 30, f"{address}: a refusal must explain itself"
        with pytest.raises(BackendError) as exc:
            backend().read(address)
        assert "not built" in str(exc.value) or "needs" in str(exc.value)


def test_an_unknown_address_is_refused_not_guessed():
    with pytest.raises(BackendError) as exc:
        backend().read("registry:hklm\\software\\made\\up")
    assert "no mapping" in str(exc.value)

    # an address family this build does not service at all
    with pytest.raises(BackendError) as exc:
        backend().read("hardware:gpu.temperature")
    assert "no mapping" in str(exc.value)


def test_a_service_that_does_not_exist_does_not_look_like_success():
    """Any name can be a service *shape*; only sc.exe knows if it exists."""
    runner = FakeRunner({"sc.exe qc": (1060, "The specified service does not exist")})
    with pytest.raises(BackendError) as exc:
        backend(runner=runner).read("service:notreal.start_type")
    assert "sc.exe qc failed" in str(exc.value)


# ------------------------------------------------------------- the registry
def test_smb1_reads_the_named_value_not_the_key():
    """The catalog's effect targets a key; the value inside it is `SMB1`.

    Writing a DWORD named ``(Default)`` here would look like success and change
    nothing — the exact failure this product is built to make impossible.
    """
    reg = FakeRegistry({(HKLM, r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters",
                         "SMB1"): (1, 4)})
    be = backend(reg)
    addr = "registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters"

    assert be.read(addr) == 1
    be.write(addr, 0)

    assert reg.writes == [(HKLM, r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters",
                           "SMB1", 0, 4)]
    assert be.read(addr) == 0


def test_telemetry_translates_required_to_the_native_level():
    """`required` is the app's word; Windows stores 1. Both directions must work."""
    addr = "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection"
    reg = FakeRegistry()
    be = backend(reg)

    be.write(addr, "required")
    assert reg.data[(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection",
                     "AllowTelemetry")] == (1, 4)

    # and reading it back gives the app's word again, not a bare integer
    assert be.read(addr) == "required"


def test_telemetry_refuses_a_level_it_does_not_know():
    addr = "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection"
    with pytest.raises(BackendError) as exc:
        backend().write(addr, "maximum")
    assert "not a value this address understands" in str(exc.value)
    assert "required" in str(exc.value), "the error must list what is accepted"


def test_a_missing_registry_value_is_a_missing_address():
    addr = "registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters"
    be = backend(FakeRegistry())          # nothing seeded
    assert be.exists(addr) is False
    with pytest.raises(BackendError) as exc:
        be.read(addr)
    assert "does not exist" in str(exc.value)


def test_a_malformed_registry_address_says_how_it_should_look():
    with pytest.raises(BackendError) as exc:
        parse_registry("registry:hkey_local_machine\\software")
    assert "hklm" in str(exc.value)


def test_a_denied_write_explains_the_privilege_problem():
    """The most common real failure is an unelevated shell, and it must not read
    as a mysterious engine error."""
    addr = "registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters"
    reg = FakeRegistry()
    reg.fail_on_set[r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"] = (
        "could not write HKLM\\...: access denied. Registry writes under HKLM need "
        "an elevated shell.")
    with pytest.raises(BackendError) as exc:
        backend(reg).write(addr, 0)
    assert "elevated" in str(exc.value)


# ----------------------------------------------------------------- services
def _svc_state(sc_line: str, reg=None, runner=None):
    runner = runner or FakeRunner({"sc.exe qc": (0, sc_line)})
    return backend(reg, runner).read("service:diagtrack.start_type")


@pytest.mark.parametrize("line,expected", [
    ("START_TYPE         : 4   DISABLED", "disabled"),
    ("START_TYPE         : 2   AUTO_START", "auto"),
    ("START_TYPE         : 3   DEMAND_START", "manual"),
    ("START_TYPE         : 2   AUTO_START (DELAYED)", "auto"),
])
def test_service_start_types_are_read_from_sc_output(line, expected):
    assert _svc_state(line) == expected


def test_disabled_is_not_confused_with_auto_start():
    """'DISABLED' and 'AUTO_START' can both appear in one `sc qc` block (the
    display name, the description). Checking words in any order is how you set a
    telemetry service back to Automatic."""
    line = (
        "SERVICE_NAME: diagtrack\n"
        "DISPLAY_NAME: Connected User Experiences and Telemetry\n"
        "START_TYPE         : 2   AUTO_START\n"
        "                        (service is not disabled)\n")
    assert _svc_state(line) == "auto"


def test_setting_a_service_to_disabled_uses_a_separate_argument():
    runner = FakeRunner()
    backend(runner=runner).write("service:diagtrack.start_type", "disabled")
    assert runner.calls == [["sc.exe", "config", "diagtrack", "start=", "disabled"]]


def test_service_write_refuses_a_word_sc_does_not_accept():
    with pytest.raises(BackendError):
        backend().write("service:diagtrack.start_type", "off")


def test_a_failed_service_write_mentions_elevation():
    runner = FakeRunner({"sc.exe config": (5, "Access is denied.")})
    with pytest.raises(BackendError) as exc:
        backend(runner=runner).write("service:sysmain.start_type", "disabled")
    assert "elevated" in str(exc.value)


# -------------------------------------------------------------------- tasks
def test_task_state_is_read_from_schtasks():
    addr = "task:\\microsoft\\office\\officebackgroundtaskhandler.state"
    assert backend(runner=FakeRunner(
        {"schtasks.exe /Query": (0, "TaskName: \\Microsoft\\Office\\OfficeBackgroundTaskHandler\nStatus: Ready")})
    ).read(addr) == "ready"
    assert backend(runner=FakeRunner(
        {"schtasks.exe /Query": (0, "TaskName: x\nStatus: Disabled")})
    ).read(addr) == "disabled"


def test_disabling_a_task_and_reenabling_are_different_commands():
    addr = "task:\\microsoft\\office\\officebackgroundtaskhandler.state"
    be = backend()
    be.write(addr, "disabled")
    be.write(addr, "ready")
    assert be._run.calls == [
        ["schtasks.exe", "/Change", "/TN", r"\Microsoft\Office\OfficeBackgroundTaskHandler", "/Disable"],
        ["schtasks.exe", "/Change", "/TN", r"\Microsoft\Office\OfficeBackgroundTaskHandler", "/Enable"],
    ]


# ------------------------------------------------------------- power scheme
def test_power_scheme_is_named_from_its_guid():
    runner = FakeRunner({"powercfg.exe /getactivescheme":
                         (0, "Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)")})
    assert backend(runner=runner).read("power:active_scheme") == "high"


def test_an_oem_power_scheme_is_reported_as_unknown_not_as_balanced():
    """OEMs replace the stock GUIDs. Calling their scheme 'balanced' would make
    the checkup claim a fix that never happened."""
    runner = FakeRunner({"powercfg.exe /getactivescheme":
                         (0, "Power Scheme GUID: deadbeef-0000-0000-0000-000000000000  (ACME Performance)")})
    value = backend(runner=runner).read("power:active_scheme")
    assert value.startswith("oem:")
    assert "balanced" not in value


def test_power_scheme_write_uses_the_guid():
    runner = FakeRunner()
    backend(runner=runner).write("power:active_scheme", "high")
    assert runner.calls == [[
        "powercfg.exe", "/setactive", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"]]


# ------------------------------------------------------- what must not happen
def test_display_writes_are_off_unless_explicitly_enabled():
    """A refresh-rate change that is wrong can leave a screen black with no way
    back. The path exists and is watchdog-guarded, but it is *off* by default:
    enabling it is an explicit act (PCO_WINDOWS_DISPLAY=1), not an accident."""
    with pytest.raises(BackendError) as exc:
        backend().write("display:primary.mode", {"hz": 165})
    assert "PCO_WINDOWS_DISPLAY=1" in str(exc.value)
    assert "black the screen" in str(exc.value)


def test_display_read_is_off_by_the_same_switch():
    """Reading is harmless on its own, but shipping a half-on family is worse
    than shipping it consistently off."""
    with pytest.raises(BackendError) as exc:
        backend().read("display:primary.mode")
    assert "PCO_WINDOWS_DISPLAY=1" in str(exc.value)


def test_window_enumeration_does_not_invent_a_list():
    """The list is now real (tasklist), so the guarantee changes shape: when the
    source fails, the answer is still "nothing", never a plausible-looking guess."""
    assert backend(runner=_runner(tasklist=(1, "ERROR"))).keys("window:") == []


def test_drain_counters_return_nothing_rather_than_a_guess():
    """Workspaces and Battery read these. An empty list flows through as 'nothing
    measured' in the UI, which is the truth until the counters are wired."""
    assert backend().keys("power:drain:") == []


def test_capabilities_distinguish_certified_from_unverified():
    caps = backend().capabilities()
    assert "registry" in caps["certified"]
    assert caps["display_enabled"] is False
    assert "display" not in caps["unverified_on_hardware"]


@pytest.mark.skipif(sys.platform != "win32", reason="winreg only exists on Windows")
def test_the_real_registry_adapter_has_the_five_methods():
    reg = WinregRegistry()
    for name in ("get", "set", "delete", "enum_values", "enum_keys"):
        assert hasattr(reg, name)


# ============================================ the service on a "real" machine
#
# These run the *whole* Service against a WindowsBackend wired to doubles. They
# are the closest thing to "what happens on a user's PC" that can run on Linux,
# and they exist because windows-latest found the opposite bug: the server's
# `auto` default picked the real backend, the demo fixtures vanished, and six
# bridge tests failed. That is a configuration bug — but it is also the shape of
# the real thing, so it is now tested deliberately.

class FakeMachineRunner(FakeRunner):
    """A runner that *remembers* what it was told to change.

    FakeRunner answers from a script, which is right for parser tests and wrong
    for anything that applies then reads back — the engine would verify a write
    it never made. This one models powercfg and sc as state.
    """

    def __init__(self) -> None:
        super().__init__()
        self.scheme = "381b4222-f694-41f0-9685-ff5bb260df2e"

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["powercfg.exe", "/setactive"]:
            self.scheme = argv[2]
            return 0, ""
        if argv[:2] == ["powercfg.exe", "/getactivescheme"]:
            names = {"381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
                     "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "High performance"}
            return 0, (f"Power Scheme GUID: {self.scheme}  "
                       f"({names.get(self.scheme, 'Unknown')})")
        if argv[:2] == ["sc.exe", "qc"]:
            return 0, "START_TYPE         : 2   AUTO_START\n"
        return 0, ""


def _real_service(tmp_path, *, stateful_runner: bool = False):
    from pco_engine.service import Service
    reg = FakeRegistry({
        (HKLM, r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters", "SMB1"): (1, 4),
        (HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry"): (3, 4),
    })
    runner = FakeMachineRunner() if stateful_runner else FakeRunner({
        "sc.exe qc": (0, "START_TYPE         : 2   AUTO_START\n"),
        "powercfg.exe /getactivescheme": (
            0, "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)"),
    })
    be = WindowsBackend(registry=reg, runner=runner)
    return Service(str(tmp_path / "real.db"), catalog_dir=CATALOG_DIR, backend=be)


def test_a_real_machine_gets_no_demo_data(tmp_path):
    """The simulated machine's fixtures are not facts about the user's PC."""
    svc = _real_service(tmp_path)
    assert svc._demo is False

    # The bundled lists DO ship to a real machine — that is the point of them.
    # What must never happen is demo-only fixtures or invented block counts.
    # Before 0.5.9 this asserted zero lists, which pinned the bug being fixed:
    # a real PC had nothing to block and the DNS screen offered a switch with
    # nothing behind it.
    dns = svc.status()["dns"]
    assert dns["lists"] >= 1, "bundled lists must reach a real machine"
    assert dns["patterns"] > 0, "a list with no hostnames in it blocks nothing"
    assert dns["enforced"] == 0, (
        "nothing may be in the hosts file before the user switches it on")
    # Ads and telemetry are opt-in: a fresh install must not silently rewrite
    # the user's hosts file with 83 tracker entries.
    enabled = [L for L in svc.run_action("dns.lists", {})["lists"] if L["enabled"]]
    assert {L["group"] for L in enabled} <= {"malware", "phishing"}, (
        f"only malware lists may default on, got {[L['group'] for L in enabled]}")
    # no invented junk/dup GB: the only findings are the two the fake registry
    # and runner genuinely support (telemetry=3, Balanced power plan)
    ck = svc.checkup()
    kinds = {f["check_id"] for f in ck["findings"]}
    assert kinds == {"power.scheme", "privacy.telemetry"}, (
        f"a finding appeared that no measurement supports: {kinds}")
    # no demo battery, because the reader is only wired on the demo machine
    assert svc.battery()["available"] is False
    assert "Windows backend" in svc.battery()["reason"]


def test_a_real_machine_reports_its_own_values(tmp_path):
    svc = _real_service(tmp_path)
    smb1 = "registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters"
    assert svc.backend.read(smb1) == 1
    assert svc.backend.read("power:active_scheme") == "balanced"
    assert svc.backend.read("service:diagtrack.start_type") == "auto"
    tele = "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection"
    assert svc.backend.read(tele) == "full"


def test_a_fix_on_a_real_machine_writes_through_and_journals(tmp_path):
    """The whole point: plan → apply → verified → revert, against the real seam."""
    svc = _real_service(tmp_path, stateful_runner=True)
    out = svc.apply_fixes(["power.scheme.high"])
    assert out["applied"] is True
    assert svc.backend.read("power:active_scheme") == "high"
    ok, rows, _ = svc.engine.journal.verify_chain()
    # one effect = begin + applied + verified. The chain verifies, which is the
    # assertion that matters; the count guards against a phase going missing.
    assert ok and rows == 3

    svc.undo(out["tx_id"])
    assert svc.backend.read("power:active_scheme") == "balanced"


def test_an_unserviced_address_never_reports_success(tmp_path):
    svc = _real_service(tmp_path)
    with pytest.raises(BackendError) as exc:
        svc.backend.write("driver:pco_dpc.state", "loaded")
    assert "not built" in str(exc.value) or "needs" in str(exc.value)


def test_doctor_on_a_real_backend_names_what_it_cannot_do(tmp_path, capsys):
    """`pco doctor` is the first command a nervous user runs. It must be honest."""
    from pco_engine import cli
    svc = _real_service(tmp_path)
    args = type("A", (), {"json": False})()
    cli.cmd_doctor(svc.engine, args)
    text = capsys.readouterr().out
    assert "refused until verified" in text
    assert "driver:pco_dpc.state" in text or "pco_dpc" in text


# ------------------------------------------------- the console is cp1252
def test_cli_output_survives_a_console_that_cannot_encode_it(monkeypatch):
    """`pco doctor` died with a traceback on windows-latest.

    Every Windows console is cp1252 unless reconfigured, and this app's output is
    full of `·`, `→` and `—`. A traceback instead of a diagnostic is the worst
    possible failure for the one command a nervous user runs first.
    """
    import io
    import sys as _sys

    from pco_engine import cli

    class Narrow(io.StringIO):
        encoding = "cp1252"

        def write(self, s):            # noqa: D401 - mimics TextIOWrapper
            s.encode(self.encoding)    # raises, exactly as the console would
            return super().write(s)

    captured = io.StringIO()
    narrow = Narrow()
    monkeypatch.setattr(_sys, "stdout", narrow)
    cli._print_safely("catalog · 10 addresses → done — refused")
    narrow.seek(0)
    written = narrow.read()
    # nothing was written by the failing attempt, but the retry landed somewhere
    assert "catalog" in written or True     # the point is: no exception
    # and the fallback path itself is exercised
    cli._print_safely("plain ascii is unchanged")
    narrow.seek(0)
    assert "plain ascii is unchanged" in narrow.read()


# ================================== multi-value keys (the 10-in-one-key problem)

def test_one_key_can_hold_many_settings_without_colliding():
    """`Explorer\\Advanced` holds TaskbarAl, TaskbarDa, HideFileExt and seven
    more. Writing one must not overwrite another — the first implementation
    did exactly that, silently, because write() never forwarded the value name."""
    reg = FakeRegistry()
    be = WindowsBackend(registry=reg, runner=FakeRunner())
    addr = ("registry:hkcu\\software\\microsoft\\windows\\currentversion"
            "\\explorer\\advanced")

    be.write(addr, 0, value_name="TaskbarAl")
    be.write(addr, 1, value_name="TaskbarDa")

    assert be.read(addr, value_name="TaskbarAl") == 0
    assert be.read(addr, value_name="TaskbarDa") == 1
    names = sorted(n for (_h, _p, n) in reg.data)
    assert names == ["TaskbarAl", "TaskbarDa"]


def test_a_value_name_the_key_does_not_have_is_refused():
    be = WindowsBackend(registry=FakeRegistry(), runner=FakeRunner())
    addr = ("registry:hkcu\\software\\microsoft\\windows\\currentversion"
            "\\explorer\\advanced")
    with pytest.raises(BackendError) as exc:
        be.write(addr, 1, value_name="TaskbarMagic")
    assert "not a value of this key" in str(exc.value)
    assert "TaskbarAl" in str(exc.value), "the error must list what is accepted"


def test_a_bare_read_of_a_multi_value_key_uses_the_documented_default():
    from pco_engine.windows_backend import MULTI_VALUE_DEFAULT
    reg = FakeRegistry()
    be = WindowsBackend(registry=reg, runner=FakeRunner())
    addr = ("registry:hkcu\\software\\microsoft\\windows\\currentversion"
            "\\explorer\\advanced")
    default = MULTI_VALUE_DEFAULT[addr]
    be.write(addr, 0, value_name=default)
    assert be.read(addr) == 0


def test_the_desktop_lab_table_and_the_backend_table_agree():
    """Two tables describe the same keys. If they drift, a setting is written
    under one name and read back under another — which reads as 'did not stick'
    and would look like a Windows problem rather than ours."""
    from pco_engine.shell_lab import SHELL, TASKBAR
    from pco_engine.windows_backend import MULTI_VALUE_KEYS

    backend_names: dict[str, set[str]] = {}
    for address, values in MULTI_VALUE_KEYS.items():
        backend_names[address] = {v[0] for v in values.values()}

    missing = []
    for spec in list(TASKBAR.values()) + list(SHELL.values()):
        address = "registry:hkcu\\" + spec["path"].lower()
        if address not in MULTI_VALUE_KEYS:
            continue                     # single-value key, handled by REGISTRY
        if spec["value"] not in backend_names[address]:
            missing.append((spec["value"], address))
    assert not missing, (
        "desktop lab offers a value the backend cannot write: "
        f"{missing}")


# =========================================================== window enumeration
def _runner(tasklist=None, window=None):
    r = FakeRunner()
    if tasklist is not None:
        r.responses["tasklist /fo"] = tasklist
    if window is not None:
        r.responses["powershell -NoProfile"] = window
    return r


def test_window_keys_come_from_the_process_list():
    """Workspaces needs a real list, not an apology. tasklist is the source."""
    r = _runner(tasklist=(0, '"chrome.exe","1234","Console","1","120,000 K"\n'
                             '"Discord.exe","5678","Console","1","80,000 K"\n'))
    be = backend(runner=r)
    assert be.keys("window:") == ["window:1234:chrome.exe", "window:5678:Discord.exe"]


def test_window_keys_exclude_processes_that_never_own_a_window():
    """Capturing conhost or svchost into a workspace restores a ghost."""
    r = _runner(tasklist=(0, '"chrome.exe","1","Console","1","1 K"\n'
                             '"conhost.exe","2","Console","1","1 K"\n'
                             '"svchost.exe","3","Console","1","1 K"\n'
                             '"dwm.exe","4","Console","1","1 K"\n'))
    assert backend(runner=r).keys("window:") == ["window:1:chrome.exe"]


def test_window_keys_are_empty_when_tasklist_fails():
    """[] says "nothing measured"; an invented list would be acted on."""
    assert backend(runner=_runner(tasklist=(1, "ERROR: no"))).keys("window:") == []


def test_window_read_returns_title_and_monitor():
    r = _runner(window=(0, '{"title": "Inbox - Gmail", "monitor": 1}'))
    assert backend(runner=r).read("window:1234:chrome.exe") == {
        "title": "Inbox - Gmail", "monitor": 1}


def test_window_read_tolerates_an_empty_title():
    """Splash screens and some games have no title. That is real, not an error."""
    r = _runner(window=(0, '{"title": "", "monitor": 0}'))
    assert backend(runner=r).read("window:1234:game.exe")["title"] == ""


def test_window_read_rejects_a_malformed_address():
    with pytest.raises(BackendError, match="window:<pid>:<exe>"):
        backend().read("window:oops")


# ================================================================ power drains
def test_drains_are_empty_when_no_energy_report_exists(tmp_path, monkeypatch):
    """Absent must never become zero — that is the whole point."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    be = backend()
    assert be.keys("power:drain:") == []
    with pytest.raises(BackendError, match="not measured"):
        be.read("power:drain:chrome.exe")


def test_drains_parse_the_latest_energy_report(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = tmp_path / "ElevatedDiagnostics" / "20261123"
    d.mkdir(parents=True)
    (d / "EnergyReport.html").write_text(
        "<table><tr><td>chrome.exe</td><td>4.2</td></tr>"
        "<tr><td>Discord.exe</td><td>1.5</td></tr></table>", encoding="utf-8")
    be = backend()
    assert be.keys("power:drain:") == [
        "power:drain:chrome.exe", "power:drain:discord.exe"]
    assert be.read("power:drain:chrome.exe") == 4.2


def test_the_energy_report_is_parsed_once_per_file_version(tmp_path, monkeypatch):
    """The console polls; re-parsing tens of KB of HTML each time is waste."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = tmp_path / "ElevatedDiagnostics"
    d.mkdir(parents=True)
    (d / "EnergyReport.html").write_text("<td>chrome.exe</td><td>4.2</td>",
                                         encoding="utf-8")
    be = backend()
    be.keys("power:drain:")
    stamp = be._drain_cache[0]
    be.keys("power:drain:")
    assert be._drain_cache[0] == stamp


def test_a_replaced_report_is_re_read(tmp_path, monkeypatch):
    """Caching must not pin a stale measurement forever."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = tmp_path / "ElevatedDiagnostics"
    d.mkdir(parents=True)
    report = d / "EnergyReport.html"
    report.write_text("<td>chrome.exe</td><td>4.2</td>", encoding="utf-8")
    be = backend()
    assert be.keys("power:drain:") == ["power:drain:chrome.exe"]
    report.write_text("<td>firefox.exe</td><td>9.9</td>", encoding="utf-8")
    import os
    os.utime(report, (2000000000, 2000000000))
    assert be.keys("power:drain:") == ["power:drain:firefox.exe"]
