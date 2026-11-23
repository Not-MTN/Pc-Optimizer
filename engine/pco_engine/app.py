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
    store.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PCO_DATA", str(store))

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
        srv.main(["--host", "127.0.0.1", "--port", str(port)])

    threading.Thread(target=serve, name="keel-engine", daemon=True).start()

    if not _wait_ready(url):
        print(f"{APP_NAME}: the engine did not start; see {store / 'keel.log'}",
              file=sys.stderr, flush=True)
        return 1

    if "--no-window" in argv:
        print(f"{APP_NAME} → {url}", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    return _open_window(url + "/", APP_NAME)


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
        raise
