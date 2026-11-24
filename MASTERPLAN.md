# Master Plan — Where We Are, What's Missing, What's Next

> The complete audit: everything designed so far, the gaps nobody has covered, ~40 new features scored, and a concrete upgrade path for every module that already exists.

---

## 1. The whole design on one page

```
                        ┌─────────────────────────────────────────┐
                        │              NIM (the surface)          │
                        │  Casual mode  ·  Pro mode  ·  Palette   │
                        └───────────────┬─────────────────────────┘
                                        │  one action model
        ┌───────────────────────────────┼───────────────────────────────┐
        │                               │                               │
  ┌─────▼─────┐                   ┌─────▼─────┐                   ┌─────▼─────┐
  │  MEASURE  │                   │   CHANGE  │                   │  PRETTIFY │
  │ Checkup   │                   │ Cleaner   │                   │ Desktop   │
  │ Benchmarks│                   │ Startup   │                   │ Studio    │
  │ Gaming    │                   │ DNS block │                   │ Terminal  │
  │  Coach    │                   │ Autopsy   │                   │ Themes    │
  │ Sens Lab  │                   │ Sandbox   │                   │ Wallpaper │
  │ Health    │                   │ Privacy   │                   │ Widgets   │
  │ Network   │                   │ Profiles  │                   │ Sounds    │
  └─────┬─────┘                   └─────┬─────┘                   └─────┬─────┘
        │                               │                               │
        └───────────────┬───────────────┴───────────────┬───────────────┘
                        │                               │
              ┌─────────▼─────────┐           ┌─────────▼─────────┐
              │     GUARDIAN      │           │   AIR-GAP CORE    │
              │ journal · snapshots│          │ zero network ·     │
              │ preflight · boot  │           │ local-only data ·  │
              │ panic · blame     │           │ inspectable rules  │
              └───────────────────┘           └───────────────────┘
```

**Nine documents, ~4 modules deep, 3 concept screens.** What exists today:

| Area | Status | Where |
|---|---|---|
| Vision + thesis | ✅ complete | `IDEAS.md` |
| Architecture, stack, process model | ✅ complete | `ARCHITECTURE.md` |
| PC Checkup + duplicates | ✅ specced | `FEATURES.md §1` |
| Cleaner | ✅ specced | `FEATURES.md` + `GUIDE.md` |
| Gaming session + Coach | ✅ specced | `FEATURES.md §2` |
| Sensitivity Lab | ✅ specced + playable | `SENSITIVITY.md` |
| DNS ad/tracker blocker | ✅ specced | `FEATURES.md §3` |
| Startup surgeon | ✅ specced | `FEATURES.md §4` |
| Installer Autopsy + sandbox | ✅ specced | `FEATURES.md §5, §8` |
| Palette + Personal CMD | ✅ specced | `FEATURES.md §6` |
| Customization suite | ✅ specced | `DESKTOP.md` |
| Safety engine | ✅ specced | `SAFETY.md` |
| Motion system | ✅ specced | `MOTION.md` |
| Identity / two moods | ✅ specced | `IDENTITY.md` |
| Health / Network / Privacy | 🟡 sketched only | `FEATURES.md §10` |
| File manager | 🟡 sketched only | `FEATURES.md §9` |
| Benchmarks / automation / updates / drivers / battery / peripherals | ❌ **missing** | this doc |
| Packaging, updates, signing, localization, support | ❌ **missing** | this doc |

---

## 2. Honest gap analysis

**What's strong:** the trust thesis, the safety engine, the honesty rules, the gaming measurement story, sensitivity. Depth in the *hard* places.

**What's thin:**
1. **Nothing closes the loop on "did it actually help?"** We promise measurements everywhere but never defined a standing benchmark. That's the single biggest hole in the product's honesty claim.
2. **Everything is on-demand.** A PC changes all day; the app only looks when you open it. There's no continuous, cheap watcher — the thing that makes apps feel smart.
3. **No lifecycle:** nothing about installing, updating, uninstalling, and keeping *itself* trustworthy over years (delta updates, rollback of the app, signing, localization, support).
4. **No battery/laptop story** at all — huge share of devices, and where "optimizer" apps actually make a measurable difference.
5. **No peripheral or hardware-health depth** — the "is my mouse double-clicking?" class of problems, which people love because it's decisive.
6. **No automation.** We have a great action model and a palette, but no "when X happens, do Y" — the natural next step, and where power users live.
7. **Windows Update / drivers are mentioned but not designed.** For a PC optimizer that's a headline feature, not a footnote.
8. **No multi-device or household story** (and no place for the git-based profile sync to actually pay off).

---

## 3. New features — scored

Score: **V**alue / **E**ffort (1–5, lower effort = better) / **U**niqueness. ★ = do it early.

### A. Close the loop (measurement)
| # | Feature | What it is | V | E | U |
|---|---|---|---|---|---|
| A1 ★ | **Standing Benchmark** | A reproducible 60-second CPU/GPU/disk/memory/IO bench (own, tiny, no download) run weekly or on demand. Every tweak is A/B'd against your *own* baseline, not the internet. | 5 | 3 | 5 |
| A2 ★ | **Evidence Locker** | Every receipt, report, chart, clip and screenshot in one searchable local archive. Export a **PC Health Report PDF** — the thing you hand a warranty tech, an RMA desk, or a buyer. | 5 | 2 | 5 |
| A3 | **Change feed** | One timeline of everything that changed on the PC (tweaks, installs, updates, drivers, crashes), in plain English, with "what happened just before it broke". | 5 | 3 | 4 |
| A4 | **A/B bench for game settings** | Extend the Coach's A/B test to any game, any setting, with confidence stats and a library of results per game. | 4 | 3 | 4 |

### B. Always-on intelligence
| # | Feature | What it is | V | E | U |
|---|---|---|---|---|---|
| B1 ★ | **Sentinel** | A < 3 MB event-driven watcher: boot time, disk growth, thermal spikes, crash, new autostart, DNS change, driver install, thermals. Not polling — ETW/power/registry notifications. Feeds the change feed and the weekly report. | 5 | 3 | 4 |
| B2 ★ | **Space time-travel** | Daily cheap folder-size snapshots → "space grew 4.2 GB this week, here's what" + prediction "you hit 90% in 12 days". | 5 | 2 | 5 |
| B3 | **Trend graph per finding** | Every checkup finding gets history: boot time, thermals, junk growth, security posture. | 4 | 2 | 3 |
| B4 | **"What did I change?"** | Ask in plain words → Guardian answers from the journal with links to undo each item. | 5 | 3 | 4 |

### C. Lifecycle (installing, updating, removing)
| # | Feature | What it is | V | E | U |
|---|---|---|---|---|---|
| C1 ★ | **Driver Manager** | Inventory + versions + rollback points; "known-good" scoring from your own crash history; snapshot before every driver install; block a bad update from coming back. | 5 | 3 | 4 |
| C2 ★ | **App Inventory + Updates** | winget-backed list of everything installed, outdated, and *known-exploited*. Batch update with a receipt; per-app changelog. | 4 | 2 | 3 |
| C3 | **Leftover cleaner** | Paired with Autopsy: after any uninstall, find orphaned files, registry keys, services, tasks — and show the evidence. | 4 | 3 | 4 |
| C4 | **Windows Update orchestration** | Defer to a window you choose, pre-snapshot before feature updates, "updates finished while you were idle" receipts, never fight the OS. | 4 | 3 | 3 |
| C5 | **OEM debloat with receipts** | Remove manufacturer trials/bloat, and block their silent reinstall tasks — reversibly, with a list of what each one was. | 4 | 2 | 3 |

### D. Laptops, power, thermals
| # | Feature | What it is | V | E | U |
|---|---|---|---|---|---|
| D1 ★ | **Battery Care** | Health (design vs actual capacity), drain analysis per app, charge-limit support where the hardware allows, "plugged/unplugged" automatic profiles. | 5 | 2 | 3 |
| D2 | **Thermal + fan curves** | History of throttle events with evidence, vendor fan-mode integration (Asus/Lenovo/etc. APIs), T2 manual curves for desktops. | 4 | 4 | 3 |
| D3 | **Lid-close / sleep hygiene** | Finds what prevents sleep, auto-fixes common wake locks, reports what woke the PC. | 4 | 2 | 3 |

### E. Automation (the natural evolution of the palette)
| # | Feature | What it is | V | E | U |
|---|---|---|---|---|---|
| E1 ★ | **Recipes** | "When [trigger] → do [actions]" with the existing action model: on game launch, on battery, on idle, at 22:00, when disk > 90%, when a new app installs. Preview, dry-run, receipts, easy off switch. | 5 | 3 | 5 |
| E2 | **Natural-language commands** | "clean downloads older than 30 days" → a previewed recipe. Local intent parsing, never executes blind. | 4 | 4 | 4 |
| E3 | **Workspaces** | Save/restore a working set: apps, windows, files, terminal tabs, dev servers. "Work mode", "study mode", one keystroke. | 4 | 3 | 3 |

### F. Hardware & peripherals (surprisingly loved)
| # | Feature | What it is | V | E | U |
|---|---|---|---|---|---|
| F1 ★ | **Peripheral Lab** | Mouse double-click + sensor jitter test, keyboard chatter test, polling rate, wireless battery, monitor dead-pixel/uniformity test, mic/headset test. Decisive answers, screenshots to prove it. | 5 | 3 | 5 |
| F2 | **PC Birth Certificate** | Full parts inventory (CPU, GPU, RAM kits, drives with serials, monitors, firmware) → exportable spec sheet + warranty dates + "is this SSD genuine" checks. | 4 | 2 | 4 |
| F3 | **Storage health deep dive** | SMART/NVMe wear, temperature history, "which drive is slowing this app", TRIM status, game/library placement suggestions. | 4 | 2 | 3 |

### G. Gaming extensions
| # | Feature | What it is | V | E | U |
|---|---|---|---|---|---|
| G1 ★ | **Stutter Cam** | When the Coach detects a spike, it saves a 10-second clip of that moment (and the counter graph beside it). You *see* the evidence instead of trusting a number. | 5 | 3 | 5 |
| G2 | **Game Settings Vault** | Snapshot every game's settings (OCR + config files), diff before/after, restore one setting or the whole profile after a patch. | 5 | 3 | 4 |
| G3 | **Launch hook** | Detect a game starting → session, sens profile, wallpaper pause, Focus mode, bench baseline, all in one. | 4 | 2 | 3 |
| G4 | **Streaming / VR modes** | Encoder settings + bitrate vs measured upload (Moonlight/Parsec/Steam Link/OBS); VR-specific compositor and supersampling guidance. | 4 | 3 | 3 |
| G5 | **Aim training plan** | From Sens Lab data: weekly drills with progress tracking, "your flick accuracy improved 12% at this sensitivity". | 3 | 3 | 3 |

### H. Security & privacy depth
| # | Feature | What it is | V | E | U |
|---|---|---|---|---|---|
| H1 | **App firewall with receipts** | Per-app network allow/deny, blocked-attempt log, "this app tried to talk to 41 hosts on first launch". | 4 | 4 | 4 |
| H2 | **Hardening presets** | Exploit protection, ASR rules, browser hardening — each with plain-English consequences, in tiers, all reversible. | 4 | 3 | 3 |
| H3 | **Breach & stale-account awareness** | Local list of installed apps that are EOL or known-affected; "this VPN version has a CVE". Purely local matching. | 3 | 2 | 3 |

### I. Product plumbing (unsexy, mandatory)
| # | Feature | Why it matters |
|---|---|---|
| I1 ★ | **Update system** | Signed, delta, staged rollout, one-click rollback of the *app itself*. If we ask users to trust our safety engine, our own updates must be the safest thing on the machine. |
| I2 ★ | **Packaging** | MSIX + winget + portable + `pco` CLI. Portable mode matters to power users and to reviewers. |
| I3 | **Localization** | Strings externalized from day one; Turkish, German, Spanish, Korean, Chinese, Portuguese first. |
| I4 | **Support bundle** | One click → a redacted, local-only diagnostic archive you choose to send. Nothing automatic, ever. |
| I5 | **Accessibility pass** | Screen-reader labels on every control, high contrast, keyboard-only paths, "reduce motion" already designed. |
| I6 | **Enterprise/GPO** | Later, but it changes what we can't do by default — decide early. |

---

## 4. Upgrading what already exists

### Cleaner → **Space Intelligence**
- **Time-travel graph** (B2) and prediction — the number that makes people act is "you'll be full in 12 days", not "42 GB".
- **Auto-rules:** "downloads older than 30 days → archive drive", with a preview, a dry-run and a receipt.
- **Learns from restores:** anything you pull back out of the vault twice is never suggested again.
- **Leftover mode** (C3) after uninstalls.
- **Dedupe v2:** perceptual image matching, near-identical videos (length + frame sampling), document version grouping.
- **Verified deletes:** hash-checked restore, vault integrity scan on schedule, one-click "empty vault" with an explicit warning.
- **Idle scheduling:** "clean while I sleep" with a morning receipt, never mid-work.

### PC Checkup → **Always-On Doctor**
- Runs **continuously at near-zero cost** via Sentinel (B1), not just on demand.
- Findings get **trends** and "since last week" deltas (B3).
- **Explain-like-I'm-five** toggle on every finding; the evidence is already there, this is a presentation layer.
- **Share card:** a screenshot-friendly score card with the evidence attached (what people actually post).
- **Peripheral + battery + driver checks join the posture list** (F1, D1, C1).
- **"Fix everything safe" gets a receipt history:** every batch is comparable — "this batch reclaimed 3.1 GB, last week's reclaimed 9.8".

### Gaming Coach → **Performance Studio**
- **Stutter Cam** (G1) — clips as evidence.
- **Settings Vault** (G2) — the thing that saves people after every game patch.
- **Launch hook** (G3) for a fully automatic session.
- **Benchmark library** (A1/A4) with per-game baselines and version deltas ("this patch cost you 6%").
- **Streaming/VR modes** (G4), **tournament mode** (maximum stability, everything background off, one switch).
- **Session report as a shareable card** with the honest verdict at the top.

### Sensitivity Lab → **Aim System**
- **Sens lock + auto-repair:** games silently reset sensitivity (patches, cloud sync, reinstalls). The Lab notices and offers to restore it — very few tools do this and it saves people constantly.
- **Crosshair designer** with a preview that renders exactly like the game.
- **Monitor-distance matching** explained per game with a visual.
- **Controller mode** with stick tests and the same U-curve logic.
- **Progress tracking** over weeks (G5) — "did the new sensitivity actually help my aim?"
- **Aim trainer integration** (KovaaK's / Aim Lab) alongside the built-in drill, as both paths.

### DNS Blocker → **Network Shield**
- **Per-device (LAN) protection** for the whole household from one PC.
- **Blocked-attempt ledger per app** (not just per domain) with "what broke" recovery flows.
- **List health:** false-positive reporting, safety-tested updates, "these three rules caused your login failure".
- **Phishing/malware download warnings** using local heuristics + the block list.
- **Connectivity doctor:** when a site breaks, it tells you *which* blocked domain is responsible, instantly.

### Startup Surgeon → **Boot Architect**
- **Boot timeline visualisation** with phases and per-item attribution.
- **What started this process** — full ancestry, signer, network activity.
- **Defer instead of disable** by default, with scheduling.
- **Service dependency explorer** and a "safe boot profile".
- **Re-add watchdog** with vendor attribution ("Acme Updater re-added itself on a schedule, here's the task").

### Palette → **Command Center**
- **Recipes** (E1) and **natural language** (E2) on top of the same action model.
- **Snippets library** with variables (`{selection}`, `{clipboard}`, `{date}`).
- **Shell integration:** `pco` works inside any terminal, so the palette and CLI are one system.
- **Hotkey conflict detection** and a "what's bound to this key" inspector.

### Desktop Studio → **Personal OS**
- **Dynamic themes:** time of day, weather, active game, work vs play.
- **Theme lint:** warns when a theme would cost more than the governor budget, with the exact component.
- **Theme A/B:** apply two variants, measure cost, keep the cheaper one.
- **Per-monitor everything.**
- **Widget SDK** (sandboxed WASM) so the ecosystem can grow without loading arbitrary DLLs.
- **Community gallery** with preview loops and honest cost labels on each theme.

### Guardian → **Time Machine + Warranties**
- **Plain-English journal search** (B4).
- **Health report export** (A2) for support/warranty/RMA — with the full evidence trail.
- **Recovery USB builder** with your golden state and the printed playbook.
- **Blame narratives:** "your PC got 40% slower at 14:02; you applied X at 14:01; it was reverted at 14:05".
- **Exportable audit:** everything the app ever did, readable without the app.

### Terminal → **Work Shell**
- **Session restore:** tabs, splits, cwd, scrollback, history, per project.
- **Profiles per project** with env, prompt theme and aliases.
- **Output intelligence:** grep within blocks, copy structured output, "explain this error" offline.
- **Elevation flow:** run a single command elevated without spawning a separate window, with a receipt.

### Sandbox / Autopsy → **Safe Execution**
- **Download scanner:** right-click any download → Autopsy it before you run it.
- **Trusted publisher allowlist:** signers you trust skip the prompts, everything else is watched.
- **No-network sandbox mode** as a first-class button.

### Health → **Hardware Confidence**
- **Peripheral Lab** (F1) and **Birth Certificate** (F2).
- **Provenance checks:** firmware versions vs vendor, fake SSD detection, RAM timing verification.
- **Warranty tracking** with expiry reminders in the weekly report.

### Nim → **Genuine companion**
- **Habit learning:** "you usually clean on Fridays — want that automated?"
- A **voice-off "insights" lane:** short, true observations, never nagging, max one per day.
- Skins and personality packs (already planned) stay cosmetic — never behaviour.

### Architecture → **Product-grade**
- **Sentinel** process (B1) at < 3 MB, event-driven.
- **Bench** module (A1) as its own isolated process so a bench never disturbs the UI.
- **CLI + JSON output** for every action — automation, scripts, and reviewers.
- **Plugin SDK (WASM)** with signed capability manifests.
- **Localization + accessibility** from the first commit, not bolted on.

---

## 5. The next ten, in order

| # | What | Why now |
|---|---|---|
| 1 | **Sentinel + change feed** | Everything else gets smarter when the app is always (cheaply) watching. |
| 2 | **Standing Benchmark + Evidence Locker** | Closes the honesty loop and produces the one artifact people share. |
| 3 | **Cleaner v2 (time-travel + auto-rules)** | Highest daily-use value per effort. |
| 4 | **Driver Manager + App Inventory** | The "PC lifecycle" story, and real, measurable stability wins. |
| 5 | **Gaming: Stutter Cam + Settings Vault** | The most *demonstrable* gaming features — clips beat claims. |
| 6 | **Battery Care + Peripheral Lab** | Differentiates on laptops and hardware trust; cheap to build. |
| 7 | **Recipes (automation)** | Turns the palette into a platform; power users will build the demos for us. |
| 8 | **Sens Lab: sens lock + aim progress** | Turns a one-time tool into a habit. |
| 9 | **Update system + packaging (MSIX/winget/portable/CLI)** | You cannot ship a safety-first app with an unsafe updater. |
| 10 | **Theme gallery + dynamic themes** | The shareable, screenshot-able surface that brings new users. |

**What not to build (yet):** a browser, a full Explorer replacement, a password manager, a VPN, an antivirus, a cloud sync service, a phone app, a store. Every one of them is a different company.

---

## 6. Convergence — combinations only this app can do

These are the compound features. Each one needs 3–4 modules and is therefore very hard to copy.

1. **"Did it actually help?"** — Checkup finds → apply → Standing Benchmark measures → Guardian journals → Evidence Locker proves. No competitor can answer this because they never measured anything.
2. **Game patch regression report** — Launch hook + Settings Vault + Coach history + Benchmark → "the 4.2 patch cost you 6% in this game; here's the setting to reclaim it".
3. **The incident story** — Sentinel + change feed + blame engine + journal → "your PC got slower at 14:02; a driver installed at 14:01; rolled back at 14:05". Rendered as one readable paragraph.
4. **Warranty case** — Evidence Locker + Peripheral Lab + Health + Birth Certificate → a PDF a support technician cannot argue with.
5. **Fresh PC in 15 minutes** — Profile + recipes + settings vaults + app inventory → reinstall Windows, restore everything, provably identical.
6. **Household network shield** — DNS blocker + per-device rules + privacy ledger → one PC protects every screen in the house.
7. **Automation with receipts** — Recipes + action model + Guardian → the first PC automation you can undo.

---

## 7. Risks this plan introduces

| Risk | Mitigation |
|---|---|
| Feature sprawl (40+ ideas, one builder) | The "next ten" list is the contract. Everything else waits. |
| Sentinel becoming a battery drain | Hard budget: < 3 MB, event-driven only, zero wakeups when idle; ship a power audit in CI. |
| Benchmarks being unfair or noisy | Own bench, own baseline, variance shown, "no measurable change" allowed. |
| Recipes becoming a scripting language | Deliberately capped: triggers × actions, no loops, no variables in v1. |
| Peripheral tests upsetting vendors | Honest language ("your device reported X twice in 40 clicks"), never "your mouse is broken". |
| Autopsy/recipes touching security software | Preflight detects AV/EDR and refuses politely, always. |
| Update system being the weak link | Signed, delta, staged, rollback-able, and itself listed in the journal. |

---

## 8. The one-paragraph summary

The design is strong where it's unusual — safety, honesty, measurement — and thin where it's ordinary: continuous observation, lifecycle, power, hardware, automation, and shipping. The roadmap therefore isn't "more features"; it's **closing loops**: a standing benchmark so claims are provable, a sentinel so the app is always right, recipes so it does things on its own, peripheral and battery tools so it answers hardware questions decisively, and a hardened update path so the safest app on your PC is also the best maintained. Everything else is already designed and waiting.
