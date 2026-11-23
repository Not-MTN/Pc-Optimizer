# Guardian engine — reference implementation

The safety core of PC Optimizer, written in Python so it can be **run and tested on any OS**, then ported to Rust 1:1.

> Why Python first: the engine's logic (journal, preflight, snapshots, recovery) is the part that must never be wrong, and it is 100% testable without Windows. Writing it twice — once to discover the design, once for production — is cheaper than debugging a broken safety net in the field. The Rust port keeps the same module, class and method names.

## Run it

```bash
cd engine
python3 -m venv .venv && .venv/bin/pip install -q pytest

# the promise, demonstrated end to end
.venv/bin/python -m pco_engine.cli --fresh crash

# drive it by hand
.venv/bin/python -m pco_engine.cli catalog
.venv/bin/python -m pco_engine.cli plan display.set_refresh startup.defer.onedrive
.venv/bin/python -m pco_engine.cli apply display.set_refresh
.venv/bin/python -m pco_engine.cli revert <tx_id>
.venv/bin/python -m pco_engine.cli status
.venv/bin/python -m pco_engine.cli verify      # hash-chain check
.venv/bin/python -m pco_engine.cli drift
.venv/bin/python -m pco_engine.cli snapshot --kind golden
.venv/bin/python -m pco_engine.cli diff <id-a> <id-b>

# tests
.venv/bin/python -m pytest tests -q
```

## What works today (53 tests, all passing)

| Guarantee | Where it's proven |
|---|---|
| Every change is journaled **before** the machine is touched | `test_journal_records_full_lifecycle`, `test_power_loss_mid_batch_is_recovered` |
| Journal is append-only and tamper-evident | `test_storage_layer_blocks_update_and_delete`, `test_tampering_is_detected`, `test_truncation_is_detected` |
| Conflicts are caught before anything is written | `test_conflict_between_two_tweaks_in_one_batch` |
| Unprovable preconditions refuse instead of guessing | `test_precondition_failure_blocks`, `test_unknown_tweak_blocks` |
| A failed write rolls back earlier writes in the batch | `test_failed_write_rolls_back_earlier_writes` |
| A value the OS refuses to keep is detected, not assumed | `test_value_that_does_not_stick_is_detected` |
| Crash / power loss is recoverable to a consistent state | `test_power_loss_mid_batch_is_recovered` + `pco crash` |
| Risky changes auto-revert if not confirmed | `test_armed_transaction_auto_reverts_when_expired` |
| Drift (Windows Update undoing us) is detected | `test_drift_detected_when_outside_world_changes_value` |
| Snapshots are small, verifiable and diffable | `test_snapshots.py` |

## Architecture

```
catalog/tweaks.json ──► Catalog ──► Preflight ──► PLAN (read-only)
                                       │
                                       ▼
                                   Engine.apply()
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
    SnapshotStore                 Journal.append(begin)           Backend.write()
    (pre-state)                   (durable intent)                (the only real I/O)
                                       │
                                       ▼
                             Journal.append(applied/verified)
                                       │
                                       ▼
                                  managed projection ──► drift detection
```

`Backend` is the single seam to reality. Everything above it is pure and deterministic.

## Files

| File | Responsibility |
|---|---|
| `pco_engine/address.py` | canonical addresses — one string per managed thing |
| `pco_engine/catalog.py` | tweak documents, validation, index |
| `pco_engine/backend.py` | the only I/O seam + `FakeBackend` (and policy `pin()`) |
| `pco_engine/preflight.py` | simulation, conflicts, preconditions, apply order |
| `pco_engine/journal.py` | append-only hash-chained log, interruption detection |
| `pco_engine/snapshots.py` | capture, verify, diff (time machine) |
| `pco_engine/engine.py` | apply / confirm / revert / recover / drift |
| `pco_engine/store.py` | connection, pragmas, migrations |
| `pco_engine/cli.py` | the `pco` command |

## Bugs this harness already caught

Recorded because they are the argument for building the tests first:

1. **`metric_series` PK/nullability** — a device-less series could never be inserted (found by executing the schema in real SQLite).
2. **`executescript` inside a transaction** — migrations reported success while committing nothing.
3. **`keys()` treated as truth** — an address absent from the index was reported as existing; `read()` is now the authority.
4. **Unprovable preconditions passed** — `equals` on a missing address returned "ok". It now refuses.
5. **False interruptions** — every *committed* transaction looked crashed on the next startup, so recovery would have silently reverted good changes on every boot. This one would have been a field disaster.
6. **Stale reads hid policy overrides** — a value re-imposed by policy looked successfully applied.

## Porting to Rust (the contract)

| Python | Rust |
|---|---|
| `Backend` protocol | `trait Backend` + `WindowsBackend`, `FakeBackend` |
| `Engine.apply/revert/recover/check_drift` | same names, `Result<_, EngineError>` |
| `Journal.verify_chain()` | same formula: `sha256(prev|seq|tx|tweak|phase|at|pre|post|error)` |
| `FakeBackend` | `FakeBackend` behind `#[cfg(test)]` and the `testkit` feature |
| CLI JSON | identical keys (the future UI and `pco` share them) |

The checksum formula and the JSON keys are the two things that must not drift.

## Not built yet (deliberately)

- `WindowsBackend` (registry/services/tasks/display/power) — needs a Windows machine to test against.
- Boot guard service, panic hotkey, Scheduler (`Sentinel`), Bench, Checkup scanner, Cleaner, DNS resolver.
- All UI.

Each of those is specified already; this package is the foundation they all stand on.
