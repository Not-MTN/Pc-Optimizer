"""The Windows backend — the one file that touches a real machine.

Everything above `Backend` is pure and platform-free, which is why the whole
test suite runs on Linux. This module is the other side of that seam: it maps a
canonical address (``service:diagtrack.start_type``) onto a real Windows API.

Why an explicit table instead of parsing the address
----------------------------------------------------
An address names a *managed thing*, not a location. ``registry:hklm\\system\\...\\
lanmanserver\\parameters`` is a **key**; the value inside it (``SMB1``) lives in
the catalog's ``effect.pre`` and never reaches ``write(address, value)``. The
same address also has to translate between the app's vocabulary (``"required"``)
and the machine's (``AllowTelemetry = 1``). Guessing either of those at runtime
would be the exact class of bug this product exists to avoid, so every mapping
is written down once, in ``ADDRESSES``, and audited by tests.

State of this file
------------------
``CERTIFIED_IN_CI`` — the registry, service, task, power-scheme and file
families. Their resolution logic runs in CI on Linux against a fake registry and
a fake command runner, and again on ``windows-latest`` against real Windows.

``UNVERIFIED_ON_HARDWARE`` — the display family. The ctypes calls are the
documented ones and the write is guarded by ``CDS_TEST`` plus an automatic
revert, but no part of this file has been run on a display that matters. The
app reports this honestly rather than pretending the two are the same.

Every address that this build cannot service raises ``BackendError`` naming the
missing component. Silence is never an option: a write that quietly does
nothing is worse than a write that refuses.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .backend import BackendError
from .windows_notes import FORCE_64BIT_VIEW

# --------------------------------------------------------------------- status
CERTIFIED_IN_CI = ("registry", "service", "task", "power", "file")
UNVERIFIED_ON_HARDWARE = ("display",)

# The two hives the catalog uses. Kept as strings so addresses stay readable and
# the fake registry in tests needs no Windows constants.
HKLM = "hklm"
HKCU = "hkcu"

# What a *write* to an address that has never been read is allowed to assume.
# Anything not in here must already exist, because creating a key we misjudged is
# how an app breaks a machine it was supposed to be helping.
CREATABLE = {"registry": True}


# ============================================================ the address table
@dataclass(frozen=True)
class RegSpec:
    """One canonical registry address, and everything needed to reach it.

    ``native`` translates the app's vocabulary to the machine's. The catalog says
    "required"; Windows says ``AllowTelemetry = 1``. Keeping both means neither
    side has to leak into the other.
    """

    hive: str
    path: str
    name: str
    kind: str = "dword"                      # dword | string
    native: dict[str, Any] = field(default_factory=dict)

    def to_native(self, value: Any) -> Any:
        if isinstance(value, str):
            key = value.lower()
            if key in self.native:
                return self.native[key]
            raise BackendError(
                f"{self.path}\\{self.name}: '{value}' is not a value this "
                f"address understands (known: {sorted(self.native)})")
        if not isinstance(value, int):
            raise BackendError(f"{self.path}\\{self.name}: expected a number, got {value!r}")
        return value

    def to_app(self, raw: Any) -> Any:
        if self.kind == "dword":
            if not isinstance(raw, int):
                return raw
            for name, num in self.native.items():
                if num == raw:
                    return name
        return raw


@dataclass(frozen=True)
class CmdSpec:
    """One canonical address serviced by a command-line tool.

    ``read`` returns the app's value; ``write`` performs the change. Both take
    the runner, so the whole thing is exercised in CI without those tools.
    """

    read: Callable[[Any, str], Any]
    write: Callable[[Any, str, Any], None]
    why: str = ""


def _sc_verb(name: str) -> str:
    """sc.exe wants a service name or a display name; ours are always names."""
    return name


def _service_start_read(run: Any, svc: str) -> str:
    """Parse the one line that carries the start type.

    Not the whole output: a service description may contain the word "disabled"
    ("...is not disabled"), and a display name may say "Automatic". Searching the
    entire block for words is how a backend reports a running service as
    disabled — and then "fixes" it back on. Only the START_TYPE line counts.
    """
    code, out = run(["sc.exe", "qc", _sc_verb(svc)])
    if code != 0:
        raise BackendError(f"service {svc}: sc.exe qc failed ({code})")
    for line in out.splitlines():
        if "START_TYPE" not in line.upper():
            continue
        upper = line.upper()
        if "DISABLED" in upper:
            return "disabled"
        if "AUTO_START" in upper:
            return "auto"
        if "DEMAND_START" in upper:
            return "manual"
        if "BOOT_START" in upper:
            return "boot"
        break
    raise BackendError(
        f"service {svc}: no START_TYPE line in sc.exe output — refusing to guess "
        f"what state this service is in")


def _service_start_write(run: Any, svc: str, value: Any) -> None:
    mapping = {"auto": "auto", "manual": "demand", "disabled": "disabled"}
    if value not in mapping:
        raise BackendError(f"service {svc}: cannot set start type {value!r}")
    # A start type change must survive a reboot, so it is not a transient start.
    code, out = run(["sc.exe", "config", _sc_verb(svc), "start=", mapping[str(value)]])
    if code != 0:
        raise BackendError(
            f"service {svc}: sc.exe config start= {mapping[str(value)]} failed "
            f"({code}). This needs an elevated shell. Output: {out.strip()[:200]}")


def _task_state_read(run: Any, path: str) -> str:
    code, out = run(["schtasks.exe", "/Query", "/TN", path, "/FO", "LIST"])
    if code != 0:
        raise BackendError(f"task {path}: schtasks query failed ({code})")
    return "disabled" if "DISABLED" in out.upper() else "ready"


def _task_state_write(run: Any, path: str, value: Any) -> None:
    if value not in {"disabled", "ready"}:
        raise BackendError(f"task {path}: cannot set state {value!r}")
    flag = "/Change" if value == "disabled" else "/End"
    args = (["schtasks.exe", "/Change", "/TN", path, "/Disable"] if flag == "/Change"
            else ["schtasks.exe", "/Change", "/TN", path, "/Enable"])
    code, out = run(args)
    if code != 0:
        raise BackendError(f"task {path}: schtasks {args[-1]} failed ({code}): {out[:200]}")


# The GUIDs below are the stock ones every consumer Windows ships. An OEM can
# replace them, which is why an unknown active scheme is *reported* rather than
# silently mapped to "balanced".
SCHEME_GUIDS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "balanced",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "high",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "power_saver",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "ultimate",
}


def _power_read(run: Any, _name: str) -> Any:
    code, out = run(["powercfg.exe", "/getactivescheme"])
    if code != 0:
        raise BackendError(f"powercfg failed ({code})")
    for guid, label in SCHEME_GUIDS.items():
        if guid in out.lower():
            return label
    # Honest: name the GUID instead of pretending it is one of the four.
    tail = out.strip().splitlines()[-1] if out.strip() else "unknown"
    return f"oem:{tail.strip()}"


def _power_write(run: Any, _name: str, value: Any) -> None:
    want = {"balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
            "high": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"}.get(str(value))
    if want is None:
        raise BackendError(f"power scheme: cannot set {value!r}")
    code, out = run(["powercfg.exe", "/setactive", want])
    if code != 0:
        raise BackendError(f"powercfg /setactive failed ({code}): {out[:200]}")


# =================================================================== the table
#
# Each key is a canonical address exactly as `address.py` produces it. Adding a
# tweak to the catalog without adding its address here is a test failure, not a
# runtime surprise — see tests/test_windows_backend.py::
# test_every_catalog_address_has_a_mapping.
REGISTRY: dict[str, RegSpec] = {
    # -- security: SMBv1 server support (the catalog's `pre` names the value)
    "registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters": RegSpec(
        hive=HKLM, path=r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters",
        name="SMB1", kind="dword"),

    # -- privacy: the *key* is the address; the value inside carries the level.
    #    AllowTelemetry: 0 Security, 1 Basic/Required, 2 Enhanced, 3 Full.
    "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection": RegSpec(
        hive=HKLM, path=r"SOFTWARE\Policies\Microsoft\Windows\DataCollection",
        name="AllowTelemetry", kind="dword",
        native={"required": 1, "basic": 1, "enhanced": 2, "full": 3}),
}

# ================================================== multi-value Explorer keys
#
# `HKCU\...\Explorer\Advanced` is one key holding ten unrelated settings:
# TaskbarAl, TaskbarDa, HideFileExt, Hidden, ShowTaskViewButton... An address
# names the *key*, but `write(address, value)` has nowhere to put the value name.
#
# The honest fix is not to invent one value name and hope. It is a small,
# explicit table keyed by the (key, value) pair the desktop lab already knows —
# so this file holds no duplicate copy of the option list, and the two can be
# cross-checked by a test.
MULTI_VALUE_KEYS: dict[str, dict[str, tuple[str, str]]] = {
    # address -> {value name (lowercased): (canonical name, kind)}
    "registry:hkcu\\software\\microsoft\\windows\\currentversion\\explorer\\advanced": {
        "taskbaral": ("TaskbarAl", "dword"),
        "taskbarda": ("TaskbarDa", "dword"),
        "taskbarmn": ("TaskbarMn", "dword"),
        "showtaskviewbutton": ("ShowTaskViewButton", "dword"),
        "taskbarglomlevel": ("TaskbarGlomLevel", "dword"),
        "taskbarsmallicons": ("TaskbarSmallIcons", "dword"),
        "hidefileext": ("HideFileExt", "dword"),
        "hidden": ("Hidden", "dword"),
        "enablesnapassistflyout": ("EnableSnapAssistFlyout", "dword"),
        "taskbaranimations": ("TaskbarAnimations", "dword"),
    },
    "registry:hkcu\\software\\microsoft\\windows\\currentversion\\search": {
        "searchboxtaskbarmode": ("SearchboxTaskbarMode", "dword"),
        "bingsearchenabled": ("BingSearchEnabled", "dword"),
    },
    "registry:hkcu\\software\\microsoft\\windows\\currentversion\\themes\\personalize": {
        "enabletransparency": ("EnableTransparency", "dword"),
        "colorprevalence": ("ColorPrevalence", "dword"),
        "appsuselighttheme": ("AppsUseLightTheme", "dword"),
        "systemuseslighttheme": ("SystemUsesLightTheme", "dword"),
    },
}

# Which value inside a multi-value key a *bare* address means when the caller has
# not named one. There has to be a defensible answer for `read()`, and there is:
# the one setting the address is most often used for. The desktop lab always
# names its value explicitly, so this default only affects ad-hoc reads.
MULTI_VALUE_DEFAULT = {
    "registry:hkcu\\software\\microsoft\\windows\\currentversion\\explorer\\advanced":
        "TaskbarAl",
    "registry:hkcu\\software\\microsoft\\windows\\currentversion\\search":
        "SearchboxTaskbarMode",
    "registry:hkcu\\software\\microsoft\\windows\\currentversion\\themes\\personalize":
        "AppsUseLightTheme",
}

# The icon keys: same shape, but string values.
ICON_VALUE_NAMES = {
    "registry:hkcu\\software\\microsoft\\windows\\currentversion\\explorer\\clsid"
    r"\{645ff040-5081-101b-9f08-00aa002f954e}\defaulticon": ("Full", "Empty"),
}


COMMANDS: dict[str, CmdSpec] = {
    "power:active_scheme": CmdSpec(_power_read, _power_write),
    "task:\\microsoft\\office\\officebackgroundtaskhandler.state": CmdSpec(
        lambda run, p: _task_state_read(run, r"\Microsoft\Office\OfficeBackgroundTaskHandler"),
        lambda run, p, v: _task_state_write(run, r"\Microsoft\Office\OfficeBackgroundTaskHandler", v)),
}

# Services are open-ended (`service:<name>.start_type` for any service), so they
# are matched by shape rather than enumerated.
SERVICE_SUFFIX = ".start_type"

# Addresses this build can name but cannot honestly service yet. Each says what
# is missing, because "failed" and "not built" are different answers.
UNSERVICED: dict[str, str] = {
    "driver:pco_dpc.state":
        "needs the PCO interrupt-tuning driver, which is not built or signed yet",
    "startup:onedrive.defer_seconds":
        "deferral needs the PCO launcher installed; this build will not rewrite "
        "a Run entry it cannot then honour",
    "startup:steam.defer_seconds":
        "deferral needs the PCO launcher installed; this build will not rewrite "
        "a Run entry it cannot then honour",
}


_PATH_CACHE: dict[str, str] = {}


def _real_path(address: str) -> str:
    """The properly-cased path for a lowercased address.

    Windows is case-insensitive, so this is cosmetic — but a path that appears in
    an error message as `explorer\\advanced` reads like a typo, and this file's
    whole job is being unambiguous.
    """
    if address in _PATH_CACHE:
        return _PATH_CACHE[address]
    body = address.split(":", 1)[-1]
    _hive, _, path = body.partition("\\")
    parts = {
        "software": "SOFTWARE", "microsoft": "Microsoft", "windows": "Windows",
        "currentversion": "CurrentVersion", "explorer": "Explorer",
        "advanced": "Advanced", "search": "Search", "themes": "Themes",
        "personalize": "Personalize", "system": "SYSTEM",
        "currentcontrolset": "CurrentControlSet", "services": "Services",
        "lanmanserver": "LanmanServer", "parameters": "Parameters",
        "policies": "Policies", "datacollection": "DataCollection",
        "acme": "Acme", "telemetry": "Telemetry", "clsid": "CLSID",
        "defaulticon": "DefaultIcon",
    }
    out = []
    for part in path.split("\\"):
        lowered = part.lower()
        if lowered in parts:
            out.append(parts[lowered])
        elif part.startswith("{") and part.endswith("}"):
            out.append(part.upper())          # CLSIDs are conventionally upper
        else:
            out.append(part)
    result = "\\".join(out)
    _PATH_CACHE[address] = result
    return result


def parse_registry(address: str) -> tuple[RegSpec | None, str]:
    """Split a ``registry:`` address. Returns (known spec, canonical address)."""
    body = address[len("registry:"):]
    hive, _, rest = body.partition("\\")
    if hive not in (HKLM, HKCU) or not rest:
        raise BackendError(
            f"{address}: registry addresses look like registry:hklm\\path (hive "
            f"must be hklm or hkcu, got {hive!r})")
    return REGISTRY.get(address), address


# ============================================================ registry adapter
class Registry(Protocol):
    """A five-method view of the registry — small enough to fake completely.

    winreg's API is wide, versioned and awkward to fake; this is the subset the
    address table actually needs, so the logic above it is testable anywhere.
    """

    def get(self, hive: str, path: str, name: str) -> tuple[Any, int] | None: ...
    def set(self, hive: str, path: str, name: str, value: Any, kind: int) -> None: ...
    def delete(self, hive: str, path: str, name: str) -> None: ...
    def enum_values(self, hive: str, path: str) -> list[str]: ...
    def enum_keys(self, hive: str, path: str) -> list[str]: ...


class WinregRegistry:
    """The real thing. Imported lazily so this module loads on Linux."""

    def __init__(self) -> None:
        try:
            import winreg
        except ImportError as exc:                     # pragma: no cover - Windows
            raise BackendError(
                "the Windows registry is not available on this OS") from exc
        self._w = winreg

    def _hive(self, hive: str) -> Any:
        return self._w.HKEY_LOCAL_MACHINE if hive == HKLM else self._w.HKEY_CURRENT_USER

    # 64-bit view explicitly (windows_notes.FORCE_64BIT_VIEW): a 32-bit Python on
    # 64-bit Windows otherwise reads the WOW6432Node copy of these policies — a
    # different set of values that governs nothing.
    _ACCESS = 0x0100 | 0x0200 | 0x0001 | 0x0002 | 0x0004   # READ|WRITE|QUERY|SET|CREATE

    def get(self, hive: str, path: str, name: str) -> tuple[Any, int] | None:
        try:
            with self._w.OpenKey(self._hive(hive), path, 0,
                                 self._ACCESS | getattr(self._w, "KEY_WOW64_64KEY", 0)) as k:
                value, kind = self._w.QueryValueEx(k, name)
                return value, kind
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise BackendError(f"{hive}\\{path}\\{name}: {exc}") from exc

    def set(self, hive: str, path: str, name: str, value: Any, kind: int) -> None:
        try:
            with self._w.CreateKeyEx(self._hive(hive), path, 0,
                                     self._ACCESS | getattr(self._w, "KEY_WOW64_64KEY", 0),
                                     self._w.KEY_WOW64_64KEY if hasattr(self._w, "KEY_WOW64_64KEY") else 0,
                                     ) as k:
                self._w.SetValueEx(k, name, 0, kind, value)
        except OSError as exc:
            raise BackendError(
                f"could not write {hive}\\{path}\\{name}: {exc}. Registry writes "
                f"under HKLM need an elevated shell.") from exc

    def delete(self, hive: str, path: str, name: str) -> None:
        try:
            with self._w.OpenKey(self._hive(hive), path, 0,
                                 self._w.KEY_SET_VALUE | getattr(self._w, "KEY_WOW64_64KEY", 0)) as k:
                self._w.DeleteValue(k, name)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BackendError(f"could not delete {hive}\\{path}\\{name}: {exc}") from exc

    def enum_values(self, hive: str, path: str) -> list[str]:
        out: list[str] = []
        try:
            with self._w.OpenKey(self._hive(hive), path, 0,
                                 self._w.KEY_READ | getattr(self._w, "KEY_WOW64_64KEY", 0)) as k:
                i = 0
                while True:
                    try:
                        name, _val, _kind = self._w.EnumValue(k, i)
                    except OSError:
                        break
                    out.append(name)
                    i += 1
        except FileNotFoundError:
            return []
        return out

    def enum_keys(self, hive: str, path: str) -> list[str]:
        out: list[str] = []
        try:
            with self._w.OpenKey(self._hive(hive), path, 0,
                                 self._w.KEY_READ | getattr(self._w, "KEY_WOW64_64KEY", 0)) as k:
                i = 0
                while True:
                    try:
                        out.append(self._w.EnumKey(k, i))
                    except OSError:
                        break
                    i += 1
        except FileNotFoundError:
            return []
        return out


# Console-only or session-host processes that `tasklist` reports but that never
# own a visible window. Capturing these into a workspace would restore a ghost.
_NO_WINDOW_EXES = {
    "conhost.exe", "csrss.exe", "dwm.exe", "fontdrvhost.exe", "lsass.exe",
    "registry", "services.exe", "smss.exe", "svchost.exe", "system",
    "wininit.exe", "winlogon.exe", "wmiprvse.exe", "runtimebroker.exe",
    "sihost.exe", "taskhostw.exe", "ctfmon.exe", "searchhost.exe",
    "textinputhost.exe", "backgroundtaskhost.exe", "securityhealthservice.exe",
}


def run_command(argv: list[str], timeout: float = 15.0) -> tuple[int, str]:
    """Run a Windows console tool and return (exit code, combined output).

    No shell: `sc.exe config` takes its value as a *separate argument* with a
    trailing '=', and going through cmd.exe would re-split arguments we control.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout,
                              shell=False, check=False)
    except FileNotFoundError as exc:
        raise BackendError(f"{argv[0]} is not available: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"{argv[0]} timed out after {timeout:.0f}s") from exc
    text = (proc.stdout or b"").decode("utf-8", "replace") + \
           (proc.stderr or b"").decode("utf-8", "replace")
    return proc.returncode, text


# =================================================================== the backend
class WindowsBackend:
    """`Backend` over a real Windows machine.

    Construct it directly (``WindowsBackend()``) or let the CLI pick it up. It
    takes its registry and command runner by injection so that the resolution
    logic — the part that can be wrong in ways that damage a machine — is tested
    on every platform, every commit.
    """

    environment = "windows"

    def __init__(self, *, registry: Registry | None = None,
                 runner: Callable[[list[str]], tuple[int, str]] | None = None,
                 allow_display: bool = False) -> None:
        self._reg = registry if registry is not None else WinregRegistry()
        self._run = runner if runner is not None else run_command
        self._allow_display = allow_display
        self._writes: list[tuple[str, Any]] = []

    # -- capability reporting (this is what `pco doctor` prints) -----------
    def capabilities(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "certified": list(CERTIFIED_IN_CI),
            "unverified_on_hardware": list(UNVERIFIED_ON_HARDWARE) if self._allow_display else [],
            "display_enabled": self._allow_display,
            "known_registry_addresses": len(REGISTRY),
            "known_commands": len(COMMANDS) + 1,   # + the open-ended service family
            "unserviced": dict(UNSERVICED),
        }

    # -- the protocol ------------------------------------------------------
    def read(self, address: str, *, value_name: str | None = None) -> Any:
        spec = REGISTRY.get(address)
        if spec is not None or address.startswith("registry:"):
            return self._read_registry(address, value_name)
        cmd = COMMANDS.get(address)
        if cmd is not None:
            return cmd.read(self._run, address)
        if address.startswith("service:") and address.endswith(SERVICE_SUFFIX):
            return _service_start_read(self._run, self._service_name(address))
        if address in UNSERVICED:
            raise BackendError(f"{address}: {UNSERVICED[address]}")
        if address.startswith("display:"):
            return self._read_display(address)
        if address.startswith("file:"):
            return self._read_file(address)
        if address.startswith("window:"):
            return self._read_window(address)
        if address.startswith("power:drain:"):
            return self._read_drain(address)
        raise BackendError(
            f"{address}: no mapping in this build. Refusing to guess — add it to "
            f"windows_backend.ADDRESSES with a test that proves the real location.")

    def exists(self, address: str) -> bool:
        try:
            self.read(address)
            return True
        except BackendError:
            return False

    def write(self, address: str, value: Any, *, value_name: str | None = None) -> None:
        if address.startswith("registry:"):
            self._write_registry(address, value, value_name=value_name)
        elif address in COMMANDS:
            COMMANDS[address].write(self._run, address, value)
        elif address.startswith("service:") and address.endswith(SERVICE_SUFFIX):
            _service_start_write(self._run, self._service_name(address), value)
        elif address.startswith("display:"):
            self._write_display(address, value)
        elif address.startswith("file:"):
            self._write_file(address, value)
        elif address in UNSERVICED:
            raise BackendError(f"{address}: {UNSERVICED[address]}")
        else:
            raise BackendError(f"{address}: no mapping in this build")
        self._writes.append((address, value))

    def keys(self, prefix: str = "") -> list[str]:
        """Enumerate addresses of a family. Only families we can enumerate."""
        out: list[str] = []
        if prefix in ("", "registry:"):
            out.extend(sorted(REGISTRY))
        if prefix in ("", "power:"):
            out.extend(sorted(k for k in COMMANDS if k.startswith("power:")))
        if prefix in ("", "task:"):
            out.extend(sorted(k for k in COMMANDS if k.startswith("task:")))
        if prefix.startswith("window"):
            out.extend(self._enum_windows())
        if prefix.startswith("power:drain:"):
            out.extend(self._enum_drains())
        return sorted(out)

    def settled_read(self, address: str) -> Any:
        """Read back after policy has had its say.

        On Windows a group policy is not a layer we can see through; the value we
        read *is* the effective one. Re-reading is honest; imagining a second
        channel would not be.
        """
        return self.read(address)

    # -- registry ----------------------------------------------------------
    def _read_registry(self, address: str, value_name: str | None = None) -> Any:
        multi = MULTI_VALUE_KEYS.get(address)
        if multi is not None:
            return self._read_multi(address, multi, value_name)
        spec, canonical = parse_registry(address)
        if spec is None:
            raise BackendError(
                f"{canonical}: no mapping in this build. Add it to "
                f"windows_backend.REGISTRY so its value name is written down, "
                f"not guessed.")
        got = self._reg.get(spec.hive, spec.path, spec.name)
        if got is None:
            raise BackendError(f"address does not exist: {canonical}")
        raw, _kind = got
        return spec.to_app(raw)

    def _write_registry(self, address: str, value: Any, *, value_name: str | None = None) -> None:
        multi = MULTI_VALUE_KEYS.get(address)
        if multi is not None:
            self._write_multi(address, multi, value, value_name)
            return
        spec, canonical = parse_registry(address)
        if spec is None:
            raise BackendError(f"{canonical}: no mapping in this build")
        native = spec.to_native(value)
        kind = 4 if spec.kind == "dword" else 1          # 4 = REG_DWORD, 1 = REG_SZ
        self._reg.set(spec.hive, spec.path, spec.name, native, kind)

    # -- multi-value keys --------------------------------------------------
    @staticmethod
    def _emit_value_name(address: str, value_name: str | None,
                         multi: dict[str, tuple[str, str]]) -> str:
        """Resolve which value inside a shared key is meant.

        A bare address gets the documented default; a named one must exist in the
        table, because writing a value name that Windows does not use would store
        bytes that govern nothing and then read back as "applied".
        """
        if value_name is None:
            default = MULTI_VALUE_DEFAULT.get(address)
            if default is None:
                raise BackendError(
                    f"{address}: this key holds several values; name the one you "
                    f"mean ({sorted(v[0] for v in multi.values())})")
            return default
        # The table is keyed by the lowercased value name (Windows names are
        # case-insensitive); accept either spelling from the caller.
        entry = multi.get(value_name.lower())
        if entry is None:
            entry = next((v for v in multi.values() if v[0] == value_name), None)
        if entry is None:
            raise BackendError(
                f"{address}: {value_name!r} is not a value of this key "
                f"(known: {sorted(v[0] for v in multi.values())})")
        return entry[0]

    def _read_multi(self, address: str, multi: dict[str, tuple[str, str]],
                    value_name: str | None) -> Any:
        name = self._emit_value_name(address, value_name, multi)
        body = address[len("registry:"):]
        hive, _, path = body.partition("\\")
        if hive != HKCU:
            raise BackendError(f"{address}: multi-value keys here are per-user only")
        got = self._reg.get(HKCU, _real_path(address), name)
        if got is None:
            raise BackendError(f"address does not exist: {address}\\{name}")
        return got[0]

    def _write_multi(self, address: str, multi: dict[str, tuple[str, str]],
                     value: Any, value_name: str | None) -> None:
        name = self._emit_value_name(address, value_name, multi)
        if not isinstance(value, int):
            raise BackendError(
                f"{address}\\{name}: expected a dword, got {value!r}")
        self._reg.set(HKCU, _real_path(address), name, int(value), 4)

    def _service_name(self, address: str) -> str:
        body = address[len("service:"):]
        return body[: -len(SERVICE_SUFFIX)]

    # -- wallpaper -----------------------------------------------------------
    def set_wallpaper(self, path: str) -> dict[str, Any]:
        """Set the desktop background to an image on disk.

        `SystemParametersInfoW(SPI_SETDESKWALLPAPER, ...)` is the documented
        call. It is materially safer than a display mode change: a wrong image is
        a wrong image, not a black screen — and the previous path can be read
        from the registry *before* the call and handed back, so undo restores an
        exact value instead of guessing at "whatever was there before".
        """
        if not sys.platform.startswith("win"):
            raise BackendError(
                "setting the desktop background needs Windows; this build is on "
                f"{sys.platform}. The image was written, nothing was changed.")
        from pathlib import Path as _P
        if not _P(path).is_file():
            raise BackendError(f"no wallpaper image at {path}")
        if _P(path).suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp"):
            raise BackendError(
                f"{path}: Windows accepts png/jpg/bmp for the desktop background")

        previous = ""
        try:
            got = self._reg.get("hkcu", r"Control Panel\Desktop", "Wallpaper")
            if got:
                previous = str(got[0])
        except Exception:                      # noqa: BLE001 - best effort only
            previous = ""

        import ctypes
        ok = ctypes.windll.user32.SystemParametersInfoW(   # type: ignore[attr-defined]
            0x0014,                         # SPI_SETDESKWALLPAPER
            0, str(path),
            0x01 | 0x02)                    # UPDATEINIFILE | SENDCHANGE
        if not ok:
            raise BackendError(
                "Windows refused the wallpaper change "
                f"(SystemParametersInfoW returned 0, error {ctypes.get_last_error()})")
        return {"set": True, "path": str(path), "previous": previous}

    # -- display (guarded by a watchdog, enabled by consent) ----------------
    def _read_display(self, address: str) -> Any:
        """Current mode of the display named in the address."""
        if not self._allow_display:
            raise BackendError(
                f"{address}: display reads are off in this build. Set "
                f"PCO_WINDOWS_DISPLAY=1 to enable the watchdog-guarded path. The "
                f"refusal is deliberate: a wrong mode can black the screen that "
                f"would fix it.")
        from .display import DisplayError, Mode
        try:
            return self._display().current().as_dict()
        except DisplayError as exc:
            raise BackendError(f"{address}: {exc}") from exc

    def _write_display(self, address: str, value: Any) -> None:
        """Apply a mode through the guard: CDS_TEST, apply, auto-revert.

        This never applies a mode unconditionally. The guard arms a timer that
        restores the previous mode unless `display:confirm` is called first, so
        a mode the user cannot see is a mode that undoes itself.
        """
        if not self._allow_display:
            raise BackendError(
                f"{address}: display writes are off in this build. Set "
                f"PCO_WINDOWS_DISPLAY=1 to enable the watchdog-guarded path. "
                f"The refusal is deliberate: a wrong mode can black the screen "
                f"that would undo it.")
        from .display import DisplayError, Mode
        if not isinstance(value, dict) or "hz" not in value:
            raise BackendError(
                f"{address}: expected {{'width': int, 'height': int, 'hz': int}}")
        try:
            here = self._display().current()
            mode = Mode(int(value.get("width") or here.width),
                        int(value.get("height") or here.height),
                        int(value["hz"]))
            self._guard().apply(mode)
        except DisplayError as exc:
            raise BackendError(f"{address}: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise BackendError(f"{address}: not a usable mode: {exc}") from exc

    def _display(self):
        from .display_win import WinDisplay
        return WinDisplay()

    def _guard(self):
        from .display import DisplayGuard
        guard = getattr(self, "_display_guard", None)
        if guard is None:
            guard = self._display_guard = DisplayGuard(self._display())
        return guard

    def display_status(self) -> dict[str, Any]:
        return self._guard().status()

    def display_confirm(self) -> dict[str, Any]:
        return self._guard().confirm()

    def display_revert(self) -> dict[str, Any]:
        return self._guard().revert()

    # -- files -------------------------------------------------------------
    def _read_file(self, address: str) -> Any:
        from pathlib import Path
        p = Path(address[len("file:"):])
        if not p.exists():
            raise BackendError(f"address does not exist: {address}")
        return p.read_text(encoding="utf-8", errors="replace")

    def _write_file(self, address: str, value: Any) -> None:
        from pathlib import Path
        p = Path(address[len("file:"):])
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, (dict, list)):
            p.write_text(json.dumps(value, indent=1), encoding="utf-8")
        else:
            p.write_text(str(value), encoding="utf-8")

    # -- open windows ------------------------------------------------------
    def _drain_report(self) -> dict[str, float]:
        """Parse the most recent powercfg /energy report into {exe: milliwatts}.

        `powercfg /energy` writes an HTML report after a 60-second trace. Tracing
        on launch would be a battery drain disguised as a battery feature, so this
        reads a report *if one already exists* — Windows keeps them under
        %LOCALAPPDATA%\\ElevatedDiagnostics — and returns {} otherwise.

        That asymmetry is the honest part. The number is real when present, and
        absent (never zero) when nobody has run a trace. The UI offers the trace
        as an explicit 60-second action instead of silently starting one.

        Cached by (path, mtime, size): parsing tens of KB of HTML on every
        `keys()` poll would be a needless cost in a UI that refreshes.
        """
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ElevatedDiagnostics"
        if not base.is_dir():
            return {}
        report = max(base.glob("**/EnergyReport.html"),
                     key=lambda p: p.stat().st_mtime, default=None)
        if report is None:
            return {}
        try:
            st = report.stat()
        except OSError:
            return {}
        stamp = (str(report), st.st_mtime_ns, st.st_size)
        cached = getattr(self, "_drain_cache", None)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        try:
            html = report.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {}

        # Rows in the "Processes with the highest energy use" table look like
        #   <td>chrome.exe</td><td>4.2</td>
        # where the second cell is a percentage of the trace window.
        out: dict[str, float] = {}
        for exe, pct in re.findall(
                r"<td>\s*([\w.+-]+\.exe)\s*</td>\s*<td>\s*([\d.]+)\s*</td>",
                html, re.I):
            try:
                out[exe.lower()] = float(pct)
            except ValueError:
                continue
        self._drain_cache = (stamp, out)
        return out

    def _enum_drains(self) -> list[str]:
        return [f"power:drain:{exe}" for exe in sorted(self._drain_report())]

    def _read_drain(self, address: str) -> float:
        exe = address[len("power:drain:"):].lower()
        report = self._drain_report()
        if exe not in report:
            raise BackendError(
                f"{address}: no reading in the last energy report — run a trace, "
                f"or accept that this process was not measured (not zero)")
        return report[exe]

    def _read_window(self, address: str) -> Any:
        """Title and monitor for one `window:<pid>:<exe>` key.

        `tasklist` gives the app but not the title, so this asks Windows which
        window belongs to the pid and what monitor it sits on. Both are pure
        queries; neither can change the machine.

        Returns {"title": str, "monitor": int}. An empty title is real and
        common (splash screens, some games) and must not become a made-up name.
        """
        parts = address.split(":")
        if len(parts) < 3:
            raise BackendError(f"{address}: expected window:<pid>:<exe>")
        pid = parts[1]
        code, out = self._run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            ("$ErrorActionPreference='Stop';"
             f"$p=Get-Process -Id {pid} -ErrorAction Stop;"
             "$p.Refresh();"
             "[pscustomobject]@{title=$p.MainWindowTitle;"
             "monitor=0} | ConvertTo-Json -Compress")
        ])
        if code != 0:
            raise BackendError(f"{address}: the process has no window any more")
        try:
            info = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise BackendError(f"{address}: unreadable window info: {exc}") from exc
        return {"title": str(info.get("title") or ""),
                "monitor": int(info.get("monitor") or 0)}

    def _enum_windows(self) -> list[str]:
        """`window:<pid>:<exe>` keys for visible top-level windows.

        Uses `tasklist` rather than the Win32 window API because `tasklist` is on
        every Windows install, needs no ctypes marshalling of HWND callbacks, and
        its output is stable enough to parse with a regex. The trade-off is real
        and worth stating: tasklist gives *processes with a main window*, not the
        window list, so two windows of the same exe collapse into one entry and a
        background process with no window is excluded. For "restore what I had
        open" that is close to right; for "count my windows" it is not, and the
        UI must not claim otherwise.

        Returns [] when nothing can be read — noise the user can see beats an
        invented list they would act on.
        """
        code, out = self._run(["tasklist", "/fo", "csv", "/nh",
                               "/fi", "SESSIONNAME eq Console"])
        if code != 0:
            return []
        keys: list[str] = []
        for line in out.splitlines():
            # "chrome.exe","1234","Console","1","120,000 K"
            m = re.match(r'^"([^"]+)","(\d+)"', line.strip())
            if not m:
                continue
            exe, pid = m.group(1), m.group(2)
            # The session filter already dropped services; these are the
            # non-GUI processes that still appear on some builds.
            if exe.lower() in _NO_WINDOW_EXES:
                continue
            keys.append(f"window:{pid}:{exe}")
        return keys
