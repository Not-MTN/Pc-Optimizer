# From here to a real PC

Everything in this repo runs today on any OS against a **simulated machine**.
This document is the path from that to *"it changed something on my actual
Windows 11 PC, and I can undo it."*

It is written as steps you can do, in order, with the exact command and the exact
thing to expect back. Nothing here needs a Rust toolchain.

---

## Where the "blocked" actually is

Four separate things, often lumped together. Only the first two are blocked by
this sandbox, and both are unblocked by *you pressing a button*.

| Thing | Needs | Blocked by | Unblocked by |
|---|---|---|---|
| Real registry / services / tasks / power plan | Windows 11 | no Windows here | you run `run.ps1 -Real` |
| Refresh-rate / HDR writes | Windows 11 + a display | unverified code | CI on `windows-latest`, then a careful opt-in |
| The Rust + WinUI 3 shell | Rust + Windows | no toolchain here | `cargo` on your PC, or CI |
| Signing & installer | a certificate | money + identity | later; not needed to *use* it |

The safety core — journal, preflight, snapshots, recovery, drift — is done and
tested. What was missing was the bottom edge of the stack, and that is now
written: `engine/pco_engine/windows_backend.py`.

---

## Step 1 — run the tests on Windows (5 minutes, no risk)

This is the single most valuable thing you can do, because it tests the one part
that cannot be tested on Linux.

```powershell
git clone https://github.com/Not-MTN/Pc-Optimizer.git
cd Pc-Optimizer
Set-ExecutionPolicy -Scope Process Bypass -Force
.\run.ps1 -Test
```

Expect: `335 passed, 11 skipped` on Linux, and on Windows the 11 skipped tests in
`tests/test_real_windows.py` either **pass** or skip with a reason that names what
is missing on your machine. Those skipped ones are the real-hardware contract:
they assert that each address in the mapping table points at a place Windows
actually keeps that setting.

If one fails, the message is the bug — open an issue with that line and it is a
one-line fix in `REGISTRY` or `COMMANDS`.

**Or let GitHub do it.** `.github/workflows/tests.yml` already runs both jobs on
every push. Push the branch, open the Actions tab, and read the `windows-latest`
job. No local setup at all.

---

### What the Windows runner actually proved

This repo now runs both jobs on every push, and the `windows-latest` job has
already been green. Here is precisely what it established, so you know what is
**verified** rather than merely written:

| Test | Result on windows-latest |
|---|---|
| `test_the_backend_loads_on_windows` | ✅ passed |
| `test_sc_exe_answers_in_the_format_the_parser_expects` | ✅ **passed** — `sc.exe qc wuauserv` really does emit the `START_TYPE` line the parser expects |
| `test_powercfg_reports_a_scheme_we_can_name` | ✅ **passed** — `powercfg /getactivescheme` parsed to a named plan |
| `test_display_path_is_refused_by_default_on_real_windows_too` | ✅ passed |
| SMB1 registry value at the documented path | ⏭ skipped — **the runner image does not have that value** |
| Telemetry policy key | ⏭ skipped — no such policy on a clean CI image |
| Office background task | ⏭ skipped — Office is not installed |
| The mutating round-trip | ⏭ skipped (opt-in, needs `PCO_WRITE_TESTS=1`) |

**Read that honestly:** the *console tools* are verified against real Windows.
The two *registry* paths are not, because a fresh CI image has neither the SMB1
flag nor a telemetry policy — those exist on a real user's PC, not a clean VM.
That is the gap step 3 on your machine closes, and it is a two-minute check:

```powershell
Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters' -Name SMB1
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Name AllowTelemetry
```

If both return a value, `doctor` on your PC will say `readable` for those two and
the mapping is confirmed. If they error, the key genuinely is not there and the
checkup will (correctly) refuse rather than invent a default — in which case run
`security.smb1.disable` anyway: the write **creates** the value, which is the
documented Windows behaviour for that flag.

---

## Step 2 — see what it thinks it can touch (read-only, 1 minute)

```powershell
.\run.ps1 -Real
```

That runs `pco --backend windows doctor` **before** the console opens, and prints
one of these per catalog item:

- `readable` — the address resolved; this fix can be applied and verified.
- `not present on this machine` — the setting does not exist here. The checkup
  will say so rather than inventing a default.
- `not built yet` — the address is known but unserviceable, and says which
  component is missing (`driver.t2.example` needs a signed driver;
  `startup.defer.*` needs the launcher).

`doctor` **writes nothing.** Run it as often as you like.

### What is expected to work on a stock Windows 11 PC today

| Tweak | Address | Status |
|---|---|---|
| `display.set_refresh` | `display:primary.mode` | **refused** until verified (see step 4) |
| `display.hdr.on` | `display:primary.hdr.mode` | refused |
| `service.diagtrack.disable` | `service:diagtrack.start_type` | works (elevated shell) |
| `power.scheme.high` | `power:active_scheme` | works |
| `task.office.background.disable` | `task:\microsoft\office\…state` | works if Office is installed |
| `privacy.telemetry.minimum` | `registry:hklm\…\datacollection` | works (elevated) |
| `security.smb1.disable` | `registry:hklm\…\lanmanserver\parameters` | works (elevated) |
| `startup.defer.*` | `startup:….defer_seconds` | **refuses**, names the launcher |
| `driver.t2.example` | `driver:pco_dpc.state` | **refuses**, needs the signed driver |

A refusal that names the missing piece is the product working. A write that
silently does nothing is the failure mode this whole codebase is built around.

---

## Step 3 — apply one real change (the moment)

Open an **elevated** PowerShell (Registry and `sc.exe config` need it), then:

```powershell
cd Pc-Optimizer\engine
..\venv\Scripts\python.exe -m pco_engine.cli --backend windows plan power.scheme.high
..\venv\Scripts\python.exe -m pco_engine.cli --backend windows apply power.scheme.high
```

Pick `power.scheme.high` first: it is low-risk, it visibly changes the Power
Options panel, and reverting it is instant.

What you should see, in order:

1. A **preflight** line for each effect, with the old and new value.
2. `need restart` if the change requires one (SMBv1 does; the power plan does not).
3. A **transaction id**, and a journal row count that went up by four per effect
   (`begin → applied → verified`, plus the receipt).
4. `powercfg /getactivescheme` now reporting *High performance*.

Then the part that matters:

```powershell
..\venv\Scripts\python.exe -m pco_engine.cli --backend windows history
..\venv\Scripts\python.exe -m pco_engine.cli --backend windows revert <tx_id>
```

It goes back. The receipt is marked undone. **That round trip is the product.**

### The risky one, and the safety net

```powershell
..\venv\Scripts\python.exe -m pco_engine.cli --backend windows apply security.smb1.disable
```

`security.smb1.disable` is `high` risk, so it is **armed**: the engine starts a
60-second confirmation window. If you do nothing, `tick` reverts it on its own —
and if the app was closed for most of the window, it records that the window was
*missed* rather than treating silence as consent.

This is the feature that was dead until v0.5 (bug 26: `require_confirm` was never
resolved from the plan). Step 3 is the first place it becomes observable on real
hardware, so watch for it.

---

## Step 4 — the display path (only if you want it)

Refresh-rate changes are the single most dangerous write in the product: a wrong
mode on the primary monitor can leave you with a black screen on a display you
cannot recover. So `WindowsBackend` **refuses** `display:` writes by default, even
on Windows, and the refusal names why.

To enable it you would need, in order:

1. `read` implemented via `EnumDisplaySettingsW` for `DISPLAY_DEVICE_PRIMARY_DEVICE`.
2. `write` implemented as: `ChangeDisplaySettingsW(…, CDS_TEST)` → if that fails,
   refuse and log; if it succeeds, apply with `CDS_UPDATEREGISTRY`, then re-read.
3. A watchdog: if the new mode is not confirmed within 15 s, restore the
   pre-change mode automatically (the snapshot already holds it).
4. That watchdog tested on at least one machine, including a deliberately bad
   mode that must be rejected.

Until all four exist, the refusal is correct. `PCO_WINDOWS_DISPLAY=1` exists so
the work can be done; it should never be set on a machine you are not sitting in
front of.

---

## Step 5 — the Rust + WinUI 3 port

Optional, and genuinely blocked here because there is no `rustc`/`cargo` and no
Windows SDK in this sandbox. Two honest paths:

**A. Do it on your PC.** Install the [Rust toolchain](https://rustup.rs) and the
`windows` crate; `ARCHITECTURE.md` has the module-by-module mapping and
`engine/README.md` has the port contract — the checksum formula and the JSON keys
are the two things that must not drift.

**B. Do it in CI.** `windows-latest` runners have `cargo` and the MSVC toolchain
preinstalled. A `cargo test` job in `.github/workflows/` would give the port the
same safety net the Python has: every commit, on Windows, for free.

Do not port before step 3 works. A Rust rewrite of a core that has never touched
a real machine just gives you two untested cores.

---

## What is still missing, stated plainly

- **Per-process battery drain** (`power:drain:<exe>`) returns nothing on Windows.
  It needs `CallNtPowerInformation` + process energy estimation. The UI shows
  "nothing measured", which is true.
- **Window enumeration** for Workspaces raises rather than returning a list.
  It needs `EnumWindows` + the process name per `HWND`.
- **Charge limits** are unsupported everywhere: firmware-dependent, and claiming
  it without firmware access would be the most damaging lie in the app.
- **Startup deferral** refuses until the PCO launcher exists, because rewriting a
  Run entry you cannot honour is how an app silently stops something from
  starting.

Each of those is a small, isolated addition behind an address that already has a
name in the table. None of them requires the safety core to change — which is the
whole point of `Backend` being the only seam.
