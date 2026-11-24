# The Simple Guide

> Everything this app is and will be, in plain language. No jargon. If a word is technical, it gets explained the first time it appears.

---

## What is this?

One app that keeps your Windows PC **fast, clean, safe, and set up the way you like it** — without slowing the PC down itself.

Most optimizer apps promise a lot and either (a) do nothing real while showing you scary red warnings, or (b) change things and can't undo them, so one wrong click means a broken PC.

This one is built on two simple promises:

1. **It never lies to you.** If it says something got faster, it measured it. If nothing changed, it says "nothing changed."
2. **You can always undo.** Every change is written down. One button takes it back. If the PC ever fails to start properly, it fixes itself automatically.

That's the whole idea. Everything below is a detail of those two promises.

---

## The two parts of the app

Think of it like a **car with a dashboard and an engine**:

- **The engine** — a tiny background program that does the actual work: cleaning, measuring, watching, blocking ads. It's built to be almost invisible. Think "lighter than a single browser tab." When you're not asking it to do something, it uses **zero** processor power. It doesn't poll or check things constantly (that's what makes most "optimizer" apps worse than useless).
- **The window** — the part you see. It opens when you want it, looks good, and closes when you're done. When it's closed, it's not running. Not "minimized" — actually gone.

**Nim** is the little robot face. He's not decoration: his antenna pulses faster when your processor is busy, his eyes change color as the PC gets hot, he sleeps when the PC is idle, and he frowns when a test shows something got *worse*. He can't lie about the PC's condition because he *is* the condition display.

---

## What it actually does

### 1. PC Checkup — one scan, one button, everything fixed
This is the first screen. One scan looks at your whole PC and puts every problem into **three separate lists** — because lumping them together is how optimizer apps lie to you:

| List | What it means |
|---|---|
| **Slowing you down** | Things that actually affect speed (a monitor running at 60 Hz when it can do 165, 21 startup programs, a hot CPU throttling itself, a 94%-full drive) |
| **Taking up space** | Disk space, not speed — junk, **every duplicate file**, old installers, stale big files |
| **Needs attention** | Safety and updates — outdated apps with known exploits, firewall holes, encryption off, apps allowed to use your mic in the background |

Every item explains itself in one line, shows the evidence behind it, and says what the fix does. Then one button: **Fix everything safe**. It shows the plan first, runs it as one batch, gives one receipt and one undo. Anything needing your judgment goes to a review list instead of being done silently.

There's also a **score** — Speed, Security, Privacy, Reliability — and clicking it shows exactly which findings created that number. Never a magic number. A **weekly report** tells you what changed and what Windows quietly reverted behind your back.

### 2. Cleaning — with a receipt
A vacuum cleaner that shows you what it picked up. It scans your disk, shows you a map of what's taking up space, and sorts everything into three piles:

- **Safe to delete** — temporary files, leftover installers, crash logs, old update junk.
- **Your call** — things that have a tradeoff (like the hibernation file, or old Windows backups). Each one comes with a one-line explanation.
- **Never touch** — your documents, game saves, browser data. It doesn't even list them.

After cleaning, you get a **receipt**: how much space came back, what was skipped, and why. Deleted files go to a holding box for 30 days rather than vanishing — so undo is real, not a promise.

**Duplicates, done properly.** It finds every duplicate file — not by name, but by reading the actual contents, so renamed copies are caught too. Nearly identical photos are grouped together. You pick a rule (keep newest / keep the one in my folder / ask me each time), preview everything side by side, and deleted files still go to the 30-day holding box.

**What it refuses to do:** the classic "registry cleaning" that every optimizer app sells. It's mostly theater and occasionally breaks things. Also no fake "RAM boosters" and no deleting things that Windows actually needs to start quickly.

### 3. Gaming mode — a session, not a switch
You press start, it prepares the PC, and — this is the important part — it **measures** while you play. When you stop, it shows you an honest scorecard: did frame times get smoother? Did stutter drop? Did average speed change at all?

If the answer is "basically no difference," it says so. That sounds like bad marketing, but it's the reason you'd trust the app when it says something *did* help.

Everything it changed is put back exactly as it was when the session ends. And it knows that some games use anti-cheat software that dislikes deep system tools — so it steps aside for those games instead of getting you banned.

**The Gaming Coach (the big one).** While you play it watches quietly — frame times, GPU and CPU load, temperatures, memory, disk, network, stutter — without ever touching the game. Then it tells you exactly what holds you back, in plain words:

- *"Your graphics card is maxed out and your processor is bored — lowering visual settings is the only thing that will help."*
- *"One processor core is maxed out — turn down draw distance, not textures."*
- *"Your card overheats after 9 minutes and slows itself down — fan settings matter more than any game setting."*

From that it suggests **actual game settings** — `Shadows: High → Medium, +6%` — with a confidence level and the reason. Two things make it real:

- **Screenshot reading:** open the game's settings menu, press a hotkey, and it reads your current settings off the screen (locally, nothing uploaded). No typing.
- **90-second A/B test:** records the same scene twice with different settings and says which actually won. If it's too close to call, it says **"no measurable difference"** and leaves your settings alone.

**Sensitivity Lab.** Finding the right mouse sensitivity is usually folklore. There's actual math behind it: every game has a fixed "yaw" value, so the one number that means the same thing everywhere is **cm/360** — how many centimetres you move the mouse to spin exactly once. So the app:

1. **Measures yours** with a short practice drill (about 5 minutes). It tests three speeds, measures how often you overshoot versus fall short, and fits the shape of that tradeoff. Too fast overshoots; too slow falls short; the best speed is the bottom of the curve. It gives you a range and a confidence level — and if your current setting is already there, it tells you *not* to change anything.
2. **Converts it to every game** — Valorant 0.312, CS2 0.99, Apex 0.99, Fortnite 3.94 — all the same physical movement.
3. **Covers games nobody measured** with a 10-second "360° capture": turn once in any game with a hotkey held, and it calculates that game's exact value from raw mouse counts.
4. **Watches you play (only if you turn it on)** and notices "you overshoot 2.3× more here — go 10% slower in this game," using raw input only. No reading the game's memory, no injecting anything, and it never touches anti-cheat games unless you allow that game.

The number is yours, not a pro's — and it's the reason your aim carries over between games instead of resetting.

Plus an optional on-screen overlay (fps, frame times, temps), per-game history ("14% smoother since that power change"), library checks, and saved profiles.

### 4. Ad and tracker blocking — for every app, not just your browser
Browser extensions only clean up the browser. This blocks ads and trackers at the **front door of your internet connection**, so every app benefits: games, launchers, the store, apps that phone home quietly.

It also shows you a friendly list of "who your PC talks to" and lets you decide what's allowed. If a website or app breaks because something got blocked, it offers to allow that one thing for that one app — instead of leaving you with a mystery login error.

There's an honest toggle for things like Windows' own background reports, each explained in plain words ("safe to block" / "this may stop X from working").

**And you control it completely:** a big on/off switch wherever you are (plus a hotkey and the tray icon), a **Pause 30 minutes** button with a countdown, and per-app rules — strict for browsers, permissive for a game that won't connect. Off puts normal internet back instantly; on re-arms in under a second. Never a half-state.

### 5. Startup manager — with explanations, not guesswork
Shows you everything that wakes up when you log in — apps, background services, scheduled jobs — and how much time each one costs you at startup.

Every item gets a plain-English card: what it is, who made it, and **what breaks if you turn it off**. If it doesn't know, it says "unknown" and shows you the facts (publisher, location, whether it talks to the internet) instead of guessing.

Better yet: often the right fix isn't "off," it's "later." So it can hold something back for a minute instead of disabling it entirely. And it notices when an app secretly re-adds itself to startup, and tells you who did it.

### 6. One search box for everything ("Personal CMD")
Press a hotkey and a search box appears. Type and you can do anything:

- `clean` — clean the PC
- `game` — start a gaming session
- `snap` — save your current setup as a restore point
- Anything you create yourself: your own shortcuts, scripts, and commands with your own names.

You can also record a sequence of actions and save it as a single command — so a routine like "start my work setup" becomes one word. Commands run **inside** the app, so nothing flashes a black console window, nothing hangs, and it opens instantly.

### 7. Make it yours — the Desktop Studio
Your entire personal setup lives in **one file**: taskbar layout, colors, clock format, file explorer behavior, terminal aliases, keyboard shortcuts, sounds. That file can live in your own git repo, be shared with a friend, or be applied to a fresh install.

The magic flow: you reinstall Windows, run the app, point it at your profile, and your PC becomes *yours* again in about 15 minutes instead of a weekend.

**A custom terminal, not a reskin.** Tabs, split panes, blur, any theme, clickable paths, a command palette inside it, fuzzy history search, plain-English hints for dangerous commands, a warning before you paste something destructive, and a "dry-run first" offer on anything that deletes. It can also run the app's own actions (`pco clean`, `pco game start`).

**Taskbar and Windows looks.** Colors, opacity, blur, floating style, icon size and spacing, a clock with seconds/weather/music, tray behavior, multi-monitor setups, corner radius, Start menu, Explorer behavior.

**App icons.** Whole icon packs, or swap a single app's icon. Revert one, or revert all.

**A wallpaper engine.** Still images, slideshows, video, generated animated scenes (aurora, rain, parallax — tiny file size), walls that react to your music, and a live desktop HUD showing stats behind your icons. The crucial part: it **pauses in games, on battery, when the screen is locked, and when windows cover the desktop**, and shows its real cost ("1.2% graphics card") with a chart. If it ever costs too much, it tells you which part is expensive.

**Widgets — clocks, gauges and Rainmeter-style desktops.** Analog and flip clocks, world clocks, calendar, weather, CPU/GPU/RAM/disk/network gauges, temperatures, audio visualizer, now playing, notes, launchers. Drag them anywhere, snap to a grid, per monitor. Each declares how often it updates so nothing polls needlessly. Simple Rainmeter skins can be imported, and Rainmeter itself can keep running alongside if you prefer.

**All of it lives in one Theme file** — colors, taskbar, icons, wallpaper, widgets, terminal, sounds — so you can export it, share it, put it on GitHub, or rebuild your desktop after a reinstall in one click.

If Windows genuinely can't do something you want (some visual tweaks aren't officially supported), the app is honest about it and marks it as "risky mod" rather than pretending it's a normal setting.

### 8. Sandbox and "installer X-ray" (the special one)
Before you run any installer, you can put it under observation. The app records **everything** it does: files created, Windows settings changed, background services added, internet addresses contacted.

You then get a report and two buttons: **Keep** or **Erase completely**. No virtual machine, no full backup of your whole disk, no "sorry, the uninstaller left 400 files behind."

For genuinely dangerous files there's also true sandboxing (running the app in a sealed box where it can't touch your real files) — including Windows' built-in sandbox, and simpler "throwaway profile" modes for normal apps.

### 9. Privacy you can verify
"No data leaves your PC" is easy to say. Here it's checkable: the background engine literally **has no internet access by default** — not a setting, a firewall rule you can inspect. The only thing that ever connects is an update checker, and it's off unless you press it.

There's a "prove it" button that shows you the actual firewall rules protecting the app, plus a ledger of every address each app on your PC has contacted.

### 10. Guardian — the safety net (why full access is OK)
This is the most important part, and the least glamorous.

- Every change is **written in a journal** before it happens, along with exactly how to reverse it.
- Risky changes ask "keep this?" — and if you don't answer or the PC misbehaves, they undo themselves automatically.
- Before a risky change, it saves a **snapshot** of your settings — small and fast (seconds to save, seconds to restore), not the slow, unreliable System Restore.
- If the PC fails to start properly, the app **fixes it itself on the next boot** and reports what happened.
- There's a **panic key**: one shortcut that undoes everything and puts the PC back to a known-good state. It works even if the app's window won't open.
- It watches for **drift** — Windows updates secretly undoing your tweaks — and tells you what changed rather than silently losing your setup.

Put simply: full access, but **you can never get stuck.**

### 11. Time machine, health, and the file manager
- **Time machine** — settings are snapshotted regularly, so you can ask "what changed on Tuesday?" and restore just that piece.
- **Health** — disk wear, temperatures, battery ageing, crashes, and "your PC got slower this month, here's the likely reason."
- **File manager** — not a replacement for Windows Explorer, a companion: tabs, fast search, bulk renaming, duplicate finding, "what's eating this folder," and rules like "move downloads older than 30 days."

---

## Things it will never do

- Fake "speed up your PC by 300%" numbers.
- Clean the registry and act like it saved you.
- Send your data anywhere, ever.
- Hide bad news. "No measurable change" is a valid, frequent result.
- Change something it can't undo.
- Argue with your accessibility settings (if you ask for less animation, everything stops instantly).

---

## How it's built (short version)

- The engine is written in a language chosen for speed and safety (Rust); the window uses Microsoft's modern native interface tools. That combination is what makes it beautiful **and** light. Animations are handled by the graphics card — the kind of animation that costs nothing while it's standing still.
- It's split into permission levels, like a building with different keys: things that need no special help, things that need administrator access, and optional deep system access that is **off by default, loads only when needed, and unloads itself.**
- It runs on Windows 11 only, on purpose — one target done properly instead of two done halfway.

---

## Where it goes over time

1. **First version** — the safety net, the cleaner, the search box, and an honest dashboard. The most-used things, done excellently.
2. **Second** — gaming sessions with real measurements, the startup manager, and your personal setup file.
3. **Third** — the ad blocker, privacy tools, and the network view.
4. **Later** — the installer X-ray, sandbox options, and the community side of setup profiles.
5. **Someday, only if it earns it** — a live desktop overlay, a local "why is my PC slow?" assistant that can only answer from real measurements, and a plugin system for people who want to extend it.

---

## One sentence

**A PC optimizer that tells you the truth, shows you the proof, and can undo anything — while using less power than the things it turns off.**
