# Feature Specs — Balanced Scope

> Companion to `IDEAS.md` (vision), `ARCHITECTURE.md` (how it's built), `SAFETY.md` (the undo system), `DESKTOP.md` (customization suite). This doc is the *what*.

## Scope balance

| Ring | Features | Rule |
|---|---|---|
| **Core** — built to excellence | Guardian, **PC Checkup**, **Gaming session + Gaming Coach**, Palette + Personal CMD, Startup surgeon, DNS blocker, Installer Autopsy, **Customization suite** (see `DESKTOP.md`), Sandbox modes, Privacy/air-gap | If it's in Core it's *excellent* or it's cut |
| **Extended** — specced, ships v1.x | File manager companion, Health/longevity, Network lab, community gallery, drift repair UI | After Core is solid |
| **Experimental** — labeled | Live desktop HUD, local copilot, kernel interrupt lab, plugin SDK | Never blocks a release |

**Governing rule:** every feature produces a **receipt** (what it did, measured, reversible) or it doesn't ship.

---

## 1. PC Checkup (the home screen)

One scan, one list, one button. The checkup is what the app opens to.

### The honesty split (this is the core idea)

Most optimizers mix "things taking up space" with "things slowing you down" to inflate the scary number. We separate them into **three tabs**:

| Tab | Means | Examples |
|---|---|---|
| **Slowing you down** | Measurably or mechanically affects speed/responsiveness | 60 Hz on a 165 Hz monitor, 21 startup items, thermals throttling, SSD 94% full, driver crashes |
| **Taking up space** | Costs disk, not speed | 42 GB junk, duplicates, stale installers, old Windows backup |
| **Needs attention** | Risk, not speed | BitLocker off, 12 outdated apps (3 with known exploits), 4 background mic grants |

A finding never appears in a tab it doesn't belong in. Nobody is told that deleting temp files will make games faster, because it won't.

### Finding model (every finding is explainable)

```yaml
id: display.refresh.below_max
category: performance
severity: high
title: "Monitor running at 60 Hz — it supports 165 Hz"
plain: "Your screen can show 165 pictures a second but is only showing 60. This is the single biggest free win on your PC."
evidence: { current: "59.94 Hz", max: "165 Hz", panel: "VG27A", cable: "DP 1.4" }
impact: "Up to 2.7× smoother motion; games feel far more responsive"
fix: { kind: apply-tweak, tweak: "display.set_refresh", args: { hz: 165 }, risk: low, reversible: true }
auto_safe: true          # safe enough for "Fix everything"
```

Click any finding → **why this matters**, what the evidence is, what the fix does, and what changes if you accept.

### What gets checked

**Performance / "slowing you down"**
- Display refresh rate below panel maximum, wrong resolution, HDR/VRR off while available
- Startup items (count, boot-time contribution, what each is), services, scheduled tasks
- Background apps holding CPU/disk/RAM; apps that run at login and never close
- Thermal throttling history (did the CPU/GPU drop clocks under load?), fan curve sanity
- GPU driver age, driver crash history, DPC/ISR latency spikes
- Disk fullness > 85/90% (real slowdowns on SSDs), disk health warnings, failing SMART attributes
- RAM pressure / pagefile thrash, memory compression churn
- Power plan set to "power saver" on a desktop, USB power suspend issues
- Network: bufferbloat grade, DNS resolver latency ranking, an app saturating the uplink
- Windows Update paused for months, pending reboot flags

**Storage / "taking up space"**
- Junk (temp, crash dumps, old installers, WinUpdate leftovers, Delivery Optimization)
- **Duplicates** (see below)
- Stale large files (untouched 1 year+, > 500 MB, previewed before suggesting)
- Empty folders, broken shortcuts, orphaned uninstall entries
- Windows.old, hibernation file, restore-point creep, WinSxS (via DISM, age-gated)
- "What's eating this folder" treemap

**Security / posture** (a *posture check*, not an antivirus — stated plainly in the UI)
- Defender state, real-time protection, **suspicious exclusion paths**
- Firewall per-profile state, inbound rules open to everything
- BitLocker/device encryption off on non-system drives, removable-drive encryption
- Pending updates, updates paused by "tweak" tricks, Windows version end-of-life
- Outdated apps vs a **shipped, updateable vulnerability feed** (locally compared — known-exploited first)
- Autostart items with no/odd signatures, tasks running from `%TEMP%`, services with unusual paths
- RDP / Remote Registry / Remote Assistance exposed, SMBv1 enabled, autorun on USB
- UAC lowered, passwordless accounts, auto-login enabled, extra admin accounts
- Hosts-file and DNS tampering, proxy hijacks
- Browser extensions with dangerous permissions (local read of extension manifests)
- Wide-open shares, guest access, saved open Wi-Fi profiles

**Privacy**
- Telemetry level, ad ID, activity history, tailored experiences, app permissions (mic/camera/location) incl. background grants, sync settings, diagnostics upload

**Reliability**
- Crash/BSOD history with blame attribution, boot-time trend, service failure history, driver rollback candidates, backup/restore-point freshness

### Scoring (explainable, never magic)

```
PC Score = 100 − Σ(finding penalties × severity weight)
Sub-scores: Speed · Security · Privacy · Reliability
```
Clicking a score shows the exact findings and weights that produced it. The score is *a summary of findings*, not a benchmark, and the UI says so. Scores never go up because we did something cosmetic.

### One-button fix

**"Fix everything safe (14)"** →
1. Shows a **plan**: 14 fixes, grouped, with combined expected effect and reboot requirements.
2. Runs as **one batch transaction** → streamed progress, cancellable.
3. Ends with **one receipt and one undo** for the whole batch.
4. Anything needing judgment (open firewall port, deleting large files, driver changes) is **not** included — it goes to **Review**, one item at a time, with the evidence in front of you.

Safety rules: never batch two risky changes together; never fix something we can't revert; if a fix requires a reboot, it's queued and clearly marked, never hidden.

### Duplicates (inside the checkup)

- **Pipeline:** group by size → hash first/last 64 KB → full hash (BLAKE3) → optional **perceptual hash** for near-identical photos. Resumable, cancelable, ~1M files in minutes.
- **Correctness:** hardlinks and symlinks/junctions are understood and never counted as duplicates or followed outside the chosen roots. System folders are excluded by default.
- **Keep rules:** keep newest / keep oldest / keep shortest path / keep in a chosen folder / ask per group.
- **Preview:** side-by-side with metadata, image thumbnails, and "this file is a game save / document" warnings.
- **Delete:** moves to the **vault** (30 days), verified per file, with a receipt. Nothing is deleted from the vault automatically without your retention setting.
- **Why it matters:** shown honestly — "frees 18.4 GB; no speed impact".

### Weekly health report + drift

A short weekly summary (local, exportable): what changed, what drifted (Windows Update silently reverted a tweak), what got worse. Replaces the "run an optimizer every day" habit.

---

## 2. Gaming — session + coach

### Lifecycle (state machine)

```
idle → arming (snapshot + pre-measure) → active (tweaks + live coach)
     → concluding (post-measure + revert) → report → idle
              ↘ aborted (crash/game exit) → auto-revert → report
```

### What runs during a session

| Bucket | Actions | Tier |
|---|---|---|
| Power | Power plan, core parking, PCIe ASPM, USB selective suspend | T1 |
| Background | Pause updaters/indexer/sync, Focus assist, suspend known-heavy apps (allowlist, never blind-kill) | T1 |
| Display | Confirm max refresh, HDR/VRR state, resolution, scaling; warn on wrong cable mode | T1 |
| GPU | Per-game GPU preference, overlay/RGB killers, HAGS flag (reboot-gated, never "live") | T1 |
| Network | Delivery Optimization throttle, per-game QoS/DSCP, Nagle off for the game process | T1/T2 |
| Deep | Interrupt affinity, DPC tuning, per-process WFP | T2, **auto-suspended** for anti-cheat titles |

### The Gaming Coach (watch gameplay → advise → verify)

**Capture while you play** (ETW, no injection):

| Signal | Source | Used for |
|---|---|---|
| Frame times, FPS, 1% lows, present mode | PresentMon-style ETW | stutter + pacing analysis |
| GPU load/clocks/temp/VRAM | PDH / vendor APIs (MSR precision at T2) | bottleneck classification |
| CPU per-core load, effective clocks | ETW | single-thread vs multi-thread bound |
| RAM, page file, disk queue | PDH | streaming/paging stutter |
| DPC/ISR spikes | ETW | driver-caused stutter |
| Power/thermal limits | PDH/WMI | throttling |
| Display state | display config API | Hz/HDR/VRR/color depth |
| Network | per-process sockets | online-game ping/jitter/loss |
| Input | raw input timestamps | input latency test (opt-in) |

**Bottleneck classifier** — a rules table, not a vibe:

| Verdict | Evidence pattern | Advice direction |
|---|---|---|
| GPU-bound | GPU ≥ 97% most frames, CPU < 70% | lower GPU-cost settings (shadows, AA, post-FX, resolution) |
| CPU single-thread bound | 1 core ≥ 95%, GPU < 85% | draw distance, crowd/physics, background CPU |
| VRAM-limited | VRAM ≥ 95% + stutter spikes | textures ↓, resolution ↓, RT off |
| Thermal/power limited | clocks fall during session, temps high | fan curve, undervolt advice, laptop power |
| Background interference | DPC spikes correlate with a process/disk | kill/defer that workload |
| Frame-cap / display issue | inconsistent pacing, Hz mismatch, VRR off | fix cap/VRR/Hz |
| Streaming stutter | disk queue spikes on asset load | move game to SSD, defrag HDD, shader warm-up |
| Network-limited | ping/jitter/loss spikes at same moments as stutter | QoS, bufferbloat, Wi-Fi |

**Recommendations — two kinds, always separated:**

1. **Windows-side** (we can apply it): refresh rate, VRR, GPU preference, power, QoS. Auto-applicable during a session.
2. **In-game** (we guide, or edit config files): presented as `current → suggested` with **expected gain, confidence, and the basis** (your own measurements, or our curated library). Never a fake percentage.

Example:
```
Resolution scale 100% → 90%     +9% fps   confidence: high     basis: 41 sessions, GPU-bound 87%
Shadows High → Medium           +6%       confidence: medium
Textures  High → keep           ±0%       you have 4 GB VRAM headroom — don't touch
Anti-aliasing TAA → keep        ±0%       CPU-bound on your 6-core; MSAA would cost more
```

**Settings capture (screenshot OCR)** — open the game's settings menu, press the hotkey, and the app reads the labels and values from the screen **locally** (Windows OCR) to build your current profile. You never type settings in by hand.

**A/B benchmark** — record a repeatable 60–120 s scene twice with different configs; the app compares distributions (n, variance, 1% lows) and tells you which actually won. If the difference is inside noise: **"no measurable difference"**, in large type.

**Sensitivity per game** — see the Sens Lab below; the coach and the lab share one profile.
**Overlay HUD** — external, top-most window (never injected): FPS, frametime graph, temps, VRAM, and a coach whisper when a pattern is detected ("stutter cluster — 3 spikes/20 s, GPU clock dropped to 1.2 GHz"). Costs < 0.5% CPU; auto-off when the game is exclusive-fullscreen and the driver refuses composition.

**Session history** — per-game timeline: "1% lows improved 14% since the power-plan change", "6% slower in this game since driver 560.94". Regression detection is the point.

**Extras** — game library readiness (space, drivers, pending downloads), move games between drives, shader warm-up, mouse polling/controller check, streaming assist (encoder + bitrate vs upload), VR/emulator profiles.

### Sensitivity Lab (full spec in `SENSITIVITY.md`)

Find *your* sensitivity with a real drill, not a pro's number, then convert it everywhere.

- **Why it can be exact:** every FPS rotates a fixed **yaw** (degrees per mouse count at sens 1). `cm/360 = (360×2.54)/(DPI×sens×yaw)` is the only number that means the same thing in every game, so it transfers exactly. Verified yaw table ships as data (CS2/Apex 0.022, Valorant 0.07, OW2/CoD 0.0066, Fortnite 0.005555, plus Source/Quake family), with community values flagged and non-linear games routed to capture.
- **The test:** 3 log-spaced points ±18%, 6 acquisition shots each, then an optional refine pass. Measures acquisition time, normalized miss error, **overshoot rate**, **undershoot rate**, correction count and tracking RMS. Fits the U-curve and returns the vertex with a confidence interval. If the points are inside the noise it says **"don't change anything."**
- **360° capture (universal coverage):** turn once in *any* game with the hotkey held; raw input counts + DPI give cm/360 exactly, and deriving the yaw lets us support games nobody has ever converted.
- **Watch mode (opt-in, raw input only):** overshoot corrections, pad usage, sweep limits and reposition frequency → per-genre adjustments. Never injection, never game memory, per-game allow list, ≥3 sessions before it becomes advice.
- **Per game:** ADS/scoped multipliers, FOV matching modes, controller mode (deadzone/curve/ADS multiplier with the same U-curve logic), DPI and polling guidance, acceleration checks.
- **Applying:** config write with backup, OCR of sliders, or the exact number for built-in converters. Every applied change is a journaled Guardian tweak.

### Anti-cheat matrix

| Ring | Vanguard | EAC | BattlEye | Faceit |
|---|---|---|---|---|
| T0/T1 tweaks | ✅ | ✅ | ✅ | ✅ |
| T2 driver | ⛔ suspended (auto) | ⛔ | ⛔ | ⛔ |

### Never

- Never promise FPS; report measured distributions.
- Never inject into game processes.
- Never apply in-game config edits without a backup and one-click restore.
- Never "optimize" by disabling security features.

---

## 3. DNS ad + tracker blocker

### The master switch (your ask)

| Control | Behavior |
|---|---|
| **On / Off** | One toggle in the tray, the dashboard, and the palette — instant, no reboot |
| **Pause** | 5 min / 30 min / 1 hour / until tomorrow, with a visible countdown badge |
| **Per-app** | Filter this app: on / off / strict |
| **Levels** | Minimal / Balanced / Strict (group toggles underneath) |
| **Hotkey** | Global toggle, so it's one keystroke mid-game |
| **State is always visible** | Tray icon changes (shield solid / ringed / gray), palette shows status, blocking counters freeze when off |

Turning it **off** restores plain system DNS immediately (no resolver swap left behind). Turning it **on** re-arms the local resolver in < 200 ms.

### Pipeline

```
app → NRPT/WFP routing (per-app) → local resolver 127.0.0.1:53
    → trie/bloom matcher → CNAME-chain check → DoH upstream (ranked) → cache → answer
```

### Lists
Compiled at build time into a sorted table + wildcard set (runtime cost ~0). Groups: ads, trackers, Windows telemetry, malware/badware, phishing, regional/adult (opt-in). User imports compile on import with progress and dedupe.

### Breakage recovery (what nobody does well)
If a page/app fails and blocked domains appeared in the last N seconds → offer **allow this domain for this app** or **pause for this app (60 s)**. Never a silent broken login.

### Transparency catalog
Every blocked endpoint has an owner, a purpose, and a plain "what breaks if blocked". Bucketed toggles: Minimal / Balanced / Strict, default **Balanced**.

### Failure modes
Resolver down → fail-open to system DNS + toast (never leave you offline) · VPN → leak warning + split rules · captive portal → bypass until auth · `.local`/mDNS → never filtered · DoH provider down → ranked failover.

---

## 4. Startup surgeon

Sources: Run/RunOnce (HKLM/HKCU, WOW64), StartupApproved, services, scheduled tasks (all triggers), shell extensions, Winlogon, UWP startup tasks, auto-start drivers, vendor updaters.

Boot-phase attribution via ETW (kernel → services → logon → shell → idle). Items get a phase and a measured cost where possible; everything else is labelled **unattributed** rather than invented.

Knowledge base ships offline, keyed by path + signer + hash:
```yaml
- key: "service:DiagTrack"
  does: "Windows diagnostics + telemetry"
  disable_ok: false
  breaks: ["Feedback Hub", "some enterprise diagnostics"]
  recommended: "leave on; limit data in Privacy instead"
```

Actions: disable, **defer N seconds** (usually better), run-once-then-disable, kill-and-watch. Dependency-aware: refuses to disable a service with live dependents and names the dependent. **Watchdog** catches apps that silently re-add themselves and reports who did it.

---

## 5. Installer Autopsy

Watch any installer: files (USN), registry (ETW), services/tasks/drivers (before/after + notifications), process tree, every network endpoint. Optional T2 minifilter for exact writes.

Report: grouped, sized, risk-flagged (autostart, service, driver, beaconing), plain-English summary, plus a **leftover detector** (run the vendor uninstaller, re-scan, show what it forgot).

**Keep** (journaled and traceable) or **Erase** (reverse order: services/drivers → tasks → registry values restored from pre-state → files → verify). Anything unremovable is listed by exact path. Honest blind spots stated: kernel-mode writers, MSIX/UWP installs.

---

## 6. Command palette & Personal CMD

Built-ins execute **inside the engine** (~4 ms, no console flash). User commands declare their executor (`engine`, `shell`, `app`, `web`, `script`), a timeout, and permissions; imported commands show a **permission diff** before first run.

```
:clean            :snap name="before update"      :game --profile rocket-league
:run "C:\tools\sync.cmd" --timeout 120
```

Macro recorder turns a sequence into a command (`:morning`); after doing the same three actions twice, the app offers to save them. Sandboxed execution: fixed cwd, env allowlist, captured output pane. Open ≤ 50 ms, first results ≤ 16 ms.

---

## 7. Customization suite

Deep specs live in **`DESKTOP.md`**: custom terminal, taskbar/shell theming, icon packs, wallpaper engine, widget layer (Rainmeter-class clocks and monitors), sounds, and **Theme bundles** that package all of it as one shareable file — governed by a performance budget so customization never costs more than it should.

---

## 8. Sandbox modes

| Intent | Solution |
|---|---|
| "What did this installer do?" | Autopsy (observe, don't contain) |
| "Run this sketchy exe" | Windows Sandbox (Pro+) / AppContainer + low integrity (Home) |
| "Nothing touches my system" | Virtual profile (files/registry redirect) |
| "No network for this app" | Per-app WFP rule / firewall by path |
| "Throwaway app, clean uninstall" | Virtual profile + Autopsy |

All reachable by right-click in Explorer and from the palette. Each returns a receipt.

---

## 9. File manager companion (Extended)

Not an Explorer clone: tabs, dual-pane, **Miller columns**, USN-based search index, bulk rename with preview, hashing, duplicates (shared with the checkup), treemap sizes, rule engine ("downloads > 30 days → archive"), tags in SQLite for portability. Targets: 10k-entry folder < 100 ms; 1M-file index < 60 s.

## 10. Health, network lab, privacy (Extended)

- **Health**: SMART/NVMe wear, temps/fans, battery cycles, thermal history, driver/BIOS drift, crash correlation with the journal.
- **Network**: bufferbloat, resolver ranking, per-app throughput ledger, "game on this network" (measured toggles, revert on exit).
- **Privacy**: endpoint catalog, per-app ledger, air-gap with inspectable WFP rules, self-test that reads the real rules.

---

## Cross-cutting rules

1. **Receipts** on every mutation; "no measurable change" is a legitimate headline.
2. **Drift repair** — weekly hash check of managed state; show what Windows changed.
3. **Blame engine** — correlate crashes/hangs/boot regressions with recent tweaks.
4. **Honest claims** — no percentage without a measurement; cosmetic features labelled cosmetic.
5. **Two moods, one app** — Casual and Pro change presentation only, never behavior.
