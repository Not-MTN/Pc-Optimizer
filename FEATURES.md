# Feature Specs — Balanced Scope

> Companion to `IDEAS.md` (vision) and `ARCHITECTURE.md` (how it's built). This doc is the *what* — deep enough to build from, honest about edge cases. No product code yet.

## Scope balance (your call: "keep balanced")

| Ring | Features | Rule |
|---|---|---|
| **Core** — built to excellence, ships v0.1–v1.0 | Guardian, Cleaner, Gaming session, Palette + Personal CMD, Startup surgeon, DNS blocker, Installer Autopsy, Customization profiles, Sandbox modes, Privacy/air-gap | If it's in Core, it's *excellent* or it's cut |
| **Extended** — planned, specced, ships v1.x | File manager companion, Health/longevity, Network lab, Skins/sounds, Drift repair UI, Community gallery | Build only after Core is solid |
| **Experimental** — labeled as such | Live wallpaper HUD, local copilot, kernel interrupt lab, WASM plugin SDK, deterministic PC rebuild | Never blocks a release |

**The rule that governs everything:** every feature must produce a **receipt** (what it did, measured, reversible) or it doesn't ship. "Optimized!" with no numbers is a bug.

---

## 1. Cleaner

### Model: classify by regeneration cost, not by size

| Class | Meaning | Examples | Default |
|---|---|---|---|
| `free` | Nothing needs it again; regenerates itself or never mattered | Temp, crash dumps, old installers, Delivery Optimization cache, thumbnail/icon caches | **on** |
| `auto-regen` | Will rebuild itself, may cost time on next launch | DX shader cache, font cache, Defender scan history, Store cache | on, with "first launch feels slower" note |
| `policy` | User must decide; usually a tradeoff | hiberfil.sys (resize, don't delete), pagefile, Windows.old (age-gated), WinSxS via DISM, dormant Store apps, OneDrive on-demand | **off**, explained |
| `never` | Protects the user from us | Documents, saves, browser profiles, dev caches (outside dev profile), System32, drivers, activation | not offered |

### Rule schema (shipped data, not code)

```toml
[[rule]]
id          = "dx.shader.cache"
title       = "DirectX shader cache"
paths       = ["%LOCALAPPDATA%\\D3DSCache"]
action      = "delete-children"          # delete-children | delete-tree | resize | dism-component
class       = "auto-regen"
risk        = "cosmetic"
regen_cost  = "games recompile shaders, first launch slower"
locks       = ["dwm.exe", "explorer.exe"] # may hold files open
fallback    = "schedule-reboot"           # MoveFileEx(PENDING) when locked
estimate    = "scan"                      # computed, never hardcoded
receipt     = ["bytes", "files", "elapsed", "locked-skipped"]
```

### Sources of truth for size estimates
Streaming directory walk (cancelable, resumable), memory-mapped sorting of results, and for system stores the **DISM component API** rather than guessing. Nothing is reported as "reclaimable" unless it was actually scanned.

### What the Cleaner refuses to do (documented in-product)
- ❌ Registry "cleaning" — no measurable benefit, real risk. Offered only as an opt-in hive-compaction expert tool (off by default).
- ❌ "RAM optimizers" that free standby memory — actively harmful on Windows.
- ❌ Deleting Prefetch — Windows 11 already manages it; deletion costs boot time.
- ❌ Pagefile deletion — breaks crash dumps and some apps; resizing is offered instead.
- ❌ Anything under user documents, saves, or browser profiles.
- ❌ "Speed up internet" registry tricks.

### Lock handling & undo
Deletes go to a **quarantine vault** on the same volume (`.pco-vault`, 30-day retention, size shown honestly in "space freed"). Undo = move back, restoring original timestamps. Locked files → scheduled delete at reboot, listed in the receipt. Never follows junctions/symlinks out of an approved root path.

### UI
Disk heatmap (treemap, streamed), per-rule toggles with plain-English consequences, **scan → confirm → clean → receipt** flow. The receipt is a shareable card: freed bytes, items, time, what was skipped and why.

---

## 2. Gaming session

### Lifecycle (a state machine, not a switch)

```
idle → arming (snapshot + pre-measure) → active (tweaks + live measure)
     → concluding (post-measure + revert) → report → idle
                  ↘ aborted (crash/game exit) → auto-revert → report
```

### What actually runs during a session
| Bucket | Actions | Tier |
|---|---|---|
| Power | Power plan, core parking, min/max processor state, PCIe ASPM, USB selective suspend | T1 |
| Background | Pause updaters/indexer/sync, suspend non-essential apps *(best-effort, allowlist of known-safe)*, disable notifications (Focus API), pause Windows Update | T1 |
| GPU | Per-game GPU preference, HAGS *flag only* (needs reboot → never "live"), overlay/RGB killers | T1 |
| Network | Delivery Optimization throttle, per-app QoS (DSCP), Nagle off for game process | T1/T2 |
| Deep | Interrupt affinity, DPC tuning, per-process WFP | T2, auto-suspended for anti-cheat titles |

### Measurement (the honest part)
- **PresentMon-style ETW**: frametimes, FPS, 1% lows, frame-time σ, GPU busy, present mode.
- **DPC/ISR ETW**: latency spikes attributed to drivers.
- **CPU/GPU power & temps** where available without a driver (PDH/WMI); T2 unlocks MSR-based precision.
- **Input latency**: user-initiated 60-second test (photodiode-free, uses raw input timestamps) — optional.
- **Rules of honesty**: report effect size *with variance*; if the delta is inside noise, say **"no measurable change"** in large type; never average away a regression; always show the raw distribution, not just the mean. Sample size and duration displayed.

### Per-game profiles
Auto-detect from Steam/SX/Epic/GOG libraries and exe hash; profile = overrides on top of the global session profile. Importable as YAML. Example:

```yaml
match: { exe: "RocketLeague.exe", hash: "sha256:…" }
overrides:
  power.core_parking: "disabled"
  network.nagle: off
  gpu.preference: "high"
  suspend: ["OneDrive.exe", "Spotify.exe"]
disabled_for_anticheat: ["t2.*"]
```

### Anti-cheat matrix (shipped, updated like an antivirus definition)
| Ring | Vanguard | EAC | BattlEye | Faceit | Notes |
|---|---|---|---|---|---|
| T0/T1 tweaks | ✅ | ✅ | ✅ | ✅ | Service-level only, no injection |
| T2 driver | ⛔ suspended | ⛔ | ⛔ | ⛔ | Auto-unloads on process detect; UI badge explains |

### Failure modes → behavior
| Failure | Behavior |
|---|---|
| Game crashes | Session concludes, revert, report kept |
| PC crashes / power loss | Boot guard reverts session tweaks automatically |
| User alt-tabs and forgets | Auto-conclude after 10 min idle, notify |
| Launcher → game tree | Session binds to process tree, survives launcher exit |
| Multiple games | One session at a time; second game prompts |

---

## 3. DNS ad + tracker blocker

### Pipeline

```
app → NRPT/WFP routing (per-app policy) → local resolver (127.0.0.1:53)
    → allowlist/denylist trie (wildcards + CNAME chain check)
    → DoH upstream (ranked by measured latency) → cache → answer
```

### Lists
Compiled at **build time** to a sorted array + bloom filter + wildcard table (imports in ms, ~0 runtime cost). Groups: ads, trackers, Windows telemetry, malware/badware, phishing, optional adult/regional. User-imported lists compiled at import with a progress pane; dedupe across lists.

### Per-app policies
- Default: all apps filtered.
- App sets: browsers strict; games permissive; apps with known login flows auto-allowlisted.
- **Breakage recovery (the thing nobody does well):** when a page or app fails and blocked domains were involved in the last N seconds, offer a one-click **"allow this domain for this app"** + a 60-second "pause blocking for this app" — never a silent broken login.

### Transparency catalog
Every blocked endpoint has a plain-English entry: what it is, who owns it, what breaks if blocked (e.g., "blocks Windows Update telemetry — safe; blocks license validation — apps may call home on next launch"). Bucket toggles: **Minimal / Balanced / Strict**, default Balanced.

### Failure modes
| Failure | Behavior |
|---|---|
| Resolver unavailable | Fail-open to system DNS + toast (never leave user offline) |
| VPN active | Detect, warn about DNS leak, offer split rules |
| Captive portal | Auto-detect, bypass until auth completes |
| mDNS/.local | Never filtered |
| DoH provider down | Ranked failover list, measured, local cache |

### Extras
DNS latency ranking (real browsing speed gain, measurable), DNS cache warm-up, **CNAME-cloaking detection** via our own resolution chain, per-domain live counters, and an export of "who your PC talks to" as a CSV.

---

## 4. Startup surgeon

### Enumeration sources
Run/RunOnce (HKLM/HKCU, WOW64), StartupApproved state, services, scheduled tasks (all triggers), shell extensions (context menu, icon overlays), Winlogon entries, UWP startup tasks, drivers with start=auto, known vendor updaters.

### Boot-phase attribution
ETW phases: kernel init → services (group by service group) → logon → shell init → first idle. Each startup item is attributed to a phase and a duration contribution where measurable; the rest is labeled "unattributed" rather than invented.

### Knowledge base (offline, versioned, shipped)
```yaml
- key: "service:DiagTrack"
  does: "Windows diagnostics + telemetry"
  disable_ok: false
  breaks: ["Feedback Hub", "some enterprise diagnostics"]
  recommended: "leave on; use Privacy screen to limit data instead"
- key: "task:\\Microsoft\\Office\\OfficeBackgroundTaskHandler"
  does: "Office updater housekeeping"
  disable_ok: true
  breaks: ["nothing user-visible"]
  recommended: "defer 60s rather than disable"
```
Keyed by file hash + authenticode signer when paths match flaky vendors. Unmatched items get an honest "unknown — here's what it is, decide yourself" card with signer, path, network activity.

### Actions
Disable (type-correct API), **defer N seconds** (usually better), run-once-then-disable, and "kill and watch." Dependency graph: refuses to disable a service with running dependents; explains which app depends on it.

### Watchdog
Detects re-enable events (updates silently restore startup entries — very common), reports who did it and when, offers re-apply. Logs every drift event to the journal so the time machine can show it.

---

## 5. Installer Autopsy (the killer feature)

### Session capture
Start when a watched process tree starts; end when the tree exits + 15 s quiet or a hard timeout.
| Source | Method | Fidelity |
|---|---|---|
| Files | USN journal read (default, no driver) | high for creates/deletes/moves |
| Files (deep) | Read-only minifilter (T2, optional) | exact writes |
| Registry | ETW `Kernel-Registry` (create/set/delete + key path) | high |
| Services / tasks / drivers | Change notifications + before/after enumeration diff | exact |
| Network | ETW `Kernel-Network` + DNS client events → hostnames | per-endpoint |
| Processes | ETW `Kernel-Process` | full tree |

### Report model
Grouped by category with sizes, **risk flags** (autostart, service, kernel driver, scheduled task, beaconing), a plain-English summary, and a **leftover detector**: run the vendor's uninstaller, re-scan, show what it forgot.

### Erase
Reverse creation order: stop/remove services and drivers first (reboot-safe mode when a driver is loaded), delete created tasks, remove created registry keys (restore *modified* values from the captured pre-state), delete files, verify, report. Anything it can't remove is listed with the exact path — never "cleaned 100%" when it wasn't.

### Honest limitations
- Kernel-mode writers can hide from user-mode observation (T2 minifilter closes most; not all).
- MSIX/UWP installs go through the deployment stack — separate dedicated path.
- Detached, self-elevating child processes are caught by PID-tree + time window + parent-set heuristics; misses are reported as "possible blind spots."

---

## 6. Command palette & Personal CMD

### Action model
```yaml
id: game.start
title: Start gaming session
keywords: [game, gaming, session, fps]
icon: gamepad
tier: T1
target: engine.session.start
args: { profile: "auto", measure: true }
risk: none
confirm: false
undo: on-session-end
```

### Execution model
- Built-ins run **inside the engine** — no shell spawn, no console flash, ~4 ms.
- User commands choose their executor explicitly: `engine` (built-ins), `shell` (cmd/pwsh/bash with declared shell), `app` (launch), `web`, `script` (a file with hash recorded).
- Sandboxed execution: explicit cwd, env allowlist, timeout, captured stdout into a results pane, and a permission badge per command (tier + what it may touch). First run of any imported command shows a **diff of its permissions**.

### DSL (small on purpose)
```
:clean                 # alias to built-in
:clean --dry           # flags
:snap name="before update"
:run "C:\tools\sync.cmd" --timeout 120 --as user
:game --profile rocket-league
```
No variables/functions in v1; v2 adds `{selection}`, `{clipboard}`, and pipes. Deliberately not a general-purpose language.

### Out-of-the-box magic
- **Macro recorder**: record a sequence of palette actions and replay as a command (`:morning`).
- **Alias suggestions**: after doing the same 3 actions together twice, offer to record it.
- **Preview pane**: what a command will do before Enter.

### Performance contract
Open ≤ 50 ms (resident helper), first results ≤ 16 ms, input latency ≤ 1 frame, zero allocations in the typing path after warm-up.

---

## 7. Customization profiles (taskbar, CMD, Explorer, the whole vibe)

### Profile schema (one file, git-friendly)
```yaml
meta: { name: "MTN dev rig", version: 3 }
appearance: { base: dark, accent: "#4fd1c5", wallpaper: "file:wall/aurora.png", transparency: on }
taskbar: { align: center, size: compact, grouping: never, labels: off, clock: "{HH:mm} {date}", seconds: true }
explorer: { tabs: on, compact: on, classic_menu: on, sync_provider: off }
terminal: { profile: "pwsh", font: "Cascadia Code 11", opacity: 92, aliases: { ll: "eza -la", gs: "git status" } }
shortcuts: { "capslock": "esc", "win+shift+s": "snip" }
sounds: { scheme: "studio-quiet", toggles: on, session_chime: on }
```

### Per-setting apply / verify / revert
Every setting is an individual tweak (Guardian rules apply). Some things Windows simply doesn't expose → handled by an optional **mod pack** (Windhawk-detected; we ship configs, not injections, at first). Honest support labels in the UI:

| Area | Support |
|---|---|
| Accent, wallpapers, sounds, pointer, terminal, aliases, shortcuts | **native, perfect** |
| Taskbar align/size/grouping/labels/clock | **native** (Win11 26H2) |
| Explorer behaviors | **mostly native** |
| Start menu layout, Explorer visuals beyond settings | **mod pack**, labeled risky, auto-disabled after crash |

### Fresh-install bootstrap (the wow flow)
`winget export/import` + profile apply + manual account re-login checklist → a wiped PC becomes *yours* in ~15 minutes. Profile stored in the user's own git repo; the app can commit changes per session.

### Community
Profiles are static files → a gallery can be a static site (publish via Vercel). Import shows exactly what will change before applying.

---

## 8. Sandbox modes (the decision tree users never get)

| User intent | Solution | Tier |
|---|---|---|
| "What did this installer do?" | **Autopsy** — observe, don't contain | T0/T1 |
| "Run this sketchy exe safely" | Windows Sandbox (Pro+) / AppContainer + low integrity (Home) | T0/T1 |
| "Nothing should touch my system" | Virtual profile (files/registry redirect) | T1 |
| "No network for this app" | Per-app WFP rule (T2) or firewall rule by path | T1/T2 |
| "Throwaway app with clean uninstall" | Virtual profile + Autopsy | T1 |

All of these are **one right-click away** in Explorer and in the palette. Each returns a receipt.

---

## 9. File manager companion (Extended)

Not an Explorer clone. Scope: tabs and dual-pane, **Miller columns**, fast search from our own USN-based index, bulk rename with live preview, hashing, duplicate finder (size → partial hash → full hash), treemap folder sizes, rule engine ("downloads > 30 days → archive"), tags stored in SQLite (not ADS, to stay portable). Performance targets: 10k-entry folder paint < 100 ms; 1M-file initial index < 60 s; incremental updates via USN.

## 10. Health, network lab, privacy (Extended, concise)

- **Health**: SMART/NVMe wear, temps, fan, battery cycles/degradation, thermal history, driver/BIOS drift, crash correlation with the tweak journal.
- **Network lab**: bufferbloat test, resolver ranking, per-app throughput ledger, "game on this network" (measured toggles, revert on exit), router-side advice where software ends.
- **Privacy**: endpoint catalog, per-app network ledger, air-gap toggle with provable WFP rules, privacy self-test that reads the real rules.

---

## Cross-cutting rules

1. **Receipts**: every mutation produces a journal entry + a human receipt. If an action can't be measured, say so.
2. **Drift repair**: Windows Update/Store/vendors silently revert tweaks — weekly hash check of managed state, show what changed, offer re-apply. This is the #1 complaint about every optimizer.
3. **Blame engine**: correlate crashes/hangs/boot regressions with recent tweaks and offer targeted revert.
4. **Honest claims policy**: no percentage claims without a measurement; "no change" is a legitimate headline result; cosmetic features are labeled cosmetic.
5. **Two moods, one app**: Casual (mascot-forward, plain language, big friendly controls) and Pro (dense, numeric, monospace, keyboard-first). Same engine, same guardrails — only the surface changes.
