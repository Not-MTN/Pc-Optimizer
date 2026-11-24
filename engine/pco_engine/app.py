"""Keel — the desktop entry point.

`server.main` is the developer console: bind a port, serve, wait for Ctrl+C.
This module is the *app*: it finds a free port, starts the engine on a thread,
opens a window, and shuts everything down when the window closes.

Ordering matters and is the reason this is not three lines of code:

1. The engine (migrations, journal recovery) must finish **before** the window
   renders, or the first thing a user sees is a half-migrated store.
2. The port is chosen by binding 127.0.0.1:0 rather than by trying a list of
   numbers; a fixed 7788 collides with the dev console and with a second copy.
3. A single-instance lock stops two copies from migrating the same database.
   The second copy raises the first window instead of corrupting the store.

No network: the server binds loopback only, and the console makes no external
requests. If pywebview is present it uses the Edge WebView2 runtime that ships
with Windows 11, so no browser is downloaded and no extra runtime is bundled.
Without pywebview it falls back to the default browser — the app still runs,
which matters on a machine where WebView2 has been removed.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_NAME = "Keel"
APP_ID = "Keel.PcOptimizer.1"


# --------------------------------------------------------------- environment
def data_dir() -> Path:
    """Where the store lives, per platform, without asking the user.

    Windows: %LOCALAPPDATA%\\Keel — the per-user, non-roaming location that the
    uninstaller can find and that never needs admin rights.
    """
    # PCO_DATA wins if it is already set: `main()` uses `setdefault` to publish
    # its choice, so honouring it here keeps ONE source of truth. Without this,
    # an explicitly set PCO_DATA was silently ignored and the app wrote to the
    # platform default anyway — including the crash log, which is the file you
    # most need to be in the place you were told to look.
    override = os.environ.get("PCO_DATA")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or "."
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def _port() -> int:
    """Ask the OS for a free loopback port, then release it for the server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(url: str, timeout: float = 30.0) -> bool:
    """Poll /api/health until the engine answers, so the window never shows 500."""
    import urllib.error
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return False


# ------------------------------------------------------------- single instance
def _already_running() -> bool:
    """A named mutex on Windows, an abstract socket elsewhere.

    Two copies migrating one SQLite file is a real corruption path, not a
    theoretical one. The loser exits quietly and focuses the winner's window.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.CreateMutexW(None, False, f"{APP_ID}.mutex")
            return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        except Exception:                      # noqa: BLE001 - never block launch
            return False
    return False


# ---------------------------------------------------------------- window
def _open_window(url: str, title: str) -> int:
    """Prefer a real window; degrade to the default browser; never fail to show."""
    try:
        import webview                          # type: ignore[import-not-found]
    except Exception:                           # noqa: BLE001 - optional dependency
        webbrowser.open(url)
        print(f"{title} → {url}", flush=True)
        return 0
    webview.create_window(title, url, width=1180, height=780,
                          min_size=(940, 620), background_color="#0B0D10")
    webview.start()
    return 0


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv:
        from pco_engine import __version__ as v
        print(f"{APP_NAME} {v}")
        return 0

    if _already_running():
        print(f"{APP_NAME} is already running — look for its window.", flush=True)
        return 0

    store = data_dir()
    try:
        store.mkdir(parents=True, exist_ok=True)
        # Touch a file rather than trusting mkdir: a directory can exist and
        # still be unwritable (a read-only mount, a locked-down profile, a
        # redirected AppData). Failing here is the difference between a clear
        # message now and a migration error thirty lines later.
        probe = store / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        # This is the failure a windowed app handles worst: console=False, so a
        # traceback goes nowhere and the user sees nothing at all. Say what is
        # wrong and where, in the one place they can still read — stderr if it
        # exists, and the log file if it does not.
        reason = (f"Keel cannot use its data folder:\n  {store}\n"
                  f"  {type(exc).__name__}: {exc.strerror or exc}\n\n"
                  "That folder is where the journal lives. Keel will not start "
                  "without it, because starting without the journal would mean "
                  "making changes it cannot undo.\n\n"
                  "Set PCO_DATA to a writable folder and try again, for example:\n"
                  "  setx PCO_DATA %LOCALAPPDATA%\\Keel")
        print(f"{APP_NAME}: {reason}", file=sys.stderr, flush=True)
        try:
            (Path(os.environ.get("TEMP") or ".") / "keel-startup-error.txt").write_text(
                reason, encoding="utf-8")
        except OSError:
            pass
        return 3
    os.environ.setdefault("PCO_DATA", str(store))

    # THE APP IS THE REAL APP.
    #
    # server._pick_backend() defaults to "demo" — a simulated machine — even on
    # Windows, on the reasoning that a browser tab you leave open should not be
    # able to change a real PC. That reasoning is right for someone running the
    # server by hand. It is exactly wrong for a double-clicked desktop app: the
    # one thing a user cannot be expected to know is to set an environment
    # variable before launching, so every install ran against a simulation and
    # every button was honest about a machine that does not exist.
    #
    # A person who installs Keel on Windows and opens it wants Keel to look at
    # the PC they are sitting at. PCO_BACKEND is still honoured if set, so the
    # demo remains one variable away.
    if sys.platform == "win32" and not os.environ.get("PCO_BACKEND"):
        os.environ["PCO_BACKEND"] = "windows"

    port = _port()
    url = f"http://127.0.0.1:{port}"

    # Import after the data dir is set: store.py resolves paths at import time.
    #
    # Absolute, not relative: this file is the frozen entry point, so it runs as
    # a top-level module where `from .` raises
    #   ImportError: attempted relative import with no known parent package
    # The package directory is already on sys.path (PyInstaller's `pathex`).
    from pco_engine import server as srv

    def serve() -> None:
        # A corrupt store raises from the first migration, on this thread. With
        # no handler the thread dies silently and _wait_ready below reports a
        # timeout — so the user sees "the engine did not start" with no reason,
        # when the real answer was "your database is damaged, here is its path".
        try:
            srv.main(["--host", "127.0.0.1", "--port", str(port)])
        except BaseException as exc:      # noqa: BLE001 - a dead thread is a dead app
            _ENGINE_ERROR.append(exc)
            _crash_log(exc)

    threading.Thread(target=serve, name="keel-engine", daemon=True).start()

    if not _wait_ready(url):
        # Say what actually happened where the thread told us; fall back to the
        # log path only when it really did just time out.
        if _ENGINE_ERROR:
            why = _ENGINE_ERROR[0]
            detail = f"{type(why).__name__}: {why}"
        else:
            detail = f"it did not answer within 20 seconds. See {store / 'keel.log'}"
        # A windowed exe has no stderr. Printing the reason was the same as
        # discarding it — the app vanished on launch and said nothing.
        print(f"{APP_NAME}: the engine stopped: {detail}",
              file=sys.stderr, flush=True)
        _tell_the_user(
            f"{APP_NAME} could not start",
            f"The engine did not start, so nothing was changed.\n\n{detail}\n\n"
            f"Details: {store / 'keel.log'}")
        return 1

    if "--no-window" in argv:
        print(f"{APP_NAME} → {url}", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    return _open_window(url + "/", APP_NAME)



def _tell_the_user(title: str, message: str) -> None:
    """Show a failure where a person will actually see it.

    The packaged exe is built with console=False, so `print(..., file=sys.stderr)`
    writes to a handle that does not exist: the window never appears, the process
    exits 1, and from the outside that is indistinguishable from a double-click
    that did nothing. Everything that can stop the app from starting has to end
    up in this function.

    Order matters. A native message box is best and needs no imports. If that
    fails (headless session, locked-down policy) the message is appended to a
    file whose path is on screen in the same directory a user is told to look.
    """
    try:
        if os.name == "nt":
            import ctypes
            # MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10 | 0x10000 | 0x40000)
            return
    except Exception:          # noqa: BLE001 - fall through to the file
        pass
    try:
        path = data_dir() / "startup-error.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{message}\n")
    except Exception:          # noqa: BLE001 - nothing left to try
        pass


# A list, not an attribute: the engine runs on its own thread and the main
# thread needs to know *why* it died in order to tell the user.
_ENGINE_ERROR: list[BaseException] = []


def _crash_log(exc: BaseException) -> None:
    """With console=False a frozen crash writes nowhere. The only clue is a
    dialog that says "Unhandled exception in script", and the details vanish
    when the user clicks Close. Put the traceback somewhere durable first."""
    try:
        path = data_dir() / "crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        import traceback
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            fh.write("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
    except Exception:      # noqa: BLE001 - logging must never mask the crash
        pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:      # noqa: BLE001 - last line of defence
        _crash_log(exc)
        # Re-raising in a windowed build shows "Unhandled exception in script"
        # with a traceback, or for pywebview nothing at all. Say it plainly.
        _tell_the_user(f"{APP_NAME} stopped",
                       f"{type(exc).__name__}: {exc}\n\n"
                       f"A crash log was written to {data_dir() / 'crash.log'}")
        raise
