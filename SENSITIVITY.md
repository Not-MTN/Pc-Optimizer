# Sensitivity Lab — find *your* sensitivity, then take it to 40+ games

> Most "sensitivity guides" hand you a pro's number and call it advice. A pro's number is where *their* hand, pad, and eyes balance. This module measures **yours**, in about five minutes, and then converts it to every game you own.

## Why this can be exact (the physics)

Every FPS turns your view by a fixed number of degrees per "mouse count" (one dot of movement) at in-game sensitivity 1. That constant is the **yaw** value.

```
cm/360  = (360 × 2.54) / (DPI × in-game sensitivity × yaw)
eDPI    = DPI × in-game sensitivity          (only comparable inside one game)
convert: new_sens = old_sens × (old_yaw ÷ new_yaw)
```

**cm/360** — how many centimetres you drag the mouse to spin exactly once — is the only number that means the same thing everywhere. Two setups with the same cm/360 feel identical regardless of DPI, game, or slider numbers. That single fact is what makes "suggest the best sensitivity for all games" possible at all: **find your cm/360 once, convert everywhere.**

### Verified yaw values (shipped, versioned, patch-tracked)

| Game / engine family | Yaw (° per count) | Confidence |
|---|---|---|
| CS2, CS:GO, CS 1.6, CS:Source | 0.022 | ✅ verified ×4 sources |
| Apex Legends, Titanfall 2 | 0.022 | ✅ verified |
| Half-Life 2, Team Fortress 2, L4D2, Garry's Mod, Quake family, Deadlock | 0.022 | ✅ Source/Quake standard |
| Valorant | 0.07 | ✅ verified (Riot-confirmed) |
| Overwatch 2, Call of Duty (MW2019, Warzone) | 0.0066 | ✅ verified |
| Fortnite (X-axis) | 0.005555 | ✅ verified (ADS/build use multipliers — handled) |
| PUBG | 0.02 | ⚠️ community |
| Escape from Tarkov | 0.125 | ⚠️ community |
| Rust | 0.1125 | ⚠️ community |
| Rainbow Six Siege, Battlefield, Halo Infinite, The Finals, Marvel Rivals, Delta Force, others | — | ❌ **non-linear or unverified → use 360° capture (below)** |

Values are shipped as data, not code, and update with patches. Every entry carries a confidence flag, and the UI never presents a community guess as a verified fact.

**The universal path that needs no table at all:** the **360° capture** (section 2). Works in literally any game, including ones nobody has ever measured.

---

## 1. The Sens Lab (the test)

A short, repeatable aim test inside the app — a real aim trainer, not a quiz.

### Protocol
1. **Baseline**: read your current settings (entered, or OCR'd from a screenshot of the game's settings menu) to get your starting cm/360.
2. **Three test points**, log-spaced ±18% around your current sensitivity.
3. Each point: **6 acquisition shots + 4 tracking passes** (~40 s each).
4. **Refine pass** (optional, ~2 min): three more points narrowed around the best result → convergence.
5. **Fit a curve** through error-vs-sensitivity and take the **vertex** — the minimum of the U-curve — with a confidence interval.

### What each shot measures
| Metric | Definition | Why it matters |
|---|---|---|
| **Acquisition time** | ms from target appearing to the shot | slower = too slow (not enough travel per cm) |
| **Normalized error** | miss distance ÷ required travel (%) | the core signal |
| **Overshoot rate** | shots where the crosshair crossed the target and came back | too fast (too much rotation per cm) |
| **Undershoot rate** | shots that stopped short | too slow |
| **Correction count** | extra micro-movements after the first move | too fast, or shaky grip |
| **Tracking RMS** | average deviation while a target moves | low sens usually wins here |
| **Time-to-first-move** | reaction + decision | not sensitivity — shown separately so it isn't mistaken for it |

### The curve (this is the science)
At any cm/360, error is **U-shaped**: too fast produces overshoot and corrections, too slow produces undershoot and slow acquisition. The optimum is the bottom of the U. Three measured points give a parabola; the refine pass confirms it. Result:

```
Your optimum: 34.2 cm/360  (± 2.1, 88% confidence)
  ↔ Valorant 0.312 @ 800 DPI      ↔ CS2 0.99 @ 800 DPI
  genre band for tactical shooters: 35–55 — you sit slightly faster, which fits your overshoot rate
```

### Honesty rules
- **Never** claim "this is the best sensitivity in the world." It's *your measured optimum on this day, with this pad*.
- Report the interval, sample count, and variance. If the three points are indistinguishable → say **"your current setting is within noise — don't change it."**
- Warm-up guidance: first 20 shots discarded; the test says so.
- Fatigue-aware: if error rises steadily across a session, the app flags it and stops suggesting changes.

---

## 2. The 360° capture (universal game coverage)

Works in **any** game, verified table or not. Takes about 10 seconds.

1. Stand facing a memorable object in game. Press the capture hotkey.
2. Turn exactly 360° until you're facing it again. Press the hotkey again.
3. The app has been counting **raw mouse input counts** (OS-level `WM_INPUT`, not a hook into the game) and knows your DPI:

```
cm/360 = (counts / DPI) × 2.54
```

4. If you typed your in-game sensitivity, the app can also **derive that game's yaw** and (opt-in) contribute it to the shared dataset:

```
yaw = 360 × 2.54 / (DPI × sens × cm/360)
```

This turns "we support 40 games" into "we support **every game you can turn in**" — including obscure early-access titles, emulators, and games with non-linear sliders. It's the difference between a converter and a measurement instrument.

### Where the game hides its sensitivity
- **Writable config** (most UE/Source games): we can read and write the setting directly, with a backup.
- **Slider only**: **OCR** reads the value off the settings screen locally; we tell you the exact number to set, and the overlay can highlight the slider.
- **Obfuscated/in-game-profile-only**: we show the target cm/360 and let the game's own converter do the last step (Valorant, Apex, and OW2 all have built-in conversion).

---

## 3. Watching you play (in-game analysis, opt-in)

The Sens Lab gives the cleanest measurement. This gives the *situational* one — from the same raw input stream the capture uses, so no injection and no game files touched.

| Signal | How it's derived | What it tells us |
|---|---|---|
| **Overshoot corrections** | quick direction reversal (>180°) within 150 ms while decelerating | your sens is likely too fast for this game's target sizes |
| **Pad usage** | distribution of continuous travel between direction reversals | you use 18 cm of a 45 cm pad → your effective ceiling is pad-limited |
| **Sweep limits** | largest comfortable single movement | 180° turns need ≤ ½ pad — if not, sens is too low for the space |
| **Reposition frequency** | lifts/recentres per minute (inferred from input gaps) | high = fighting the sensitivity |
| **Flick size distribution** | distances of fast movements | tactical angles vs wide tracking, per game |
| **Consistency drift** | per-match variance in movement profile | fatigue/tilt proxy, not skill judgement |

Output: **"Your aim corrects overshoot 2.3× more in Valorant than in Apex — for a 34 cm/360 base, try 38 cm/360 in tactical shooters and keep 30 in arena shooters."** Per-genre adjustments, always with the evidence.

**Honest limits (stated in the UI):**
- Raw input tells us *how you moved*, not whether the shot hit. No game-state reading, no memory access, no overlay injection.
- Some anti-cheats are sensitive about global raw-input registration: the feature is **off by default**, per-game allow/deny, and it never runs when a protected title is in focus unless you've explicitly allowed that game.
- A bad aim day is not a sensitivity verdict. Patterns need ≥ 3 sessions before they become advice.

---

## 4. Per-game tuning that isn't just one number

| Factor | Handling |
|---|---|
| **ADS / scoped multipliers** | stored per game per weapon class (Apex 1×/2×/3×+; Valorant per-scope; CS2 zoom_sens_ratio; Fortnite separate ADS/scoped) with suggested values derived from your hipfire cm/360 |
| **FOV matching** | optional "monitor distance matching" (0% / 178% / focal-length) per game, explained in one line each; 4:3 stretched and ultrawide handled with a warning about horizontal-vs-vertical sensitivity |
| **Controller** | separate mode: deadzone, response curve (linear / exponential / dual-zone), ADS multiplier, aim-assist strength per game — with stick-specific tests (micro-adjust drift, sweep-to-target, tracking) and the same U-curve logic |
| **DPI** | guidance, not mysticism: 800 is a common baseline, 1600 is fine, very high DPI can add smoothing/jitter; never claim DPI improves aim |
| **Polling rate** | read from the device where possible, advise 1000 Hz; flag 125 Hz |
| **Mouse acceleration** | check and offer to disable Windows "enhance pointer precision"; confirm raw input in-game |
| **Windows pointer speed** | verify the 6/11 neutral notch |
| **Pad space** | measured from your input profile → the app refuses to recommend a cm/360 that can't fit a 180° turn on your desk |

---

## 5. Applying it

| Where | How |
|---|---|
| Config-file games | read + write with backup, one-click restore |
| Slider games | OCR reads current value → we show the exact target number → overlay highlights the slider |
| Built-in converters | we give you the number to enter (Valorant, Apex, OW2, Fortnite) |
| Aim trainers (KovaaK's, Aim Lab) | yaw values included; test your new sens in a trainer first, with the app scoring the result |
| Profiles | per-game profile saved in your Theme bundle, restored on reinstall |

Every applied change is a **Guardian tweak**: journaled, with the previous value, and one-click undo.

---

## 6. What this will never do

- ❌ No injection, no memory reading, no aim assist, no macros — it's a measurement tool, not a cheat.
- ❌ No "pro settings" package sold as your optimum.
- ❌ No claiming sensitivity makes you better. It removes a *variable*; aim comes from practice.
- ❌ No pretending a 6-shot sample is proof. Confidence is displayed, honestly, always.

---

## 7. Data model

```toml
[[game]]
id       = "valorant"
name     = "VALORANT"
yaw      = 0.07
tier     = "verified"
config   = { kind = "none" }              # none | ini | json | registry
multipliers = { ads = 1.0, scoped = [1.0, 1.0, 1.0] }
notes    = "per-scope sensitivity; built-in converter available"

[profile.rocket_league]
cm360    = 34.2
confidence = 0.88
source   = "sens-lab"                     # sens-lab | capture360 | manual | imported
updated  = 2026-11-23
genre    = "tactical"                     # drives the band + coaching voice
```

---

## 8. Roadmap

| Phase | Ships |
|---|---|
| v0.2 | Converter + verified yaw table + profiles |
| v0.3 | Sens Lab (3-point + refine), curve fit, confidence, per-game export |
| v0.4 | 360° capture (universal), yaw derivation, OCR slider reading |
| v0.5 | Gameplay watching (raw input), per-genre adjustments, pad limits |
| v1.x | Controller mode, ADS/multiplier tuning, shared dataset contributions (opt-in), aim-trainer integration |

---

## The one-line promise

**We don't sell you a pro's sensitivity. We measure yours, prove it with your own numbers, and carry it into every game you own.**
