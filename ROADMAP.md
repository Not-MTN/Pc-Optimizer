# ROADMAP — how long, how much, and what "best UI/UX" actually costs

> Honest numbers for building this at the quality bar the design sets. Three scenarios, because the answer depends entirely on **how many people** and **how good is "good"**.

---

## The blunt summary

| Scenario | Who | Calendar | Working hours | What you get |
|---|---|---|---|---|
| **A. Solo, full-time, best-quality** | 1 senior engineer (Rust + WinUI) | **30–34 months** | ~5,200 h | Everything specced, at the quality bar |
| **B. Small team** | 3–4 people (2 eng, 1 designer, 1 QA/release) | **15–18 months** | ~20,000–24,000 person-hours | The full product, properly tested |
| **C. Solo, MVP-first** | 1 person, ruthless scope | **6–8 months** to a shippable v0.1 | ~1,100 h | Core loop only, honest but narrow |

A usable **v0.1** is scenario A minus 80% of the features: **6–8 months solo**. The remaining 20 months in scenario A buy the other 80% — and that ratio is normal, not a sign of bad planning.

---

## Why it's this long (the six costs nobody expects)

| Cost | Reality | Months (solo) |
|---|---|---|
| **1. The safety engine** | Journal + snapshots + boot guard + panic + recovery drills. This is the product's promise; it must be bulletproof, and bulletproof means built slowly. | 4–5 |
| **2. Measurement accuracy** | Extracting real ETW metrics (frametimes, DPC, CPU/GPU counters) without injection, validated against reference tools, is genuinely hard. Getting numbers *wrong* destroys the whole thesis. | 3–4 |
| **3. Hardware breadth** | Every GPU vendor, laptop vendor, mouse driver and monitor behaves differently. This is where 60% of bug reports will come from. | 3 |
| **4. The UI at "best possible"** | Not the screens — the *feel*: 120 fps motion, spring physics, accessibility, Pro mode, empty states, 6 languages, 1000 keyboard paths. | 5–6 |
| **5. Windows reality** | Driver signing, WHQL-ish testing, SmartScreen/Smart App Control, antivirus false positives, VS Code-class support burden, WSUS/OEM variance. | 3–4 |
| **6. Support & maintenance** | After v1.0 the product is never "done": every Windows patch breaks something. | 30–40% of ongoing time |

---

## Phase-by-phase (solo, full-time, best quality)

| Phase | Deliverable | Weeks | Cumulative |
|---|---|---|---|
| **0. Foundations** | Rust engine skeleton, WinUI 3 shell, IPC, DB + migrations, design system + motion tokens, CI | 8 | 2.0 mo |
| **1. Guardian** | Tweak catalog, preflight, journal, snapshots, boot guard, panic key, **recovery drills** | 14 | 5.2 mo |
| **2. Checkup + Cleaner** | Scan engine, findings/evidence, scoring, one-button batch, duplicates, vault, receipts | 14 | 8.4 mo |
| **3. Palette + Personal CMD + Terminal** | Action model, DSL, recorder, terminal (blocks, guard), hotkeys | 12 | 11.2 mo |
| **4. Startup + Boot Architect** | Enumeration, ETW boot phases, knowledge base, defer, watchdog | 8 | 13.0 mo |
| **5. Gaming + Coach + Sens Lab** | Session state machine, ETW measurement, bottleneck rules, OCR, A/B, clips, yaw engine, drill | 18 | 17.1 mo |
| **6. DNS blocker** | Resolver, NRPT/WFP, list compiler, per-app, breakage recovery | 10 | 19.4 mo |
| **7. Autopsy + Sandbox** | USN/ETW capture, report, erase, Sandbox/AppContainer/vprofile | 12 | 22.1 mo |
| **8. Desktop Studio** | Terminal theming, taskbar/explorer, icons, wallpaper engine, widgets, themes, governor | 18 | 26.2 mo |
| **9. Sentinel + Benchmark + Evidence + Recipes** | Always-on watcher, standing bench, evidence locker + PDF, automation | 12 | 29.0 mo |
| **10. Hardware suite** | Peripheral Lab, Health, Battery, Drivers, App inventory | 10 | 31.3 mo |
| **11. Polish, signing, packaging** | MSIX/winget/portable, delta updater, 6 languages, accessibility, docs, site | 10 | **33.7 mo** |
| **12. Beta → 1.0** | Bug-fix cycles, driver matrix, support workflows | included above | — |

**≈ 146 weeks ≈ 33 months solo.** Start of "shippable v0.1" lands at the end of Phase 3 (week ~48, **11 months**) — which is why the MVP cut below exists.

---

## The MVP cut (what actually ships in 6–8 months)

Same discipline, 20% of the surface:

| Ships in v0.1 | Deferred |
|---|---|
| Guardian (journal, snapshots, panic, boot guard — non-negotiable) | Recipes, blame narratives |
| Checkup (safe findings only) + Cleaner + duplicates + vault | Per-rule auto-rules |
| Palette + Personal CMD (no terminal) | Terminal, snippets, CLI |
| Dashboard + honest receipts | Desktop Studio entirely |
| English only, 2 themes (Casual/Pro) | Localization, gallery |

**≈ 28–34 weeks solo** = 6.5–8 months. That's the number to plan against if you're one person.

---

## Team scenario (recommended if this is serious)

| Role | FTE | Months |
|---|---|---|
| Windows/systems engineer (Rust, ETW, drivers, services) | 1.5 | 16 |
| Front-end/app engineer (WinUI 3, Composition, accessibility) | 1 | 16 |
| Product designer (motion, two moods, themes, docs) | 0.75 | 10 |
| QA + release engineer (VM matrix, signing, support) | 1 | 16 |

**≈ 15–18 months**, ~€900k–€1.4M (European salaries, fully loaded). The QA role is not optional — it's the difference between "ships" and "works."

---

## Cost of the "best UI/UX possible" specifically

The UI is not where most of the time goes — but where the *perceived* quality comes from:

| Area | Solo weeks | What you're actually buying |
|---|---|---|
| Design system + motion tokens | 4 | one source of truth, so everything feels like one product |
| Casual/Pro duality | 3 | two complete surfaces from one engine (no forks) |
| Composition animation + springs | 4 | 120 fps feel that never drops frames |
| Accessibility (screen reader, contrast, keyboard-only) | 3 | WCAG-ish quality; also good UX for everyone |
| Empty / loading / error states | 3 | the difference between "polished" and "hobby" |
| Wallpaper engine + widgets | 6 | shaders, governors, measured cost |
| Terminal UX | 4 | blocks, guards, paste protection, theming |
| Localization scaffolding (6 languages) | 3 | done once, cheap forever — skipped once, expensive forever |
| **Total UI/UX** | **≈ 30 weeks** | ≈ 22% of the whole project |

**Rule of thumb:** the UI/UX work is 20–25% of total effort and 80% of first impressions. Cutting it to save time is the most expensive saving available.

---

## What makes it take longer than expected (the risk multipliers)

| Risk | Buffer |
|---|---|
| Kernel driver signing, WHQL, Smart App Control | +2–3 months if you ship T2 early |
| Antivirus/EDR false positives (an app that edits services and registry *will* be flagged) | +4–8 weeks of vendor submissions |
| ETW metric accuracy edge cases per GPU vendor | +6–10 weeks |
| Anti-cheat conflicts | +4 weeks (and the auto-suspend design must land before release) |
| Windows 26H2/27H1 breaking shell customization | +unbounded; budget 10% of every release cycle |
| Localization quality (not just strings — layouts) | +4 weeks |

A realistic **planning number** is scenario A × 1.25. If you plan to 33 months, budget 40.

---

## Maintenance after v1.0 (this never ends)

| Activity | Ongoing (solo) |
|---|---|
| Windows patch breakage + fixes | 30–40% of a work week |
| Driver/GPU matrix updates | 10% |
| Blocklist + yaw + knowledge-base updates | 5% |
| Support | 20% |
| New features | the rest |

**Sanity check:** this is why every optimizer that survives has a paid tier. A serious version needs revenue (one-time license, or ~€4–6/month) covering at least 1 FTE to stay honest and updated. That's not a business plan — it's a survivability note.

---

## If you want it faster

1. **Cut the driver (T2).** Removes signing, WHQL, anti-cheat risk, and ~2–3 months.
2. **Ship the checkup/cleaner/palette trio first** (the 6–8 month MVP) and validate it with real users before building the Desktop Studio.
3. **Integrate instead of building:** Sandboxie-Plus for deep sandboxing, Windhawk for shell mods, Everything's SDK for search, winget for updates. Each saves 4–8 weeks.
4. **One designer with taste beats three engineers guessing.** The UI is where the product is judged; don't staff it last.
5. **Buy the boring parts:** code signing, crash reporting, analytics-free telemetry review, localization service.

---

## The one-line answer

**Solo, full-time, at the quality bar in these docs: ~33 months (plan 40). A shippable honest MVP: 6–8 months. A small team: 15–18 months.** The UI/UX is ~22% of the work and ~80% of why people trust it.
