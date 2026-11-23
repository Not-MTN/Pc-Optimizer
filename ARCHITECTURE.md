# Architecture — Beauty That Stays Fast, Access That Can't Break You

> Companion to `IDEAS.md`. This doc answers: **how does the UI look premium and stay quick**, and **how do we take full access without bricking anything**. No app code is being written yet — this is the design.

## Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | OS target | **Windows 11 only** (24H2 minimum, 26H2 baseline) |
| 2 | Access level | **Full** — T0 user up to T3 kernel, on a personal device |
| 3 | Cloud | **None, ever.** No account, no telemetry, no phoning home. Optional manual update check is the only exception, and even that can be turned off |
| 4 | Build order | **Design first.** No product code until the concept and specs are locked |
| 5 | UI goal | **Beautiful + animated + fast.** Motion is a feature, not decoration — but it may never cost CPU while idle |

---

## 1. UI stack — "beautiful and animated" *and* "lowest system usage"

The trap: gorgeous UIs are usually web UIs, and web UIs are usually heavy. The way out is not a lighter framework — it's **where the animation runs**. If every animation is a *compositor* animation (GPU, off the UI thread), you get 120 fps silk for ~0% CPU. If animations run in your UI thread or a JS event loop, they stutter, especially while a game is hogging the GPU.

| Option | Motion fidelity | Idle RAM | Cold start | Real Mica/Acrylic | Iteration speed | Verdict |
|---|---|---|---|---|---|---|
| **WinUI 3 + Composition** (C#/.NET 9) | ★★★★★ GPU compositor, springs, implicit animations | 60–80 MB | 0.6–1.2 s | ✅ native | ★★★ | **Pick for the shell** |
| Native DirectComposition (C++/Rust) | ★★★★★ total control | 25–40 MB | <0.3 s | ✅ manual | ★★ | Too slow to iterate for v1 |
| Flutter (Windows) | ★★★★★ custom rendering, Impeller | 60–90 MB | 0.8–1.5 s | ⚠️ custom-drawn | ★★★★★ | Strong alternate if you prefer one language for UI |
| Tauri / WebView2 / CSS | ★★★★ easy, but jank risk when GPU is busy | 120–170 MB | ~1 s | ⚠️ faked with tricks | ★★★★★ | Not for the shell; fine for docs/charts |
| Avalonia / WPF | ★★★ | 70–100 MB | 0.6 s | ⚠️ | ★★★ | Dated animation character |

**Decision:** **Rust engine + C# WinUI 3 shell**, talking over a named pipe with a shared-memory ring buffer for live stats. Rust owns ETW, WMI, cleanup, DNS, journal (where it's unsafe/expensive in .NET). C# owns windows, motion, and input. If the UI ever needs replacing, the engine doesn't move.

Why this wins the "beautiful but fast" brief:
- Composition animations are handed to `dwm.exe`/the compositor and keep running even if the UI thread is busy — so clicking fast never queues animations.
- Mica/Acrylic, rounded corners, dark mode, and system accent are first-class (`DWMWA_SYSTEMBACKDROP_TYPE`, `DWMWA_WINDOW_CORNER_PREFERENCE`, `DWMWA_USE_IMMERSIVE_DARK_MODE`) — the app looks native because it *is* native.
- WinUI 3 already gives correct per-monitor DPI, 120 Hz frame pacing, and title-bar theming for free.

---

## 2. The motion contract (the ten rules that keep it fast)

1. **Animate only compositor properties** — offset, scale, rotation, opacity, blur, clip. Never width/height/margins (layout = reflow = jank), never a per-frame timer.
2. **Springs over fixed durations.** Every animation is interruptible and re-targetable mid-flight. Fast clicking retargets, it never queues.
3. **Frame budget is law:** 8.3 ms at 120 Hz, and the UI thread gets ≤ 4 ms of it. Anything over budget fails review.
4. **Zero motion when unwatched:** window occluded, minimized, on battery saver, or Reduce Motion → animation instantly stops. Paused = 0% CPU, truly.
5. **State-driven, not fire-and-forget.** The UI renders *current state with a spring toward the target*; it never plays a scripted "video" of a change.
6. **One source of tokens.** Duration/easing/spring/color/radius live in a single token file that compiles to XAML, so motion is consistent everywhere.
7. **No spinner > 300 ms.** Skeletons with a slow shimmer instead; instant perceived navigation (route transition ≤ 120 ms).
8. **Materials have tiers:** Premium (Mica + layered translucency), Balanced (Mica only), Performance (opaque). Auto-switch on GPU/iGPU detection, battery, and user choice.
9. **Motion is measurable.** Built-in frame profiler overlay (dropped frames, GPU time, layers) — shippable in the release build behind a hotkey, not a dev-only tool.
10. **Reduce Motion and High Contrast always win.** Never argue with accessibility settings.

### Token sheet (first pass)

| Token | Value | Used for |
|---|---|---|
| `motion.fast` | 120 ms, `cubic-bezier(.2,.9,.2,1)` | hover, press feedback |
| `motion.base` | 200 ms | toggles, pills, selection |
| `motion.emphatic` | 320 ms, spring k=320 c=28 | cards, sheets, dialogs |
| `motion.page` | 400 ms, spring k=260 c=32 | section transitions |
| `spring.pop` | non-damped, ~6% overshoot | success moments, confetti |
| `radius.card` | 12 px (inner controls 8 px) | all surfaces |
| `stroke.hairline` | 1 px `rgba(255,255,255,.07)` | card edges — the "expensive" look |
| `shadow.elev` | `0 8px 24px rgba(0,0,0,.35)` | floating layers only |

---

## 3. Motion catalog — what actually moves

**Window & navigation**
- Window opens from 96% scale + 12 ms blur-in from the cursor position (feels like it grew out of the click).
- Rail selection pill slides with a spring and stretches slightly with velocity; icon scales 1 → 1.06.
- Cards stagger in 24 ms apart on first paint, 8 px rise + fade. Never replays on scroll.
- Section change: outgoing fades 60 ms, incoming rises 8 px over 200 ms. No slide-from-right page theater.

**Data & feedback**
- Meters *pour* with a 4% overshoot, then settle; numbers roll with tabular figures (no layout shift).
- Ring sweeps once with a velocity-based ease; the "GB freed" counter rolls digit-by-digit.
- Sparklines draw in left→right on first appearance only; afterwards they just tick.
- Hover lighting: a radial gradient lazily follows the cursor inside the card (one compositor property, 60 fps for free).
- Toggles squash/stretch the thumb on snap; switches have a 1-frame "settle" bounce.
- Drag & drop: item lifts (scale 1.04, shadow), drops with a soft landing spring; lists reorder with layout animations, never snapping.

**Palette & personal CMD**
- Alt+Space: 30 ms fade + 4 px scale from 98%, background blurs in over 120 ms.
- Results stagger 12 ms; selection pill springs between rows; typed characters get a subtle "ink" emphasis on the matched substring.
- Execute → the palette collapses toward the action it triggered (connected animation), so the user *sees* where a command went.

**Signature moments (the ones people screenshot)**
- Cleaning: 41.9 GB of particles get vacuumed into the counter, then a receipt card flips open. Undo is a big friendly button.
- Gaming session start: the whole app tint shifts to a "combat" accent, a stopped-watch style timer slides in, the chart ribbon draws.
- Every irreversible-feeling action ends in a *reversibility* animation: a small "snapshot taken" chip drops into the journal with a tick.
- Panic key: everything collapses into a single "restored" state in < 400 ms — the visual promise of the product.

**Nim, the mascot**
- Eyes track the cursor (2 px of range, clamped).
- Antenna pulses in time with CPU load; the pulse *is* the load meter.
- Sweats when thermals are high, sleeps with tiny Z's when idle > 5 min, thumbs-up after a clean, gasps before a risky tweak, then holds up a shield.
- Skins: classic, retro terminal, liquid metal, rubber duck. Because it's your PC.
- Renders on its own composition layer; when nothing changes it costs nothing.

**Ambient / system feedback**
- Global hue = health: calm teal → amber under load → red at throttle. The UI's temperature is your PC's temperature.
- Taskbar progress + badge via `ITaskbarList3` so long jobs are visible without opening the window.
- Optional sound pack: soft "tick" on toggles, "whoosh" on clean, boot chime on session start. All local, all mutable.
- Dream feature: **live wallpaper HUD** — render stats onto the desktop behind icons (WorkerW), toggled off during games.

---

## 4. Safety architecture — full access without breakage

"Full access" is fine. "Full access with no way back" is not. Six layers, all built before the first risky tweak ships:

| Layer | What it does |
|---|---|
| **L0 Guardrails** | Immutable protect-list: boot config, storage/network drivers, Defender, activation, system partition. T3 must pass a typed confirmation + creates a fresh golden snapshot first. Combinations that are known-dangerous (e.g., disabling a service another tweak depends on) are refused with an explanation |
| **L1 Pre-flight** | Every tweak declares preconditions and dependencies. The engine *simulates* the apply against current state before touching anything and blocks conflicts instead of discovering them at reboot |
| **L2 Journal** | Every change is a transaction: id, tier, pre-state, action, post-state, revert, checksum. Append-only SQLite. Nothing is applied "unrecorded," not even a registry value |
| **L3 Golden snapshots** | Small, fast config snapshots (managed registry keys, service/task states, power plans, shell config, driver list) — seconds to capture, seconds to restore. Not System Restore |
| **L4 Boot guard** | Service heartbeat on boot; if the machine doesn't reach a healthy desktop in N minutes, a recovery task restores the last golden state and disables the suspect tweak. Bugcheck on last shutdown → the last applied tweak is auto-suspended and reported |
| **L5 Panic key** | One global hotkey reverts everything to the last golden profile. Also the "Safe Mode" boot option in the installer |

Plus two ongoing jobs:
- **Drift repair** — Windows Update, Store, and third-party installers silently revert tweaks. A cheap weekly hash-check of managed keys detects drift, tells you what changed, and offers re-apply. This is the #1 real-world annoyance with every optimizer, and nobody solves it well.
- **Blame engine** — correlates crashes/hangs/BSODs and boot-time regressions with the tweak journal. "You applied X at 14:02; boot got 40% slower at 14:05. Revert?"

### Honest risk statement
With SYSTEM + a kernel driver, nothing is mathematically safe. The mitigation is **speed and certainty of recovery**, plus **never applying two risky things at once**. The product's promise is not "we never break it" — it's "you will never be stuck."

---

## 5. Sandboxing — yes, your idea is possible (seven different ways)

"Launch apps in a sandbox" is real, but it's a spectrum. Ranked by isolation vs. effort vs. breakage:

| Technique | Isolation | Effort | App breakage | Best for | Verdict |
|---|---|---|---|---|---|
| **Windows Sandbox** (Hyper-V) | VM-grade | Low | None inside; disposable | "Test this installer / this download / this sketchy tool" | ✅ Ship as right-click *Run in sandbox* (Pro/Enterprise/Education only — not Home) |
| **AppContainer / low integrity** | Medium | Medium | Some apps break | "Run this untrusted utility with no access to my files" | ✅ v1 |
| **Restricted token + Job object** | Weak–medium | Low | Rare | "No file writes outside its folder, CPU/RAM caps, kill everything on exit" | ✅ v0 |
| **Per-app virtual data profile** | Files only | Low | Rare | Throwaway Chrome profile, second Steam account, clean uninstall | ✅ v0 (very high joy per effort) |
| **Sandboxie-style virtualization** (minifilter + hooks) | Strong for FS/registry | Very high | Moderate | "Run anything without touching my system" | 🤝 Integrate Sandboxie-Plus rather than reinvent |
| **Installer autopsy** (monitor, don't contain) | None — but perfect visibility | Low–medium | None | "What did this installer actually do, and undo all of it" | ✅✅ **Build this first — it's the killer** |
| **Per-app network consent** (WFP callout) | Network only | High (driver) | None | Little Snitch for Windows | 🔶 T2, later |

**The standout idea: Installer Autopsy.**
Run an installer (or any new app's first launch) in *watch mode*. The engine records:
- files created/modified (USN journal, or a read-only minifilter for full fidelity),
- registry writes (hive diff + change notifications),
- services, scheduled tasks, drivers, startup entries it created,
- every network endpoint it contacted (ETW `Kernel-Network`),
- what it added to Start/context menus/firewall.

Then you get a plain-English report and two buttons: **Keep** or **Erase completely**. No VM, no snapshots of your whole disk, no "sorry, uninstaller left 400 files." This delivers 80% of the sandbox dream with 10% of the cost, and it's the most on-brand feature possible: *receipts*.

---

## 6. Privacy — "nothing leaves the device," provable

- **The engine service has no network capability at all.** A Windows Filtering Platform block rule scoped to the service SID denies all outbound; exceptions are loopback only. That's not a policy, it's a firewall rule you can inspect yourself.
- A separate, tiny **updater** is the only component allowed to touch the network, it's off by default, and it only fetches signed metadata with your click.
- All blocklists/themes/profiles ship **inside the installer** or are imported as files. No accounts, no sync servers. "Sharing" = exporting a `.pcoprofile` file or a git repo *you* own.
- **Privacy self-test** button: shows the actual firewall rules, the actual service SID, the actual rules applied to you — not a marketing page.
- Data lives in open formats (SQLite + JSON). "Export everything / delete everything" always works offline.

---

## 7. Process model

```
pco-service.exe      (SYSTEM, Rust)   ETW, WMI, cleanup, DNS, journal, boot guard   <15 MB
pco-ui.exe           (user, C#)       WinUI 3 + Composition, on demand              0 MB when closed
pco-tray.exe         (user, Rust)     palette hotkey + tray HUD                      <8 MB
pco-driver.sys       (T2, optional)   DPC/affinity/WFP/per-app network              opt-in
```

- **The UI never runs elevated.** It asks the service; the service validates the caller's SID and token over an ACL'd named pipe.
- One binary protocol between engine and UI, versioned. The UI can be killed and restarted mid-operation without losing state.
- T2 driver loads only on demand, unloads on session end, and is **auto-unloaded when a protected game process is detected** (see anti-cheat section in `IDEAS.md`).

---

## 8. Build order (still design-only)

| Phase | What | Gate |
|---|---|---|
| **D0 (now)** | Ideas + motion spec + architecture | You pick what to deepen |
| **D1** | Click-through prototype of the shell (framer-level detail, not the product) | You approve the feel |
| **D2** | Engine skeleton: journal + golden snapshots + one real tweak | Undo works on a real machine |
| **D3** | Cleaner + palette + dashboard on the real engine | Daily-drivable v0.1 |

---

## 9. Risks worth naming now

- **WinUI 3 rough edges** on very new Windows builds; mitigation: pin Windows App SDK, test on 26H2 day one.
- **Kernel driver + anti-cheat** is a genuine conflict (Vanguard/EAC). Mitigation: T2 is optional, unloadable, and the gaming profile runs fine entirely on T0/T1.
- **Windows Update drift** will fight us constantly; drift repair is a core feature, not a side quest.
- **Antivirus false positives** on a tool that edits services and registry: EV code-signing cert, early submission to Microsoft for whitelisting, and "explain every action" by design.
- **Scope creep**: the draw of this project is that every subsystem is a hobby. The roadmap is the defense.
