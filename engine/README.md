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
| Storage | what grew this week, a forecast of when you hit 90%, and hash-confirmed duplicates |
| Startup | what starts with Windows, what is safe to defer, and what is refused (with the reason) |
| Hardware Lab | mouse double-click, keyboard chatter, sensor jitter, polling rate, monitor patterns, and the printable Health Report |
| Gaming | watch gameplay → frame stats, bottleneck diagnosis, settings plan, session history |
| Autopsy | watch an install, read what it left, erase exactly that (critical items immune) |
| Studio | themes with a declared idle-GPU cost; anything over 3% is refused |
| Performance | benchmark score vs baseline, weekly report, drift check |

Two things cut across every view:

* **Command palette** (`Ctrl/⌘ K`) — engine-native verbs, fuzzy search, no console
  flash. Shell/script executors are refused unless explicitly enabled.
* **Ask** — "what did I change?", "why is my disk full?", "am I secure?" answered
  from the journal and the live database, never from generated prose.

Plus a **consent** control on the Checkup view: `safe` applies only catalog-declared
auto-safe fixes; `trusted` also applies review-tier items *that have a real fix*.
Nothing is applied without a declared tweak, and every batch is undone in one click.

Every response is JSON with the exact keys the future WinUI shell consumes, so
the console is a reference client, not a toy.

## What works today (555 tests: 544 here, 11 that only a real Windows machine can run)

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
| An unmeasured startup item is never counted as zero | `test_unmeasured_items_are_unattributed_never_zero` |
| A service with dependents is refused and the dependent is named | `test_service_with_dependents_is_refused_and_named` |
| "Your disk will fill in 78 days" is arithmetic, not a guess | `test_two_samples_produce_a_ranked_delta` |
| Duplicates are only grouped after the bytes match | `test_confirmed_only_after_bytes_match`, `test_different_files_are_never_grouped` |
| A mouse that double-fires says "hardware fault", and RMA advice | `test_failing_switch_is_called_a_hardware_fault` |
| No clicks recorded is "inconclusive", never a pass | `test_no_clicks_is_inconclusive_not_a_pass` |
| The Health Report's numbers match the live machine | `test_report_numbers_match_the_machine` |
| The report HTML escapes hostile text | `test_html_escapes_hostile_text` |
| Every "ask" answer is traceable to a journal row | `test_every_answer_is_traceable` |
| The palette refuses shell execution by default | `test_shell_commands_are_refused_by_default` |
| A high-risk change is **armed**, and reverts if you never confirm it | `test_a_risky_change_is_armed_by_default`, `test_an_expired_window_reverts_on_its_own` |
| A window that expired while the app was closed is *named*, not silently kept | `test_a_window_that_expired_while_offline_is_named_as_missed` |
| Confirming keeps the change through its window | `test_confirming_keeps_the_change` |
| Battery health is a percentage of design capacity, not a guess | `test_battery_health_is_reported_as_a_share_of_design` |
| Drain shares are of the measured total, and the residual is last | `test_drain_shares_are_of_the_measured_total`, `test_bridge_power_reports_health_and_drain` |
| Heat alone is never called throttling | `test_heat_alone_is_not_throttling` |
| A charge limit the firmware does not expose says "unsupported" | `test_bridge_thermal_never_claims_throttling_from_heat_alone` |
| Workspace restore **previews by default** | `test_restore_defaults_to_dry_run` |
| A workspace entry survives a JSON round trip with its type intact | `test_round_trip_keeps_entry_details` |
| With no launcher, every restore item says so instead of pretending | `test_bridge_workspace_capture_then_preview_then_restore` |
| Drivers are graded only from this machine's crash history | `test_bridge_drivers_grade_from_local_history_only` |
| No driver history is reported as **unknown**, never "good" | `test_inventory_scores_every_driver` |
| A sentence is parsed into a preview and **never saves or runs** | `test_bridge_recipe_parse_never_saves_or_runs`, `test_parse_never_saves` |
| An unparseable sentence is refused and says what it could not read | `test_unknown_sentence_is_refused_with_the_grammar` |
| Panic reverts everything in effect and turns the blocker off | `test_bridge_boot_status_and_panic`, `test_panic_is_recorded_as_critical` |
| A broken journal chain forces read-only, never a "repair" | `test_a_broken_journal_forces_read_only` |
| Every catalog address has a **Windows mapping or a documented refusal** | `test_every_catalog_address_has_a_mapping_or_a_documented_refusal` |
| A registry address names its value, not just its key | `test_smb1_reads_the_named_value_not_the_key` |
| `"required"` translates to the native number, both ways | `test_telemetry_translates_required_to_the_native_level` |
| A service's *description* cannot flip its start type | `test_disabled_is_not_confused_with_auto_start` |
| An OEM power plan is reported as OEM, never as "balanced" | `test_an_oem_power_scheme_is_reported_as_unknown_not_as_balanced` |
| An unelevated shell produces a privilege message, not a mystery | `test_a_denied_write_explains_the_privilege_problem` |
| Display writes refuse until verified on hardware | `test_display_writes_are_refused_until_verified` |
| The real registry/sc.exe/schtasks/powercfg paths are checked **on Windows** | `test_real_windows.py` (11 tests, skipped here) |
| Every desktop setting names the registry path **and value name** it writes | `test_every_option_names_the_registry_value_it_writes` |
| Windows 11's refusals are stated, with the reason | `test_the_refusals_are_real_and_explained` |
| A preview writes nothing; a refusal never reports success | `test_bridge_shell_preview_writes_nothing`, `test_service_apply_treats_a_refusal_as_a_refusal` |
| Ten settings in one registry key stay distinct | `test_one_key_can_hold_many_settings_without_colliding` |
| Undo restores the value that was changed, not the key's default | `test_undo_restores_the_right_value_when_ten_share_a_key` |
| The desktop-lab table and the backend table cannot drift apart | `test_the_desktop_lab_table_and_the_backend_table_agree` |
| An imported theme is validated whole, and never applied by importing | `test_themes_save_import_and_never_apply_on_import` |
| The GitHub mark needs evidence, not a matching word | `test_a_local_repo_called_github_is_not_github` |
| All four git remote spellings are recognised | `test_host_extraction_handles_every_git_remote_shape` |

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
| `pco_engine/startup.py` | boot inventory, knowledge base, dependency refusals, relapse watchdog |
| `pco_engine/dupes.py` | three-stage duplicate cascade with an explainable keeper |
| `pco_engine/space.py` | folder-size time travel and the 90%-full forecast |
| `pco_engine/palette.py` | engine-native command palette + permission diffs |
| `pco_engine/peripheral.py` | five hardware tests with evidence and advice |
| `pco_engine/report.py` | the printable Health Report (Markdown + self-contained HTML) |
| `pco_engine/ask.py` | local intent parsing over the journal |
| `pco_engine/power.py` | battery health from design capacity, drain attribution, thermal verdicts |
| `pco_engine/workspace.py` | named window sets, preview-first restore, capture hints |
| `pco_engine/drivers.py` | driver inventory graded from local crash history, blocks, rollback |
| `pco_engine/nlrecipe.py` | a narrow, honest natural-language grammar that always previews |
| `pco_engine/guard.py` | boot chain verification, read-only mode, panic, boot events |
| `pco_engine/windows_backend.py` | the only file that touches Windows: an explicit address → API table, refusing anything it cannot map |
| `pco_engine/shell_lab.py` | taskbar/Explorer settings, saved themes, per-user icons, terminal look |
| `pco_engine/providers.py` | which provider a command talks to, and the mark that follows — evidence first |
| `pco_engine/windows_notes.py` | claims about Windows 11 as reviewable data, not scattered in code |
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
21. **`group` is a reserved word** — the palette's table used it unquoted and every palette query failed at runtime. Caught immediately, but it is the kind of bug that only appears on the code path a user actually takes.
22. **A forecast that said "0 days"** — the demo's free-space figures implied an already-full disk, so the projection was nonsense. Fixed the seed; the forecast now says 78 days.
23. **Two different installers grouped as duplicates** — my fixture gave both 2 MB files identical bytes, and the finder correctly reported them as copies. The fixture was wrong, not the finder — but it proved the byte comparison is the only thing standing between this feature and deleting a user's file.
24. **"Am I secure?" answered "0 findings" before any checkup had run** — which reads as "all clear". It now says there is no evidence yet.
25. **Polling-rate advice was backwards** — it told you to check the USB port whether the rate was too low or too high. Now it branches.
26. **The auto-revert safety net had no net** — `tick()` implemented the confirmation window, but `Engine.apply()` never resolved `require_confirm` from the plan, so `armed_until` was always `None` and every change committed outright. A feature that existed, was tested in isolation, and could never fire in the product. Found by asking the running service for `pending_confirmations()` and getting an empty list after a high-risk apply.
27. **A catalog entry pointed at the wrong key** — `security.smb1.disable` read and wrote `HKLM\SOFTWARE\Acme\Telemetry`, a leftover from an example. The title, the knowledge article and the risk rating were all correct; only the thing it actually changed was wrong. Every earlier test passed because the *demo backend* had been given a matching invented address. Found by asserting the real registry address after an apply, then auditing every entry's `effects`/`reactions` against its own title.
28. **Workspace restore was dangerous by default** — `restore()` took `dry_run=False` while its docstring promised a preview. Six programs would have launched on the first click. The default is now the safe direction, and the dangerous one has to be asked for.
29. **Battery drain attribution dropped every process** — readings are negative while discharging, and the filter was `mw <= 0`, so the list was always just "(unattributed)". Then the residual bucket sorted *above* the real culprits. Both fixed; a named process now always outranks the remainder.
30. **Backslashes in the console**
31. **A service description could flip a start type**

Then `windows-latest` started running, and found four more that Linux could not:

32. **The demo machine followed the tests onto Windows** — the server chose its backend from `PCO_BACKEND`/auto, so on a Windows runner it pointed at the *real* machine and six bridge tests failed because the demo battery and fixtures do not exist there. The server now defaults to `demo` even on Windows: the CLI keeps `auto` (a person typed a command), but a browser tab you left open should never be able to change your PC unless you said so out loud.
33. **Demo data leaked past the simulated machine** — `DEMO_METRICS` would have turned a hardcoded "42 GB of junk" into a *finding on a real PC*, and the demo blocklists would have been installed into a real user's blocker. Both were gated on `_demo` only after the Windows failure made me look.
34. **`pco doctor` died on every Windows console** — output full of `·` and `→` against a cp1252 console raises `UnicodeEncodeError`, so the one command a nervous user runs first printed a traceback. Now the console is reconfigured to UTF-8, with a substitution fallback and an ASCII-safe JSON mode behind it.
35. **PowerShell's `>` writes UTF-16** — the CI step redirected JSON to a file and `json.load` then choked on the BOM. Not a product bug, but the kind of thing that only appears on Windows and silently wastes an afternoon. — the first `sc qc` parser searched the whole output for the word "disabled", so a service whose description reads *"...is not disabled"* was reported as disabled, and the app would then "fix" a running service back on. Now only the `START_TYPE` line is parsed, and a missing line is an error rather than a guess. Caught by a test written before the parser had ever run against `sc.exe`. — a JS string literal with `\` breaks the whole `<script>` block, which no Python test can see. Every console edit is followed by extracting the script and running `node --check`.

36. **`write()` accepted a value name and never forwarded it** — so all ten Explorer settings collapsed onto one registry value. Every write "succeeded", every read-back "did not stick", and on a real machine it would have looked like Windows ignoring the change. Found by asserting that two settings in one key end up as two registry values.
37. **A `value_names` dict keyed by address lost all but the last change** — ten settings share one address, so changing two of them wrote both to the same value. Keyed by tweak id instead. This is bug 36's twin and the reason both now have tests.
38. **Undo restored the key's default value, not the changed one** — `revert()` reads `address -> old` from the journal, and the address is a key. Undoing a widget change would have restored the *alignment*. Fixed by recovering the value name from the tweak id, which already records it.
39. **`plan_icon` rejected `imageres.dll,-54`** — the comma and index are part of a Windows icon reference, not decoration. Anchoring the regex on `$` rejected every library icon, which is how most system icons are actually specified.
40. **A plan of only-refusals reported `applied: True`** — asking for a transparent taskbar would have shown a success toast for something the app explicitly refuses to do.

Then the app got a name, an installer and four tabs, which found five more:

41. **The navigation was the problem it was meant to solve** — seventeen icon buttons in one rail, each one an equal-priority destination. Nothing is more confusing than a UI that refuses to rank itself. Replaced with four primary tabs (Home / Look / Speed / Fix), a More flyout for the rest, and a test (`test_the_rail_stays_small`) that fails if a fifth primary tab ever appears. Every one of the seventeen views is still reachable; a view you cannot reach is a feature you did not build.
42. **`GROUPS` named a view that does not exist** — `bench` was listed under Speed; the benchmark lives inside `power`. Nothing would have crashed: the flyout would have offered a button that silently did nothing, which is precisely the failure mode the contract test exists to catch. It compares every name in `GROUPS` against every `id="view-…"` in both directions.
43. **The hub screens were reachable but empty** — the primary buttons opened a group, and the group had no content because only leaf views had bodies. A blank screen behind a tab is worse than a hidden one. The primary tabs are now real hub screens with a card per destination.
44. **`app.py` set the wrong environment variable** — it wrote `PCO_DATA_DIR` while `server.py` reads `PCO_DATA`, so the packaged app would have written its database to the *current working directory*. That works when launched from the project folder and fails the moment it is installed, which is the definition of a bug that ships. Caught by reading the reader instead of trusting the writer.
45. **The display refusal and the display feature disagreed** — the module docstring promised a `CDS_TEST`-guarded write with automatic revert; the code raised `BackendError` unconditionally. A comment describing a safety feature that does not exist is worse than no comment, because it is the thing the next person trusts. The guard now exists (`display.py`, 15 tests) and the switch that enables it is explicit.

46. **Every gradient ran backwards** — `linear()` computed `dy = -cos(rad)`,
    which makes 0° point *down*, while its own docstring (and CSS) says 0° points
    up. Every theme rendered upside down and nobody could tell, because a
    gradient has no absolute direction to compare against. Found by writing a
    test that asserted the docstring instead of the output.
47. **`WallpaperSpec.dimensions()` silently fell back to 1440p** for any size it
    did not recognise — so `width=240` produced a 1.1 MB full-resolution render,
    and the 240 and 480 previews were byte-identical. A fallback that hides a
    wrong argument is worse than an exception that names it.
48. **`themes.validate` rejected sizes that `compose` accepted** — the validator
    knew six named sizes, the renderer also took `WxH`, so the engine refused
    themes it could render perfectly. Two sources of truth, one feature.
49. **Themes invented their own shell key names.** They used `taskbar_align`
    while `shell_lab` uses `taskbar.align`. Every theme applied cleanly, reported
    success, and changed nothing on disk — the precise failure mode this project
    exists to prevent, committed by the person who wrote the rule. `KNOWN_SHELL`
    is now derived from `shell_lab` instead of hand-written, and two tests assert
    every theme's key *and value* exist.
50. **`apply_customization` was called on the Service, not the Engine** — an
    `AttributeError` swallowed into a note, so a theme's shell section silently
    did nothing while the theme itself saved. The fix was not to add the method
    but to route through `shell_apply`, which plans first, journals, and reports
    refusals by name.
51. **Negative base, fractional exponent** — `(-0.3) ** 0.5` returns a *complex
    number* in Python, not an exception, so the `TypeError` surfaced frames away
    from the cause in five different places. Every one is now `max(0.0, x)`.
52. **Degenerate splits at extreme aspect ratios** — a horizon at `int(h*0.58)`
    is 0 when `h == 1`, which built a zero-height sub-canvas and called
    `randrange(0)`. Found by a test that renders every scene at 1×1, 320×60 and
    60×320 because 3840×1080 and 1080×1920 are real monitors.
53. **A gauge bar with value 0 was invisible** — no track, no fill, nothing. It
    was indistinguishable from a render failure. The track is now drawn before
    the fill, always.
54. **The disk bar's label and detail overlapped** at every size the defaults
    use, and so did the calendar's day number and its header — because both were
    positioned with hardcoded offsets instead of measuring the block first.

55. **`migrate()` promised a backup and never made one** — the docstring said
    "backs up the file first" for many versions while the code went straight to
    `executescript`, which commits implicitly. A migration that failed halfway
    left no rollback and no copy. There is now a real backup via SQLite's own
    `conn.backup()`, taken only when the database already has a schema, so a
    fresh install does not litter `.bak` files.
56. **The service was built outside its lock** — two request threads arriving
    together both saw `_SVC is None` and both ran the full migration path against
    one SQLite file. The per-request lock does not cover this: it is taken after
    construction. Now a double-checked lock around initialisation.
57. **`PRAGMA user_version={version}` was string-interpolated.** `version` comes
    from a module constant, so it was never exploitable — but it is the shape of
    an injection, and the next person to make versions dynamic would not notice.
    Forced through `int()`.
58. **85 API calls, zero global error handlers** — `api()` throws on any non-2xx,
    and roughly twenty call sites were outside a `try`. A failed call left the
    screen on stale data with no message; the user's only clue was that the
    button did nothing, which is the worst possible outcome for an app whose
    pitch is honest reporting. Unhandled rejections and script errors now route
    into a toast that shows the server's own reason.
59. **One table held two incompatible document shapes.** `desktop_themes` stores
    both the Studio's feature maps and the gallery's full themes. Read through
    the Studio's parser, a theme document has no `features` key, so
    `cost_of()` returned 0.0 — every theme in the gallery would have reported
    **costing nothing**, including the most expensive ones. The Studio's list is
    now filtered by shape.
60. **`DEFAULT_HIVE = "hcru"`** — a typo'd hive name in `shell_lab`, marked
    "placeholder replaced below", read by nothing. Deleted rather than left as a
    trap: the next caller would have used it and hit a `KeyError` on a real
    machine.

61. **Every parameterised GET route returned 500** — `/api/themes/get?theme_id=keel`
    was the visible one, and it had been failing for the entire life of the
    gallery screen. `do_GET` called `run_action(route)` with no arguments, so the
    handler raised `KeyError` on a `theme_id` the console had sent as a query
    string. The console asked, the server knew the answer, and the wire between
    them dropped the argument. Found in the server's own access log, not by a
    test: the route existed, the contract test passed, and the feature was dead.
62. **Then my fix broke every GET** — the new code read a `parsed` variable that
    only existed in `do_POST`, so `NameError` turned 21 passing tests red. Exactly
    what the suite is for. There is now a test that fetches every parameterless
    GET route and asserts 200, and another that asserts query parameters reach
    the action.

63. **State changes were reachable by GET.** Twenty verbs — `theme.apply`,
    `drivers.block`, `workspace.capture`, `safety.confirm` — answered to a GET.
    Any page on the internet can point an `<img>` at `localhost:7788` with no
    CORS preflight and no cookie, and this app's whole purpose is changing system
    state on request. Those verbs now answer 405 over GET and name the POST.

64. **A missing parameter was a 500 traceback** on every GET route that needs one.
    `do_GET` had no `KeyError` handler; `do_POST` always did. It now answers 400
    with `missing query parameter 'theme_id'`, which is a thing a user can act on.

65. **A result card sat 1800 px below the button that filled it.** The gallery's
    four ghost buttons all wrote their answer into `#themeDiffCard`, which was
    placed *after* the fifteen-tile grid. Every click worked, the server answered
    every time, and the user saw nothing happen — because the answer was four
    screens below the fold. This is the most expensive kind of UI bug: a working
    feature that is indistinguishable from a dead one. The card now sits directly
    under the button row.
66. **Two functions named `toast`.** The second silently replaced the first. One
    appended into `#toast`; the other created `#toastBar`. Both containers
    existed, both CSS blocks existed, and `#toast` was itself
    `position:fixed` — so every toast pinned itself to the same corner on top of
    the last one. The app's only failure channel was a pile of overlapping
    rectangles. One implementation, one container, one rule.
67. **Five gallery handlers could fail in total silence** — `if (!selectedTheme)
    return;`, an uncaught `themeDoc()` throw, and `api()`'s throw escaping before
    the card was written. All five now go through one wrapper that disables the
    button, shows a verb ("Rendering…"), catches the failure into the card, and
    says "pick a theme first" instead of doing nothing.
68. **The gallery cleared itself before the network answered.** `grid
    .replaceChildren()` ran first, so any failure left a blank screen with no
    message. It now builds the new grid first and swaps at the end, and on
    failure says which call failed and that nothing was changed.

69. **`build.ps1` died with a bare "exit code 1".** `& cmd /c "$python -m
    PyInstaller --version" *> $null` runs under
    `$ErrorActionPreference = 'Stop'`. On PowerShell 7.4+ any stderr byte from
    that probe becomes a *terminating* error, so the `$LASTEXITCODE` check on the
    next line never runs and the script exits with no message. That is why CI
    reported a build failure the log could not explain. The probe now runs with
    `Continue`, captures the status, and restores the preference.
70. **The venv fallback quoted a path into a quoted command.** `$python =
    "`"$Root\.venv-build\Scripts\python.exe`""` then `cmd /c "$python -m pip …"`
    produces nested quotes and a malformed command line — failing at exactly the
    moment the fallback was needed. It calls the executable directly now.
71. **Every `Service()` wrote 68 MB of fake junk to disk.** The demo machine
    exists so the Cleaner page has something honest to show, and the files must
    report real sizes because the Cleaner reads `st_size`. But they were written
    with `b"\0" * size`, so a 550-test run left **6.5 GB** in `/tmp` — and then
    *unrelated* tests began failing with `OSError: [Errno 28] No space left on
    device`. A red suite caused by the previous green one. The blobs are now
    sparse (`truncate()`, zero blocks, identical reported size, still reads as
    zeros), which cut a full run from 3.3 GB to 496 MB.
72. **Nothing ever cleaned the pytest temp dirs.** Three runs, 6.9 GB, and no
    mechanism to reclaim any of it. The suite now trims itself to the last three
    runs at session end.

73. **The shipped exe could not start.** `app.py` is PyInstaller's *entry
    script*, so it runs as a top-level module named `__main__` with no parent
    package, and `from . import server` raised
    `ImportError: attempted relative import with no known parent package` before
    the window existed. `python -m pco_engine.app` supplies the package, which is
    why the source tree never showed it — only the frozen binary is script-mode.
    Both imports are absolute now.
74. **The smoke test could not have caught 73.** It ran `Keel.exe --no-window`,
    an early branch that never reaches the import. It now runs `--version`
    through `main()`, the path a double-click takes, and fails on a non-zero exit
    code. Then a second correction: requiring *output* was wrong, because
    `console=False` means a windowed exe has no stdout. The exit code is the
    signal.
75. **A frozen crash wrote nowhere.** With `console=False` the traceback existed
    only in a dialog that loses its details on Close. Crashes now append to
    `<data>\crash.log` before the process dies.
76. **`data_dir()` ignored `PCO_DATA`** while `main()` published its choice into
    that same variable with `setdefault`. Two sources of truth for one directory:
    an explicitly set `PCO_DATA` was silently overridden, so the store and the
    crash log could land somewhere the user was not told about.

## The wallpaper engine

Pure stdlib: no Pillow, no numpy, no shader runtime — the exe is 12 MB and a
wallpaper engine that needs 40 MB of image library to draw a gradient is not a
wallpaper engine.

| File | What it is |
|---|---|
| `canvas.py` | RGBA raster: integer blending, linear/radial gradients with ordered dither, additive glows, arcs, a 5×7 bitmap face, PNG encoding with Sub filtering |
| `scenes.py` | 8 scenes — aurora, mesh, synth, topo, grain, loft, orbit, dusk. Deterministic from `(size, palette, seed)` |
| `widgets.py` | 16 widgets drawn into the image. `LIVE_ONLY` marks the ones whose value moves |
| `wallpaper.py` | Composition: scene + widget layer + scrim + vignette, with `WxH` sizes |
| `themes.py` | 15 complete looks in one JSON document, strict validation, `diff()` for preview, `export()`/`import_()` |

**Why widgets are baked into the image, not a live layer.** A repainting overlay
costs 1–3% GPU forever and needs a DWM hook, which is exactly what the governor
refuses. Drawing into the wallpaper costs nothing after the render, survives an
Explorer crash, and cannot be covered by a window. The trade-off is stated where
it bites: `LIVE_ONLY` widgets in a still image produce a warning that the clock
will be stale, rather than silently baking a stopped clock.

**Cost.** A 1440p render is ~7 s of pure Python, once. A 4K render is ~35 s. A
360 px gallery tile is 52 KB in 0.1 s, which is why the gallery renders tiles on
demand and caches them in the browser.

**Setting it for real.** `set_wallpaper()` calls
`SystemParametersInfoW(SPI_SETDESKWALLPAPER)`, reading the previous path from the
registry first so undo restores an exact value. This is materially safer than a
display mode change: a wrong image is a wrong image, not a black screen.

## The cost policy: nothing is forced

Earlier builds **refused** a theme above 3% idle GPU unless the caller passed
`force=True`. That was a product rule treated as the user's budget, and it taught
users to click "force" without reading — worse than either refusing or allowing.

The rule now, in `cost.py`:

| | |
|---|---|
| **Nothing is blocked** | `verify()` never returns a refusal. There is no gate to click through |
| **0% is always available** | the zero tier exists, is reachable, and is shown on the tile |
| **Cost before the choice** | every effect has an entry; an effect with no entry would silently cost 0, which is a lie in the flattering direction |
| **The cheaper option is named** | "too expensive" is not advice; "here is the free version" is |
| **Estimates say so** | `measured: False` on every verdict, because these are a table, not counters |

Tiers: `zero` (0%), `light` (≤0.5%), `balanced` (≤1.5%), `full` (more). A
generated wallpaper is `zero` — it is rendered once to a PNG and is a still image
after that, exactly like any other wallpaper. The live layer is the part that
costs, and it stays opt-in.

On battery the recommendation mentions the charge. It does not disable anything.

## Building Keel.exe

`.\packaging\build.ps1` produces `dist\Keel.exe` and the installer. It needs
Windows, Python 3.10+, and (for the installer) Inno Setup 6.

CI does the same thing on every push. If the `package` job fails, download the
**build-log** artifact from the run page: the script now prints the failing
command's output instead of exiting silently. It used to swallow the reason —
which is bug 69 above.

**If the installed app does not start**, the traceback is in
`%LOCALAPPDATA%\Keel\crash.log`. With `console=False` there is nowhere else for
it to go, and the error dialog loses its text when you close it.

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

- **The installer** — `packaging/` builds `Keel.exe` (PyInstaller, one file, no
  console) and `Keel-0.5.0-Setup.exe` (Inno Setup 6, per-user, no UAC). The CI
  `package` job produces both as downloadable artefacts. It cannot be built off
  Windows: PyInstaller bundles the interpreter it runs under.
- **The app shell** — `pco_engine/app.py` picks a free loopback port, migrates the
  store on a thread, waits for `/api/health`, then opens a window through pywebview
  (WebView2, already present on Windows 11) or the default browser. Single-instance
  via a named mutex, because two copies migrating one database is a corruption path.
- ~~`WindowsBackend`~~ — **now written** (`pco_engine/windows_backend.py`): registry,
  services, tasks, power plans and files, with an explicit address table instead of
  guessing. Its mapping logic is tested on every OS; the real calls are tested by
  CI on `windows-latest`. The display family exists and is watchdog-guarded, but is
  **off by default** (`PCO_WINDOWS_DISPLAY=1` to enable): a wrong mode can black the
  screen that would undo it. See `WINDOWS.md`.
- The DNS resolver itself (this repo has the decision engine; the resolver needs Windows NRPT).
- The Rust port and the WinUI 3 shell.
- Window enumeration now uses `tasklist` (processes with a main window) rather than
  the Win32 window list, so two windows of one exe collapse into one workspace entry.
  Right for "restore what I had open", wrong for counting windows.
- Per-process battery drain reads a `powercfg /energy` report **if one exists**; it
  never starts a 60-second trace on its own. Absent stays absent, never zero.
- Widgets, wallpaper renderer, theme application to the real shell (the governor and
  document format exist; the WinUI renderer needs Windows).
- Game auto-detect via process watcher (the library, classifier and plan are done;
  PresentMon wiring needs Windows).
- The installer watcher itself (deltas are recorded and erasable; the ETW/registry
  monitor needs Windows).

Each is specified; none of them needs the safety core to change.
