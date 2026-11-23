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

### 1. Cleaning — with a receipt
A vacuum cleaner that shows you what it picked up. It scans your disk, shows you a map of what's taking up space, and sorts everything into three piles:

- **Safe to delete** — temporary files, leftover installers, crash logs, old update junk.
- **Your call** — things that have a tradeoff (like the hibernation file, or old Windows backups). Each one comes with a one-line explanation.
- **Never touch** — your documents, game saves, browser data. It doesn't even list them.

After cleaning, you get a **receipt**: how much space came back, what was skipped, and why. Deleted files go to a holding box for 30 days rather than vanishing — so undo is real, not a promise.

**What it refuses to do:** the classic "registry cleaning" that every optimizer app sells. It's mostly theater and occasionally breaks things. Also no fake "RAM boosters" and no deleting things that Windows actually needs to start quickly.

### 2. Gaming mode — a session, not a switch
You press start, it prepares the PC, and — this is the important part — it **measures** while you play. When you stop, it shows you an honest scorecard: did frame times get smoother? Did stutter drop? Did average speed change at all?

If the answer is "basically no difference," it says so. That sounds like bad marketing, but it's the reason you'd trust the app when it says something *did* help.

Everything it changed is put back exactly as it was when the session ends. And it knows that some games use anti-cheat software that dislikes deep system tools — so it steps aside for those games instead of getting you banned.

### 3. Ad and tracker blocking — for every app, not just your browser
Browser extensions only clean up the browser. This blocks ads and trackers at the **front door of your internet connection**, so every app benefits: games, launchers, the store, apps that phone home quietly.

It also shows you a friendly list of "who your PC talks to" and lets you decide what's allowed. If a website or app breaks because something got blocked, it offers to allow that one thing for that one app — instead of leaving you with a mystery login error.

There's an honest toggle for things like Windows' own background reports, each explained in plain words ("safe to block" / "this may stop X from working").

### 4. Startup manager — with explanations, not guesswork
Shows you everything that wakes up when you log in — apps, background services, scheduled jobs — and how much time each one costs you at startup.

Every item gets a plain-English card: what it is, who made it, and **what breaks if you turn it off**. If it doesn't know, it says "unknown" and shows you the facts (publisher, location, whether it talks to the internet) instead of guessing.

Better yet: often the right fix isn't "off," it's "later." So it can hold something back for a minute instead of disabling it entirely. And it notices when an app secretly re-adds itself to startup, and tells you who did it.

### 5. One search box for everything ("Personal CMD")
Press a hotkey and a search box appears. Type and you can do anything:

- `clean` — clean the PC
- `game` — start a gaming session
- `snap` — save your current setup as a restore point
- Anything you create yourself: your own shortcuts, scripts, and commands with your own names.

You can also record a sequence of actions and save it as a single command — so a routine like "start my work setup" becomes one word. Commands run **inside** the app, so nothing flashes a black console window, nothing hangs, and it opens instantly.

### 6. Make it yours — taskbar, terminal, explorer, the whole vibe
Your entire personal setup lives in **one file**: taskbar layout, colors, clock format, file explorer behavior, terminal aliases, keyboard shortcuts, sounds. That file can live in your own git repo, be shared with a friend, or be applied to a fresh install.

The magic flow: you reinstall Windows, run the app, point it at your profile, and your PC becomes *yours* again in about 15 minutes instead of a weekend.

If Windows genuinely can't do something you want (some visual tweaks aren't officially supported), the app is honest about it and marks it as "risky mod" rather than pretending it's a normal setting.

### 7. Sandbox and "installer X-ray" (the special one)
Before you run any installer, you can put it under observation. The app records **everything** it does: files created, Windows settings changed, background services added, internet addresses contacted.

You then get a report and two buttons: **Keep** or **Erase completely**. No virtual machine, no full backup of your whole disk, no "sorry, the uninstaller left 400 files behind."

For genuinely dangerous files there's also true sandboxing (running the app in a sealed box where it can't touch your real files) — including Windows' built-in sandbox, and simpler "throwaway profile" modes for normal apps.

### 8. Privacy you can verify
"No data leaves your PC" is easy to say. Here it's checkable: the background engine literally **has no internet access by default** — not a setting, a firewall rule you can inspect. The only thing that ever connects is an update checker, and it's off unless you press it.

There's a "prove it" button that shows you the actual firewall rules protecting the app, plus a ledger of every address each app on your PC has contacted.

### 9. Guardian — the safety net (why full access is OK)
This is the most important part, and the least glamorous.

- Every change is **written in a journal** before it happens, along with exactly how to reverse it.
- Risky changes ask "keep this?" — and if you don't answer or the PC misbehaves, they undo themselves automatically.
- Before a risky change, it saves a **snapshot** of your settings — small and fast (seconds to save, seconds to restore), not the slow, unreliable System Restore.
- If the PC fails to start properly, the app **fixes it itself on the next boot** and reports what happened.
- There's a **panic key**: one shortcut that undoes everything and puts the PC back to a known-good state. It works even if the app's window won't open.
- It watches for **drift** — Windows updates secretly undoing your tweaks — and tells you what changed rather than silently losing your setup.

Put simply: full access, but **you can never get stuck.**

### 10. Time machine, health, and the file manager
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
