# Motion System — Beauty That Costs Nothing

> Rule zero: **motion never competes with work.** It must look expensive and cost nothing. Every animation in this app is a compositor animation (GPU, off the UI thread) or it doesn't ship.

## The frame contract

| Budget | Value | Enforced by |
|---|---|---|
| Frame interval @120 Hz | 8.3 ms | CI perf test on a mid-range iGPU laptop |
| UI thread per frame | ≤ 4 ms | ETW + own frame profiler |
| Layout passes per frame | 0 during animation | Analyzer that fails on animated layout properties |
| Composition animations in flight | ≤ 24 | Runtime cap; excess retargets the newest |
| Open palette → first result | ≤ 50 ms | Startup benchmark, hard gate |
| Idle cost (no window) | 0.0% CPU, 0 wakeups/s | Timer-resolution audit + powercfg report |

**Banned in animation:** `Width`, `Height`, `Margin`, `Padding`, `FontSize` changes, layout-thread timers, per-frame allocation in hot paths, JS/script-driven tickers, and any animation that keeps running when the window is occluded.

---

## Token sheet (single source of truth)

```yaml
duration:
  instant:  80ms      # state swap where motion would feel laggy
  fast:     120ms     # hover, press
  base:     200ms     # toggles, selection, chips
  emphatic: 320ms     # cards, sheets, dialogs
  page:     400ms     # section change
spring:
  snap:     { k: 480, c: 40 }   # pills, switches — near critical damping
  pop:      { k: 320, c: 24 }   # success moments, small overshoot
  gentle:   { k: 200, c: 30 }   # large surfaces, no overshoot
  page:     { k: 260, c: 32 }   # navigation
easing:
  out:      cubic-bezier(.2,.9,.2,1)
  inOut:    cubic-bezier(.45,0,.25,1)
  linenar:  linear               # only for ambient loops
radius:   { card: 12, control: 8, pill: 999 }
shadow:   { elev: "0 8px 24px rgba(0,0,0,.35)" }
```

Durations above 320 ms require a written reason in the PR. Ambient loops are 4–20 s and must be GPU-only.

---

## Motion language

### 1. Origin-aware entrances
Every surface grows from the thing that caused it: the window from the cursor, dialogs from their button, the palette from the screen center with an 8 px lift, sheets from the edge nearest the trigger. Users perceive origin-consistent motion as **faster** even at identical durations.

### 2. Springs over timelines
All interactive motion is spring-based and **interruptible**: pressing again mid-flight retargets velocity rather than queueing a new animation. No "wait for the animation to finish" states anywhere.

### 3. Continuity (shared-element transitions)
- A card expands into a full screen: the card's title, icon, and content morph position rather than cross-fading.
- Palette → action: the palette collapses toward the destination (the row you chose visibly "goes" to the right place).
- Clean → receipt: the ring travels into the receipt's header, so the receipt feels like the same object.

### 4. Micro-feedback (the 120 ms layer)
- Press: scale 0.97 + 1 px sink, spring back on release.
- Hover: 1 px lift, hairline brightens, radial cursor light (one compositor property).
- Toggle: thumb stretches 1:1.35 with velocity, snaps with ~4% overshoot.
- Selection: pill springs between positions, with a slight stretch proportional to velocity.
- Success: a single 320 ms `pop` — never confetti on routine actions. Confetti is reserved for the first successful panicked recovery, once.

### 5. Data motion (honest by construction)
- Numbers roll with tabular figures (no layout shift), e.g. `42.3 → 41.9`.
- Bars pour with 4% overshoot, then settle; never bounce repeatedly (looks like noise).
- **Regression animations are colored, not hidden**: if a metric got worse, the bar animates *the wrong way* in amber and the receipt says so.
- Charts draw once on first appearance; afterwards they only tick. No perpetual "live" animation that costs GPU when nobody's looking.

### 6. Ambient layer (free, optional, off in Pro mode)
- Window border aurora that orbits slowly (14 s loop, 1 compositor property).
- Nim's antenna pulse rate = CPU load (the animation *is* the meter).
- Global hue = health (teal → amber → red at throttle).
- Desktop particles that react to cleanup (vacuumed into the freed-bytes counter).

### 7. Panic motion
The one place motion is *deliberately* fast and stark: everything collapses into a single verified "restored" state in ≤ 400 ms, in near-monochrome, with one line: **"Everything is back."** Afterwards, one calm breath, then the receipt.

---

## Interruption & state rules

| Situation | Behavior |
|---|---|
| User clicks 5 times fast | Five retargets, no queue; final state is the 5th action |
| Window closed mid-animation | No cleanup animation; state is committed **before** the window closes |
| Background/occluded | All ambient loops stop; timers suspended; 0% CPU |
| Battery saver | Ambient off, durations × 0.6 |
| High contrast | Materials off, animations → instant except state changes (still readable) |
| Reduced motion (OS or in-app) | All springs → instant, all loops off; functionality identical |
| Thermals high | Ambient loops off (don't add GPU load to an overheated GPU) |
| Fullscreen game detected | Ambient off, palette still opens fast if summoned (it's the only thing allowed) |

---

## Screen-by-screen choreography

**Dashboard (first paint)**
1. Window: 96% → 100% + 12 ms blur-in from cursor (260 ms).
2. Cards: 8 px rise + fade, staggered 34 ms, `out` easing; total ≤ 700 ms.
3. Meters fill 120 ms after their card lands.
4. Nim blinks once at ~1.2 s, bubble pops at ~1.5 s (only first session of a day).

**Palette**
- `Alt+Space` → 30 ms fade + 4 px scale from 98%, backdrop blurs 120 ms.
- Results appear instantly for cached queries; typing has ≤ 16 ms latency, no animation on keystrokes except matched-substring emphasis.
- Arrow keys: selection pill springs (< 100 ms perceived).
- `Enter`: 120 ms collapse toward destination + 200 ms destination reveal.

**Cleaner**
- Scan: progresses with a subtle shader-like sweep, but the progress bar is *real* (bytes scanned / total).
- Confirm screen: totals roll up as rule toggles snap.
- Clean: ring sweeps once, particles vacuum, counter increments, receipt card flips open (300 ms, 3D flip, one layer at a time).

**Gaming session**
- Arm: the app retints to combat accent over 400 ms; a session chip drops into the header with a tick.
- During: no decorative animation at all during gameplay (the app is minimized; even if visible, ambient is pinned off).
- Conclude: chart ribbon draws left→right, numbers roll, verdict types in at 18 chars/s (readable), then revert receipt slides in.

**Guardian**
- Journal rows insert with an 8 px drop + tick.
- Undo: the row lifts, dissolves into the toast, and the toast's progress is the actual revert operation.
- Boot-guard recovery: on login, a single full-width band expands from the top with the report; the "everything's fine again" animation is one green breath, not a celebration.

**Pro mode**
No ambient, no particles, no sheen, no mascot bubble. 80 ms state swaps, monospace numbers, keyboard-first. Motion is still *smooth*, just quieter: springs become near-critically damped (`snap`), stagger is removed, and transitions are capped at 160 ms.

---

## Accessibility & comfort

- **Reduced motion** (OS + in-app): a hard switch that stops all non-essential motion instantly. Verified by automated test: with it on, zero ambient animations exist.
- **Motion intensity slider** (subtle / normal / lively) maps to token multipliers, capped by reduced-motion.
- **Focus**: all animations respect focus order; no motion that moves focus targets while the user is navigating by keyboard.
- **Motion never carries information alone** — every state change also has text, icon, and color.
- **Flashing policy**: nothing strobes; max luminance change rate stays under WCAG guidance; the aurora/nim loops are below 3 flashes/s by construction.
- **Sound is optional and separate** from motion tokens.

---

## Measuring it (shipped, not just CI)

- Hidden perf HUD (`Ctrl+Shift+F`): frame time graph, GPU time, layers, dropped frames, and the last interaction's cost.
- "Why did that stutter?" — correlation of dropped frames with the system event log (driver DPC, another app's GPU burst).
- Public perf budget in the settings: your machine vs. the target. Honest numbers, always.
