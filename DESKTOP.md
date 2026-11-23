# Desktop Studio — Customization Suite

> Make Windows look and feel like *yours*: terminal, taskbar, icons, wallpaper engine, widgets, sounds. One discipline applies to all of it: **beauty must not cost performance**, and every visual layer can be measured, paused, and reverted.

## The unit of customization: a Theme

A **Theme** is one file that bundles everything, so a look is shareable, versionable, and applied in one click.

```yaml
meta: { name: "Aurora", author: "you", version: 2 }
colors: { base: dark, accent: "#4fd1c5", secondary: "#7c9cff" }
shell:
  taskbar: { style: floating, opacity: 0.82, iconSize: 24, align: center, clock: "{HH:mm} {s} {temp}" }
  start:   { layout: compact }
  explorer:{ tabs: on, compact: on, classicMenu: on }
  chrome:  { corners: 10, mica: strong }
icons:   { pack: fluent-neon, overrides: { "steam.exe": "custom/steam.svg" } }
wallpaper: { engine: shader, scene: "aurora-flow", fps: 30, reactToAudio: false }
widgets:
  - { type: analog-clock, style: "minimal", at: [40, 60], size: 140 }
  - { type: cpu-gauge, at: [1820, 40], size: 120 }
  - { type: now-playing, style: "liquid", at: [1600, 980], size: 300 }
terminal: { theme: "aurora", font: "Cascadia Code", opacity: 92, blur: on }
sounds:  { pack: "studio-quiet", boot: "soft-rise" }
```

Apply, export, share, or commit it to your own git repo. Import shows a diff first.

---

## 1. Terminal — the "custom CMD" (a real terminal, not a skin)

**Shells:** PowerShell 7, Windows PowerShell, cmd, WSL distros, Git Bash, nushell — detected automatically.

**Look:** tabs, split panes (any direction), acrylic/blur, background image, opacity, padding, cursor styles, ligatures, theme import from Windows Terminal schemes (`.json`) in one click.

**Feel / features**
- **Command palette inside the terminal** (`Ctrl+K`): run snippets, open settings, insert paths, explain a command.
- **History search** with fuzzy matching and frequency ranking, plus `↑`-preview.
- **Inline autocomplete** with plain-English hints ("`rm -rf` — deletes recursively and permanently").
- **Block output** (Warp-style): each command's output is a collapsible block with copy/scroll/jump, error highlighted, exit code and duration shown.
- **Click-to-act**: paths and URLs in output are clickable; hashes/IDs copy on click.
- **Error explainer**: known Windows/shell errors get a one-line cause and fix, from an offline knowledge base.
- **Aliases & abbreviations**: `gco` → `git checkout`, expanded visibly before execution; per-profile env and prompt themes (oh-my-posh compatible themes built in).
- **App commands in the terminal**: `pco clean`, `pco game start` — the same engine actions, scriptable.

**Safety (the part most terminals get wrong)**
- **Paste guard:** multi-line pastes (classic malware vector) require confirmation, with the dangerous parts highlighted.
- **Destructive command confirmation** with a dry-run offer (`Remove-Item -Recurse`, `format`, `diskpart`, `rm -rf`).
- **Command explainer** for anything with delete/format/registry/network-admin patterns before it runs.
- Sandboxed mode: run a command with filesystem writes redirected to a throwaway layer.

**Performance:** renders only when visible and focused; zero cost when the window is minimized or another app is fullscreen.

---

## 2. Taskbar, Start & shell theming

**Native settings (safe, documented, instantly reversible):**
- Color: background tint, opacity, accent, hover glow, border, blur strength
- Layout: center/left, icon size, spacing, labels never/always/when-full, grouping off/on
- Clock: seconds, date format, **custom panel** (weather, now playing, CPU, timers) as a tray extension
- Tray: hide overflow, always-show list, per-icon behavior
- Multi-monitor: taskbar per display, primary-only clock, per-monitor alignment
- Start: layout density, hide recommendations, transparency, folder groups
- Explorer: tabs, compact mode, classic context menu, details pane, drive grouping
- Window chrome: corner radius, shadow depth, Mica/Acrylic strength

**Icon packs:** replace app icons (per-app override or whole pack), file-type icons (registry, backed up), folder icons, and drive icons. Packs are folders of SVG/PNG + a manifest; conversion from `.ico`/`.icns` included. Every icon change is journaled individually — revert one icon or the whole pack.

**Where Windows fights back (honest tiers):**

| Tier | Mechanism | Safety | Examples |
|---|---|---|---|
| **A. Native** | Documented API / registry | instant revert | accents, layout, icons, clock, Explorer options |
| **B. Documented mods** | Supported COM/shell APIs | revert + restart Explorer | classic context menu, some pane changes |
| **C. Injected mods** | Windhawk-style injection into `explorer.exe` | labelled risky, auto-disabled after a crash | translucent taskbar, custom Start layout |

Tier C is opt-in, clearly marked, never on by default, and the app refuses to enable two conflicting injected mods at once. No system file patching, ever.

---

## 3. Wallpaper engine

**Content types**

| Type | Formats / notes |
|---|---|
| Static | png/jpg/webp/avif, per-monitor, fit modes, time-of-day sets |
| Slideshow | playlists per monitor, order/shuffle, change on lock/unlock |
| Video | mp4/webm, loops, mute by default, hardware-decoded |
| Shader scenes | GPU-rendered (parallax, aurora, fluid, clouds, neon grid) — no video files, tiny on disk |
| Particle scenes | snow, rain, dust, fireflies — capped particle budget |
| Reactive | audio (mic or system loopback), music visualizer, "breathe with your CPU load" |
| HUD wallpaper | live stats rendered on the desktop (CPU/GPU/RAM/net, now playing) behind icons |
| Web | HTML5 canvas scenes (sandboxed, no network unless you allow the URL) |

**The performance governor (the reason to trust this one)**

| Rule | Default |
|---|---|
| Pause when a fullscreen game is running | on |
| Pause on battery | on |
| Pause when screen is off / locked | always |
| Pause when the desktop is fully covered by windows | on (detected via DWM) |
| FPS cap | 30 (choose 15 / 30 / 60) |
| GPU budget | ≤ 3% at idle; auto-fallback to a static frame if exceeded |
| Low-power mode | static still + optional slow zoom (0% GPU) |
| Thermals | stops animated wallpaper if the GPU is hot (no adding load to a hot GPU) |

**Cost meter (always visible in settings):** "Aurora Flow · 1.2% GPU · 0.2% CPU · paused in games". These are measured with real GPU counters, not estimates, and you can open a chart of the last hour.

**Multi-monitor:** different wallpaper per screen, spanning scenes, independent FPS caps, "static on side monitors, animated on main".

---

## 4. Widget layer — clocks, monitors, and the Rainmeter niche

**Widgets:** analog clock (many hands/styles), digital & flip clock, world clock, calendar, weather + forecast, CPU/GPU/RAM/disk gauges, network up/down, temperatures, fan speeds, audio visualizer, now playing, RSS/news, notes, todo, launcher dock, countdown, uptime, battery.

**Desktop editor:** drag to place, snap-to-grid + alignment guides, resize handles, layer order (above/below icons), click-through toggle, per-monitor placement, groups. Layout saves into the Theme file.

**Energy rules:** each widget declares a refresh rate (1 s / 5 s / 60 s / event-driven); all widgets pause when the desktop is covered; the whole layer can drop to 30 s updates on battery. A widget that needs a poll shows its cost.

**Rainmeter relationship (honest):** Rainmeter skins are `.ini` + plugins + sometimes C# code, so a full emulator is not realistic. Instead:
1. **Own widget engine** with import of simple `.ini`-style skins (measures + meters, common plugins: time, CPU, RAM, disk, net, weather, now playing).
2. **Coexistence**: detect Rainmeter and offer to keep it, or migrate compatible skins and hide the rest.
3. **Long term**: plugin SDK for widget authors (sandboxed WASM) so the ecosystem can grow without loading arbitrary DLLs.

**Preset packs:** "Minimal Pro", "Retro CRT", "Glass Desk", "Command Center" (dense HUD), "Zen" (clock + weather only).

---

## 5. Sounds, pointer, boot

Sound packs for system events, the app's own clicks/success/chime (all mutable, all local), startup sound, boot animation (Tier C), cursor packs, and font installation with previews. Each is a checkbox in the Theme.

---

## 6. The global performance governor

Every customization engine reports into one budget:

| Engine | Idle target | Auto behavior |
|---|---|---|
| Static theme (colors/icons/layout) | **0%** — applied once, no runtime | — |
| Widget layer | < 0.3% CPU @ 1 s refresh | pause when covered; 30 s on battery |
| Wallpaper (animated) | ≤ 3% GPU @ 30 fps, default | pause in games/locked/covered/battery |
| Terminal | 0% when not visible | suspend rendering when unfocused |
| Sounds | 0% | event-driven only |

Settings → **"My theme costs"**: a live reading with a one-slide "budget" control. If a theme exceeds the budget, the app tells you which part is expensive and offers the cheaper variant. That's the whole brand, applied to beauty: **no unmeasured cost**.

---

## 7. Gallery & sharing

- Export a Theme as `.pco` (zip: YAML + assets).
- Import shows **exactly what changes** (diff), flags Tier C parts, and requires explicit consent for injected mods.
- Community gallery = static files on a static site (publish via Vercel). No accounts, no server, no telemetry.
- Versioned themes with history: "your theme 3 weeks ago" is restorable.

---

## Roadmap for this suite

| Phase | Ships |
|---|---|
| v0.2 | Colors, accent, taskbar layout, clock panel, Explorer options, icon packs, Theme bundles |
| v0.3 | Terminal (tabs, split, themes, palette, history, paste guard), sounds |
| v0.4 | Wallpaper engine (static, slideshow, video, shader) + governor + cost meter |
| v0.5 | Widget layer + editor + import of simple skins |
| v1.x | Gallery, Tier C mods (opt-in), widget SDK |
