# Guardian engine — reference implementation

The running app: a local server + dark console that drives the real engine over
JSON, plus the safety core beneath it, written in Python so it can be **run and
tested on any OS**, then ported to Rust 1:1.

> Why Python first: the engine's logic (journal, preflight, snapshots, recovery) is the part that must never be wrong, and it is 100% testable without Windows. Writing it twice — once to discover the design, once for production — is cheaper than debugging a broken safety net in the field. The Rust port keeps the same module, class and method names.

## Run it

```bash
cd engine
python3 -m venv .venv && .venv/bin/pip install -q pytest

# THE APP — console at http://localhost:7788
.venv/bin/python -m pco_engine.server

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

## The app

```
.venv/bin/python -m pco_engine.server --port 7788
```

One stdlib HTTP server, no dependencies, no build step. It binds `0.0.0.0` and
the UI uses **relative URLs only**, so it works on `127.0.0.1` and behind a
proxy identically. `pco_data/` (db + vault + demo machine) is created on first
run and git-ignored.

| View | What it drives |
|---|---|
| Overview | health ring, tracker count, vault size, live event feed, journal verification |
| Checkup | per-category scores, findings tagged auto-safe / review, one-click "fix everything safe", receipts with Undo |
| Cleaner | scan by rule, move to vault, restore, 30-day retention |
| Blocking | on / off / strict, pause 30 min, per-app rules, allow rules, counters |
| Sens Lab | cm/360 ⇄ every game, eDPI, and the PSA two-candidate test (6 rounds to <1 cm) |
| Gaming | watch gameplay → frame stats, bottleneck diagnosis, settings plan, session history |
| Autopsy | watch an install, read what it left, erase exactly that (critical items immune) |
| Studio | themes with a declared idle-GPU cost; anything over 3% is refused |
| Performance | benchmark score vs baseline, weekly report, drift check |

Plus a **consent** control on the Checkup view: `safe` applies only catalog-declared
auto-safe fixes; `trusted` also applies review-tier items *that have a real fix*.
Nothing is applied without a declared tweak, and every batch is undone in one click.

Every response is JSON with the exact keys the future WinUI shell consumes, so
the console is a reference client, not a toy.

## What works today (177 tests, all passing)

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
| Cleaning frees disk and is **restorable** (byte-verified) | `test_clean_moves_to_vault_and_preserves_file`, `test_restore_puts_everything_back` |
| Storage findings never claim to be a speed win | `test_storage_findings_do_not_lower_speed_score` |
| Policy-class junk is never auto-selected | `test_policy_class_is_never_auto_selected` |
| The blocker's off switch actually allows traffic, instantly | `test_mode_off_allows_everything`, `test_toggle_is_instant` |
| Pause expires on its own; allow rules beat lists | `test_pause_stops_blocking_then_expires`, `test_allow_rule_wins` |
| Recipes can't loop and never exceed 10 actions | `test_recipe_rejects_loops`, `test_recipe_capped_at_ten_actions` |
| cm/360 round-trips and CS2→VALORANT matches the ÷3.18 constant | `test_round_trip_is_exact`, `test_cs2_to_valorant_is_the_known_ratio` |
| Non-linear-ADS games are flagged, not faked | `test_table_has_every_game_and_flags_non_linear` |
| The PSA test converges to under 1 cm in 6 rounds | `test_psa_converges_below_one_centimetre` |
| The HTTP bridge works over real sockets, incl. undo | `test_web.py` (23 socket-level tests) |
| **Every button in the console has a route behind it** | `test_ui_contract.py` (10 contract tests) |
| Bad client input is a 400 with a reason, never a 500 with a traceback | `test_bridge_rejects_bad_theme_json_with_400_not_500` |
| Autopsy survives the full HTTP round trip and still spares the runtime | `test_bridge_autopsy_records_erases_and_keeps_critical` |
| Average fps never hides bad frame pacing | `test_average_fps_is_not_the_whole_story` |
| A CPU limit is found from the busiest thread, not the average | `test_cpu_limit_is_named_by_the_busiest_thread_not_the_average` |
| Thermal and VRAM limits are named *before* the naive "GPU is full" | `test_thermal_masquerading_as_gpu_is_caught_first`, `test_vram_exhaustion_beats_the_naive_gpu_reading` |
| A healthy machine gets no invented problem | `test_healthy_machine_gets_no_invented_problem` |
| Autopsy never erases a shared runtime | `test_autopsy_records_and_erases_non_critical` |
| A failed erase is reported, not swallowed | `test_partial_failure_is_reported_not_hidden` |
| Studio refuses a theme over the 3% idle-GPU budget | `test_a_heavy_theme_is_refused_with_numbers` |
| Import validates but never applies | `test_import_validates_without_applying` |
| "Fix everything" cannot pretend to fix an observation | `test_trust_never_applies_something_with_no_fix` |
| Consent survives a restart and resets to the documented default | `test_consent_survives_a_restart`, `test_reset_returns_to_the_documented_default` |
| Applying writes a receipt with before/after values; undo marks it | `test_applying_writes_a_receipt_and_undo_marks_it` |
| Fixing an already-fixed machine is a no-op, not an error | `test_applying_something_already_applied_is_success_not_an_error` |

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
| `pco_engine/service.py` | **the one object the UI talks to**; owns every module, one action catalog |
| `pco_engine/server.py` | stdlib HTTP bridge + static console, binds 0.0.0.0, relative URLs |
| `pco_engine/console.html` | the console UI — inline CSS/JS/SVG, no network, no build |
| `pco_engine/checker.py` | 9 checks in 5 categories; scores that can't borrow credibility |
| `pco_engine/cleaner.py` | rules-as-data, vault, sha256 verify, restore, retention |
| `pco_engine/bench.py` | 5 metrics, median of 3, stability flag, "no measurable change" under 3% |
| `pco_engine/dns.py` | matcher, modes, pause, per-app, allow rules, counters |
| `pco_engine/sens.py` | yaw table, cm/360, eDPI, PSA binary search |
| `pco_engine/recipes.py` | when-X-do-Y, capped, no loops, one receipt per run |
| `pco_engine/sentinel.py` | event feed: nothing polls, plugins push |
| `pco_engine/gaming.py` | frame analysis, bottleneck classifier, settings plan, profiles |
| `pco_engine/autopsy.py` | install watcher + eraser with a critical-item immune system |
| `pco_engine/studio.py` | theme validation and the idle-GPU governor |
| `pco_engine/consent.py` | the one gate every automatic fix passes through |
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
7. **A tweak whose goal was its own precondition** — `power.scheme.high` demanded `active_scheme == "high"` *before* applying it, so the single most common first fix could never be planned from Balanced. Added `not_equals`, which is the honest way to say "only offer this if we're not already there".
8. **`os.path.exists()` on a glob** — every cleaner rule silently matched nothing, so the Cleaner would have shipped reporting "0 bytes" forever.
9. **Cleaner left its journal transaction open** — the next boot would have "recovered" a clean run that finished perfectly, moving files back without being asked.
10. **Cross-thread SQLite** — the bridge's worker threads all hit one connection; every request 500'd until `check_same_thread=False` + an explicit lock made the threading model explicit.
11. **Sens profile saved under the wrong key** — "I play CS2 at 1.6" stored a per-game row the table never read, so the conversion page showed a stale 40 cm/360.
12. **Double counting in the cleaner** — one temp file listed under two rules reported 14 files when there were 10.
13. **PSA that couldn't converge** — the first binary search compared a value against itself, so the range never shrank.
14. **Consent aliasing** — one shared default dict meant a single profile's "trust everything" became every later profile's default, including after `reset()`. A saved permission that silently changes is the worst class of bug in this product.
15. **Fix-all failed on a healthy machine** — one already-correct item aborted the whole batch, so a second click reported a red error on a machine with nothing wrong. Batch application now filters satisfied tweaks first.
16. **An unfixable privacy finding** — "telemetry at the full level" had no catalog tweak, so the trusted tier legitimately did nothing. The privacy fix now exists and targets the real Windows policy key.
17. **No receipt for applies** — the engine wrote journal rows but no receipt, so History was empty after the most important action in the product.
18. **A dead button shipped unnoticed** — the console posted to `/api/autopsy/erase`, which had no route. JS syntax checks and Python tests both passed. Fixed by writing `test_ui_contract.py`, which parses the console and asserts every URL it calls exists in the router (and vice versa). It immediately found a second one: the fix-all button was bypassing the new consent path.
19. **Autopsy erased nothing** — `/api/autopsy/begin` never recorded items, so "erase every trace" erased an empty list and reported success. The bridge can now record what a watcher found (note/finish), and the flow is tested over HTTP with a real critical file in the list.
20. **Validation errors were 500s** — an invalid theme returned a Python traceback to the browser. Bad input is now a 400 with the reason.

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

- `WindowsBackend` (registry/services/tasks/display/power) — needs a Windows machine; the reference `Backend` is the whole seam, so this is one file.
- The DNS resolver itself (this repo has the decision engine; the resolver needs Windows NRPT).
- The Rust port and the WinUI 3 shell.
- Widgets, wallpaper renderer, theme application to the real shell (the governor and
  document format exist; the WinUI renderer needs Windows).
- Game auto-detect via process watcher (the library, classifier and plan are done;
  PresentMon wiring needs Windows).
- The installer watcher itself (deltas are recorded and erasable; the ETW/registry
  monitor needs Windows).

Each is specified; none of them needs the safety core to change.
