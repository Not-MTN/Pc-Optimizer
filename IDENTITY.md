# Keel

**A Windows 11 PC optimizer with an undo button for everything.**

---

## The name

A **keel** is the fin under a boat's hull. You never see it from the deck. It is
the only reason the boat does not capsize.

That is this app. The thing you interact with is a handful of switches; the thing
that makes it safe is a journal, a preflight simulation, a checksum chain and a
recovery path you will hopefully never notice. The name says where the work is.

It is also easy to say, easy to spell, one syllable, and it does not promise to
make your PC "blazing fast" — a promise every other optimizer breaks.

**Mark:** a lime fin hanging below a waterline, cut as one asymmetric sweep.
Two shapes, one idea. It survives at 16 px, which is the only test a Windows icon
really has to pass.

**Palette** (identical to the console, so the app is one object):

| Token | Hex | Used for |
|---|---|---|
| Ink | `#0B0D10` | background, the icon field |
| Lime | `#B6F24A` | the accent, the fin, primary actions |
| Mist | `#5A6478` | structure, the waterline, secondary text |

---

## The rules that shaped the product

1. **Undo before do.** Every change is journaled before the machine is touched,
   verified after, and reversible in one click. If the journal cannot be
   verified, the app goes read-only and says so.
2. **No cloud. Ever.** No account, no telemetry, no update pings. The console is
   one self-contained document that works with the network unplugged.
3. **No invented numbers.** An unmeasured startup item is "unattributed", never
   `0`. A driver with no crash history is "unknown", never "good". A disk forecast
   is arithmetic, or it is absent.
4. **Refuse loudly.** A request Windows 11 genuinely cannot honour — a
   transparent taskbar, a taskbar on the top of the screen — is declined by name
   with the reason. A toggle that silently does nothing is worse than a "no".
5. **Four tabs, not fourteen.** A security camera with forty dials is a security
   camera nobody configures. There are four places to be, plus a command palette
   for everything else.

---

## What it looks like

```
┌──────────────────────────────────────────────────────────┐
│  ◢ Home        ◧ Look        ⚡ Speed        ⚙ Fix         │
├──────────────────────────────────────────────────────────┤
│                                                          │
│   Health  92        Taskbar        Prompt                │
│   ▓▓▓▓▓▓▓▓░         ○ Left         ◉ GitHub              │
│                     ● Centre       (because a remote     │
│                                      said so)            │
└──────────────────────────────────────────────────────────┘
```

Four destinations:

| Tab | Answers | Contains |
|---|---|---|
| **Home** | "Is anything wrong?" | health ring, what changed, one Fix button |
| **Look** | "Make it mine" | taskbar, Explorer, icons, theme, prompt identity |
| **Speed** | "Make it faster" | checkup, cleaner, benchmark, startup, gaming |
| **Fix** | "Something broke" | driver grades, battery health, recovery, panic |

Everything else — 60+ verbs — lives in the palette (`Ctrl+K`). You never have to
hunt through a menu for `dns pause 30`.

---

## Honesty statement

Keel will tell you when it cannot do something. That is the design, not a
limitation:

- **Display / refresh-rate writes** are refused until verified on hardware. A
  wrong mode on the primary monitor can leave a black screen.
- **Per-process battery drain** and **window enumeration** return nothing rather
  than a guess, because the Windows counters behind them are not wired yet.
- **Charge limits** are unsupported everywhere: they are firmware-dependent, and
  claiming one without firmware access would be the most damaging lie in the app.
- **Startup deferral** refuses until the launcher that honours it exists.

Each refusal names what is missing. None of them silently no-op.
