# PC Optimizer

> A Windows 11 optimizer with full access, honest receipts, and a beauty budget of 0% idle CPU.

**Design phase — no product code yet.** This repo is currently the idea set: vision, architecture, specs, safety model, motion system and two clickable concept screens.

## Read in this order

| Doc | What it answers |
|---|---|
| [`IDEAS.md`](IDEAS.md) | The vision. Product thesis, the ideas catalogue, what ships first, and the moonshots |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Stack (Rust engine + WinUI 3 shell), the motion contract, safety layers, sandboxing options, privacy, process model |
| [`FEATURES.md`](FEATURES.md) | Deep feature specs: Cleaner, Gaming session, DNS blocker, Startup surgeon, Installer Autopsy, palette, profiles, sandbox, file manager |
| [`SAFETY.md`](SAFETY.md) | Full access without breakage: guardrails, preflight, journal, golden snapshots, boot guard, panic key, failure scenarios, recovery playbook |
| [`MOTION.md`](MOTION.md) | The frame contract, tokens, motion language, screen-by-screen choreography, accessibility, measurement |
| [`IDENTITY.md`](IDENTITY.md) | Two moods (Casual / Pro), Nim the mascot, copy voice, naming |
| [`DESKTOP.md`](DESKTOP.md) | The customization suite: terminal, taskbar/shell theming, icon packs, wallpaper engine, widget layer, one-file themes |
| [`SENSITIVITY.md`](SENSITIVITY.md) | Sensitivity Lab: yaw math, the U-curve test, 360° capture, watch mode, per-game conversion |
| [`MASTERPLAN.md`](MASTERPLAN.md) | **The audit:** what's designed, the honest gaps, ~40 new features scored, how every existing module upgrades, and the next ten in order |
| [`SCHEMA.md`](SCHEMA.md) | **The full data model:** 67 validated SQLite tables, every document format, retention, migrations, privacy guarantees |
| [`ROADMAP.md`](ROADMAP.md) | **Honest build estimates:** solo vs team, the MVP cut, what "best UI/UX" actually costs, and risk buffers |

## Clickable concepts

Open in any browser — every control is wired:

- **[`concept.html`](concept.html)** — the dashboard with four sections (Overview · Gaming · Desktop studio · Safety): PC Checkup with one-button fixes, Nim, power modes, cleaner receipts, DNS blocker with master switch, palette, gaming coach + session, terminal, wallpaper engine, widgets, themes, Guardian journal, sandbox, privacy, motion lab
- **[`concept-sensitivity.html`](concept-sensitivity.html)** — **Sensitivity Lab**: a real playable aim drill that measures your optimum and converts it to 20+ games (yaw math included)
- **[`concept-sandbox.html`](concept-sandbox.html)** — **Installer Autopsy**: watch an installer, read the receipt, then keep or erase every trace. Includes the **Casual ↔ Pro** mood switch

## The one-line promise

Full access, but **you will never be stuck** — every action is measured, journaled, and one click from undone.

## In plain language

- [`GUIDE.md`](GUIDE.md) — **start here.** The whole design and every future feature explained simply, no jargon
