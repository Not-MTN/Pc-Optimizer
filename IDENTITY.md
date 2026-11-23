# Identity — Two Moods, One App

> Your call: **mascot-forward *and* subtle/pro**, split by mode. Same engine, same guardrails, same receipts. Only the surface changes.

## The two moods

| | **Casual** (default) | **Pro** |
|---|---|---|
| Audience | everyday use, first install, the "just make it faster" mood | tweaking, benchmarking, debugging, daily driving by power users |
| Voice | second person, warm, short sentences ("Let's get that space back.") | imperative, terse, numeric ("Reclaim 41.9 GB · 4 classes · 0 policy") |
| Typeface | Segoe UI Variable Display; tabular numbers | Cascadia Mono / Consolas for data; Segoe for labels |
| Density | generous, 12 px radius, cards breathe | compact, 8 px radius, 32 px rows, no ornament |
| Motion | full library: springs, tilt, particles, sheen | `snap` springs only, 80–160 ms, no ambient |
| Nim | present: eyes track, moods, bubbles, thumbs-ups | tiny: a 20 px glyph in the status bar that pulses with load |
| Proactivity | suggests, explains, celebrates | reports, never celebrates, always shows raw deltas |
| Color | hue = health, gradients, glow | monochrome + one accent per severity |
| Sound | optional "studio quiet" scheme | off by default |
| Error style | "That didn't work — here's what I did instead." | `E_TWEAK_CONFLICT (0x4A21): svc:X depends on svc:Y` |

**Switching** is instant (`Ctrl+Shift+P`) and *state-neutral*: in-flight operations continue, nothing is re-applied, only the presentation changes. The mode is per-window, remembered per section (palette can be Pro even when the dashboard is Casual).

---

## Nim, the mascot (and why he's not just decoration)

Nim is the **honest-state machine**: he is literally rendered from live telemetry, so he can never lie about what the PC is doing.

| Expression | Driven by |
|---|---|
| Antenna pulse rate | CPU utilization (the pulse *is* the meter) |
| Eye color | health: teal ≤70%, amber 70–90%, red >90% sustained |
| Sweat drops | thermals above the device's own threshold |
| Sleep (`z z z`) | system idle > 5 min, or the app is unfocused |
| Blink | fixed 6.2 s rhythm, randomized ±1.4 s |
| Thumbs-up | after a verified successful action |
| Shield | when a risky action is armed |
| Gasp | preflight found a conflict |
| Thinking dots | simulation/diff computing (the dots are the actual progress) |
| Micro-frown | a measurement regressed (he shows it before the text does) |

**The rule that keeps him charming instead of annoying:** Nim never speaks about things he can't see, never blocks a workflow, and every bubble auto-dismisses. **All voice is in Casual mode only.**

### Skins
Classic (default), Retro CRT, Liquid Metal, Rubber Duck, Monochrome (Pro). Skins are theme files, not forks.

---

## Copy voice guide

**Casual**
- Say the benefit in the first 6 words. No "advanced optimization algorithms."
- Numbers always with a unit and a comparison: "**4.2 GB** freed — about 3 episodes of a show."
- Never promise a percentage we didn't measure. "No measurable change" is a headline, not a footnote.
- For failure: what happened → what I did → what's next.

**Pro**
- Numbers first, units always, deltas signed, variance included: `frametime σ 4.8 → 1.9 ms (n=312, p<.01)`.
- No adjectives. No exclamation marks. No mascot in the data path.
- Errors as codes + one-line cause + one-line fix.

### Banned everywhere
"Turbocharge", "blazing", "max performance", "boost your PC", "one-click magic", "optimum", "AI-powered" (unless it's literally a local model and we say which), and any percentage not produced by a measurement shipped in the app.

---

## The identity rules (5)

1. **The receipt is the aesthetic.** Every beautiful moment is a record of something real.
2. **Speed is a design element.** Motion is fast, interruptible, and never in the way.
3. **One accent at a time.** Health hue, one highlight color, everything else neutral — it's why the UI reads as calm.
4. **No fake data, ever.** If something isn't known, the UI says "unknown" — never a plausible-looking placeholder.
5. **Two moods, zero forks.** Casual and Pro are presentations of the same truth. A setting must never behave differently.

---

## Naming (working)

| Item | Name | Notes |
|---|---|---|
| App | **PC Optimizer** (working) | `Pc-Optimizer` repo; rename TBD |
| Mascot | **Nim** | short, warm, easy in any language |
| Safety engine | **Guardian** | transactions, journal, snapshots, boot guard |
| Cleanup receipts | **Receipts** | in-product term; "your receipts" |
| Config snapshots | **Golden states** | "restore a golden state" |
| Panic key | **The Panic Key** | `Ctrl+Alt+P` |
| Profile file | `.pco` / `.pcoprofile` | YAML inside, git-friendly |
| Pro palette | **Command Bar** | same system, denser surface |
