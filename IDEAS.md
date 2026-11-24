# PC Optimizer — Concept & Ideas

> Status: brainstorm / architecture sketch · Target: Windows 11 26H2+ (Win10 fallback) · Repo: `Not-MTN/Pc-Optimizer`

## The thesis (what makes this different)

Every "PC optimizer" on the market is one of two things: a **registry-cleaning snake oil app** that sells fake numbers, or a **pile of toggles** that bricks something and can't undo it.

This project wins by being the opposite on both axes:

1. **Honest** — never claim a speedup it didn't measure. Every optimization produces a *receipt*: before/after numbers, or it says "this is cosmetic."
2. **Reversible** — every OS change is a transaction with a pre-state snapshot and a one-click undo. "Ctrl+Z for your PC."
3. **Invisible** — a tiny native background service (< ~15 MB idle, 0% CPU when idle), and a UI that only exists while you're looking at it.

Full access without being scary. That combination is the product.

---

## The two architecture laws

### Law 1 — Lowest system usage
The optimizer must be lighter than the bloat it removes, or it's a joke.

- **Core = one native service** (Rust + `windows-rs`), not Electron, not a .NET desktop stack. Target: **< 15 MB RSS idle, 0% CPU, < 0.1% disk/hour**.
- **Event-driven only.** ETW subscriptions, WMI event sinks, `ReadDirectoryChangesW`, power/session notifications, `RegisterPowerSettingNotification`. No `while(true) { Thread.Sleep(1000) }` polling — that's exactly the sin we're fixing.
- **UI is a separate, on-demand process** that exits when closed. A native Win32/Direct2D shell for the fast parts; WebView2 only for the heavy editor screens (profiles, charts) and it unloads after.
- **Telemetry store** = memory-mapped ring buffer → batched SQLite writes on idle. Nothing runs while a game is fullscreen.
- **Heavy work is deferrable**: all maintenance (cleanup, defrag, scans, updates) runs in a guided/idle window: on AC, idle > 5 min, temp under threshold, not in a game. A scheduler that understands your PC, not a cron.

### Law 2 — Full access, safely
Power features need admin/driver-level reach. The trick is **capability tiers**, not a scary UAC blast at install:

| Tier | What it needs | What it can do | Default |
|---|---|---|---|
| **T0 User** | nothing | UI, reports, clean your own temp, per-user app settings, DNS-over-HTTPS proxy, palette, scripts you own | always on |
| **T1 Admin service** | Windows service (admin) | services, scheduled tasks, HKLM tweaks, system cleanup, shell config, power plans | opt-in, auto-rollback |
| **T2 Driver pack** | signed kernel driver(s) | DPC/latency tuning, interrupt affinity, per-process network filtering, real-time IO | opt-in, clearly labeled, separately uninstallable |
| **T3 Advanced** | explicit "I know what I'm doing" | WinSxS surgery, component removal, registry compaction, kernel params | behind a toggle + backup confirmation |

**Anti-cheat reality check:** T2 kernel drivers get flagged by Vanguard / EAC / BattlEye. So the design includes a **Safe Mode kernel** — everything in gaming mode must have a T0/T1 path that is invisible to anti-cheat, with T2 auto-detected and suspended when a protected game launches.

---

## The Guardian layer (the moat)

This is the feature nobody else has and everybody needs.

- **Tweak transactions.** Every change is a typed object: `{id, tier, precondition, action, prestate, poststate, blast_radius, revert}`. Applied changes are stored in a journal (SQLite, checksummed).
- **Auto-revert on breakage.** Apply → wait for "keep this?" confirmation (like a display-mode change) → revert automatically if you reboot unexpectedly, or if the service doesn't hear back after N minutes.
- **Snapshots that aren't System Restore.** System Restore is slow and flaky. Instead: content-addressed snapshot of *config state* (registry hives of interest, service/task states, power plans, shell config, app list) — small, fast, restorable in seconds.
- **"Time Machine for Windows."** Continuous lightweight config snapshots over days/weeks. "What changed between Tuesday and now that made boot slower?" — diffable, restorable.
- **Blast-radius labels** in the UI: `Cosmetic` → `Reversible` → `Needs reboot` → `Can break X`. Users can see the risk before clicking.
- **Kill switch**: one hotkey reverts *everything* to the last known-good profile.

---

## The ideas

### 1. One command palette to rule the PC — "native Raycast for Windows"
Everything in the app is an **action**, and every action is reachable from a single `Alt+Space` palette: clean now, apply gaming profile, toggle taskbar style, run my snippet, open my folder, "why is my PC slow?". Fast fuzzy search, no Electron, opens in < 50 ms from a resident 8 MB helper.

- **Personal CMD**: user-defined commands = alias + args + target (shell/PS/exe/URL) + hotkey + icon. Shareable. This is your "personal CMD" ask, done properly.
- **Actions are scriptable** via a tiny action DSL / TOML, so the palette is a mini-automation tool (Alfred/Raycast-style workflows) without a plugin VM in the hot path.

### 2. Gaming mode as a *session*, not a switch
Most "gaming modes" are 20 toggles and a placebo. Ours:

- Launch → snapshot → apply profile → **measure** (ETW frame times, PresentMon, input latency proxy, DPC/ISR) → on exit, **report the delta** and revert.
- Profile = declarative: power plan, GPU scheduling, background throttles, network QoS, notifications, RGB/overlay killers, affinity rules, and *what to pause* (updaters, indexer, sync clients).
- **Per-game profiles** with auto-detect (Steam/SX/Epic libraries, exe hash) and a curated "known bad background apps" list.
- **Honest verdict**: "Frame-time consistency improved 12%; average FPS within noise. Disable HAGS to see a real change." That builds trust faster than any benchmark screenshot.
- **Anti-cheat-aware**: auto-suspends T2 features for protected titles, and shows you a badge when it does.

### 3. The Cleaner with a brain — clean by *cost to regenerate*
Instead of "clean registry!" → **classify by regeneration cost and risk**:

- **Free to delete** — temp, crash dumps, shader caches (with rebuild warning), thumbnails, WinUpdate leftovers, hibernation file (resizable instead of deleted), old installers.
- **Needs a policy** — hibernation, WinSxS component store, Windows.old, delivery optimization cache, dormant Store apps, OneDrive files-on-demand.
- **Never touch** — user docs, save games, browser profiles, dev caches (with a "your dev machine" profile that spares node_modules/.cargo/etc.).
- **Visually**: a disk heatmap showing what's actually eating space (WinDirStat-class scan, but streaming and cancelable), and "reclaimable" is computed, not guessed.
- **Zero blind registry deletion.** Registry "cleaning" is opt-in, backed up hives, and labeled honestly as near-zero benefit.

### 4. Ad blocker built into the OS, not the browser
Browser extensions only cover browsers. The best ad/tracker block is **below the app**:

- **Userspace DNS proxy** (tier 0, no driver): DoH/DoT resolver that compiles hosts + wildcard policy into a trie, per-app policies, per-list toggles, live block log with counters. Works for every app: browsers, launchers, mobile-tethered devices, games, smart-TV apps on your LAN.
- **Optional T2 WFP callout driver** for HTTPS-level/CNAME-cloaking blocking and per-process filtering — real ad blocking without a certificate MITM.
- **Ship with owned, compiled blocklists** (no runtime rule parsing cost) + a subscription system, and a **transparency screen** for Windows telemetry endpoints with a plain-English "what this does / what breaks" for each.
- Side bonus: DNS cache warm-up + resolver latency ranking = *actual* browsing speedup, measurable.

### 5. Theming as code — one portable "PC profile"
Your whole personal setup as a single versioned file (`profile.toml`/YAML), git-friendly:

- Taskbar (position, size, grouping, labels, clock format, tray), Start layout, File Explorer (tabs, compact, classic menu, pane choices), context-menu items, terminal/CMD defaults + aliases, keyboard shortcuts, wallpaper/accent, default apps.
- **Apply** via supported COM/registry/`IExplorerBrowser` APIs where possible; **Windhawk-style mods** (inject into `explorer.exe`) as an optional pack for things Windows doesn't expose. Never patch system binaries.
- **Import someone else's setup** as a one-liner. Browse community profiles. "My gaming rig / my dev box / my grandma's PC."
- **Fresh-install bootstrap**: keep your profile in git → after a Windows reinstall, PC Optimizer recreates your whole environment in ~10 minutes. That's the killer flow.

### 6. Personal file manager
Not another Explorer clone competing with 15k LOC of thumbnails/network/cloud. A **companion**:

- Tabs, dual-pane, fast indexed search over your drive (own small index or Everything SDK if present), previews, bulk rename, hashing, duplicate finder, "what's eating this folder," rules ("move downloads >30 days to archive"), tags.
- **Deep Windows integration**: a proper "Open in PC Optimizer" context menu, and it can *drive* Explorer settings from the same profile.
- Optional: **column-based browsing (Miller columns)**, which Windows still doesn't have natively.

### 7. Startup & process surgeon (with explanations, not blanket disabling)
- Visualize the **boot chain**: every startup app → service → scheduled task → shell extension that runs at login, with boot-time attribution (ETW `Microsoft-Windows-Kernel-Process` / `Winlogon` phases).
- Curated **"what breaks if I disable this"** knowledge base (offline, versioned, shipped data file). No cloud round-trip, no guessing, no "disable all services" blackbird nonsense.
- **Watchdog**: catches apps that re-enable themselves (classic updater behavior) and tells you who did it.
- Optional: **delay/defer** instead of disable — "start this 60s after login" often beats disabling.

### 8. Network tuning you can measure
- Per-app DSCP tagging and bandwidth priority (T0 user-mode where possible), background throttle for updaters/sync/stores, Windows Delivery Optimization cap.
- **Latency lab**: bufferbloat test, DNS resolver ranking, per-app throughput map (who's using bandwidth, to which host) — local-only.
- **"Game on this network"** one-click: measured Nagle/tuning toggles, QoS, Wi-Fi power-save off, and revert-on-exit.
- Router-adjacent advice where software ends ("your 2.4 GHz channel is saturated"), no fake "TCP optimizer" registry hacks.

### 9. Telemetry & privacy map
- Enumerate what Windows and installed apps send, with an allowlist and a **block-by-default "quiet mode"** for the known telemetry endpoints (with the usual caveat list for Windows Update/Defender/Store).
- Per-app network ledger: bytes, destinations, tracking domains contacted. Local dashboard, nothing leaves the PC.
- **Fingerprint/inventory view**: what's installed, what's licensed, what's unknown, what's running at boot — "your PC's x-ray."

### 10. Health & longevity
- SMART/NVMe wear, temps, fan RPM, battery cycles/degradation, driver/BIOS drift, thermal-throttle history, "your GPU was 8 °C hotter this month."
- **Maintenance calendar** that's actually right for your hardware (SSD TRIM vs. defrag, SMART-based warnings, BIOS/UEFI updates via vendor APIs where available).
- Crash/hang analysis: correlate blue screens and app hangs with recently applied tweaks/drivers — and offer to revert the suspect transaction.

### 11. Moonshots (clearly labeled, v2+)
- **Local "why is my PC slow?" copilot** — a local model (NPU/GPU on Copilot+ hardware, CPU fallback) that can *only* answer from your telemetry store, with citations to actual counters. No cloud, no hallucinated advice. v0 fallback: rule-based explainer that's already excellent.
- **Deterministic PC rebuild** — "this PC is disposable": your whole machine state (apps, config, profiles, tweaks) declared as code and rebuilt anywhere.
- **Interrupt/affinity lab (T2)** — hunt DPC latency, pin mouse/keyboard USB interrupts away from busy cores, per-game CPU topology hints. Pro-level and measurably real.
- **WASM plugin SDK** — community actions with signed capability manifests (never arbitrary drivers), sandboxed by default.

---

## What I'd ship, in order

| Phase | Scope | Why this order |
|---|---|---|
| **v0.1** | Guardian core (transactions + journal + auto-revert) · Cleaner (safe classes + heatmap) · Palette with personal commands · Honest dashboard | Proves the trust thesis with the two most-used features |
| **v0.2** | Gaming mode as session (measure + report + revert) · Startup surgeon · PC profiles (taskbar/Explorer/CMD) | The "wow" customization + measurable gaming |
| **v0.3** | DNS ad-blocker (T0) + transparency screen · Network lab · Health dashboard | Differentiator vs. every other cleaner |
| **v1.0** | T2 driver pack (opt-in) · Fresh-install bootstrap · community profiles · installer + signing pipeline | Full access, safe and shippable |

**Cross-cutting from day one:** crash reporting (local opt-in), signed releases + reproducible builds, `--safe-mode` that disables all T2, and the **"receipts or it didn't happen"** rule in the UI.

---

## Open decisions (need your call)

1. **UI stack** — native Win32/WinUI3 shell (lightest, most work) vs. Tauri/WebView2 (fast to build, heavier but still ~40 MB, and the webview unloads). Recommendation: **native shell for the palette/dashboard + WebView2 for the editor pages**, measured.
2. **Scope of v0.1** — vertical slice we can *use* fast (Guardian + Cleaner + Palette), or breadth-first mock of everything?
3. **Windows 10 support** — target Win11 24H2+/26H2 only (cleaner, fewer legacy paths), or Win10 too (bigger audience, more test surface)?
4. **Distribution** — Microsoft Store (needs MSIX + Store policy: kernel drivers are tricky), GitHub Releases + winget (freer), or both?

---

*This doc is the brainstorm, not the spec. Next: lock the four decisions above and turn this into an architecture doc + v0.1 plan.*
