Keel 0.5.0
==========

A Windows 11 PC optimizer with an undo button for everything.

WHAT IT IS
----------
Four screens: Home, Look, Speed, Fix. Everything else is in the command
palette (Ctrl+K). Every change is written to a journal first, verified after,
and undone in one click. There is no cloud, no account and no telemetry —
the app never makes a network connection.

FIRST RUN
---------
* Keel installs per-user, so it never needs administrator rights.
* Your data lives in %LOCALAPPDATA%\Keel. Back that folder up and you have
  backed up everything: the journal, the settings, the undo history.
* Press Ctrl+K for the palette if you cannot find something. That is the
  intended way to use the app, not a hidden extra.

WHAT IT WILL NOT DO
-------------------
Keel refuses, by name and with a reason, the things Windows 11 genuinely
does not allow. Today that means:

  * a transparent or blurred taskbar
  * the taskbar on the top or side of the screen
  * labels on the primary taskbar (secondary displays only)
  * display mode and refresh-rate writes in this build
  * charge limits on hardware whose firmware does not expose one

A refusal is a promise kept. A toggle that appears to work and silently does
nothing is worse than a "no".

IF SOMETHING GOES WRONG
-----------------------
Open Fix → Recovery. The journal verifies itself with a hash chain on every
launch; if it cannot, the app opens read-only and tells you why rather than
letting you make changes it cannot undo.

Uninstalling keeps %LOCALAPPDATA%\Keel unless you tick the box. That is
deliberate: your undo history is not the installer's to delete.
