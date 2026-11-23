# PC Optimizer

> A Windows 11 optimizer with full access, honest receipts, and a beauty budget of 0% idle CPU.

**Status: runnable prototype.** Thirteen design documents, four clickable concept screens, a validated 67-table schema, and at [`engine/`](engine) a working app: the Guardian safety core plus checkup, cleaner, benchmark, blocker, gaming session analysis, installer autopsy, desktop-studio governor, consent tiers, event feed and Sens Lab — driven by a local console (177 tests, runs on any OS).

```bash
cd engine
python3 -m venv .venv && .venv/bin/pip install -q pytest
.venv/bin/python -m pco_engine.server      # console at http://localhost:7788
```

The console is a reference client for the same JSON the WinUI shell will consume: everything it shows comes from the real engine, and every change it makes is journaled, snapshotted and undoable in one click.

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

## Running code

| Path | What it is |
|---|---|
| [`engine/`](engine) | **The app + the Guardian engine** — safety core (journal, preflight, snapshots, recovery, drift), checkup, cleaner with restorable vault, benchmark, ad/tracker blocker, recipes, event feed, Sens Lab, and the stdlib HTTP bridge + dark console. Python reference (177 passing tests), written to be ported to Rust 1:1 |

```bash
cd engine && python3 -m venv .venv && .venv/bin/pip install -q pytest
.venv/bin/python -m pco_engine.server                # the console, port 7788
.venv/bin/python -m pco_engine.cli --fresh crash     # the promise, demonstrated
.venv/bin/python -m pytest tests -q                  # 177 passed
```

The console covers nine views — Overview (health ring, live event feed, journal
verification), Checkup (per-category scores, consent tiers, fix-all with Undo),
Cleaner (scan → vault → restore), Blocking (on/off/pause/allow), Gaming (watch
gameplay → bottleneck → settings), Autopsy (install receipt → erase), Studio
(themes under a 3% idle-GPU budget), Sens Lab (cm/360 ⇄ 14 games + the PSA test)
and Performance (benchmark vs baseline, weekly report). `engine/README.md`
documents the guarantees each test proves and the Rust port contract.

## Clickable concepts

Open in any browser — every control is wired:

- **[`concept.html`](concept.html)** — the dashboard with four sections (Overview · Gaming · Desktop studio · Safety): PC Checkup with one-button fixes, Nim, power modes, cleaner receipts, DNS blocker with master switch, palette, gaming coach + session, terminal, wallpaper engine, widgets, themes, Guardian journal, sandbox, privacy, motion lab
- **[`concept-sensitivity.html`](concept-sensitivity.html)** — **Sensitivity Lab**: a real playable aim drill that measures your optimum and converts it to 20+ games (yaw math included)
- **[`concept-sandbox.html`](concept-sandbox.html)** — **Installer Autopsy**: watch an installer, read the receipt, then keep or erase every trace. Includes the **Casual ↔ Pro** mood switch

## The one-line promise

Full access, but **you will never be stuck** — every action is measured, journaled, and one click from undone.

## In plain language

- [`GUIDE.md`](GUIDE.md) — **start here.** The whole design and every future feature explained simply, no jargon
