"""Local HTTP bridge + console.

Design constraints that shaped this file:

* stdlib only — no dependency can break the app before it starts;
* binds 0.0.0.0 so the sandbox preview can reach it, but the *UI* only ever uses
  relative URLs, so it works identically behind a proxy or on 127.0.0.1;
* every response is JSON with the exact keys the future WinUI shell consumes;
* the whole UI is one document with inline CSS/JS/SVG because the preview iframe
  has no network: a CDN font or icon would render as a blank box.

Run:  python -m pco_engine.server --port 7788
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import os
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs as urlparse_qs
from urllib.parse import urlparse

from . import __version__
from .backend import BackendError
from .display import DisplayError
from .engine import EngineError
from .journal import JournalError
from .recipes import RecipeError
from .store import StoreError
from .absent import Absent
from .service import Service

class _RWLock:
    """One writer at a time; any number of readers alongside each other.

    There used to be a single lock around every service call, which meant one
    slow operation stopped the whole app. Rendering a wallpaper takes five
    seconds, and /api/status -- the call the window makes on a timer, the one
    that feeds the machine pill and every counter -- blocked behind it for four
    and a half of those seconds. The UI froze for the length of any render.

    SQLite allows concurrent readers and serialises writers itself, so the only
    thing a process-level lock has to add is protection against two writers
    racing on one connection.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting = 0

    def read(self):
        return _Guard(self, write=False)

    def write(self):
        return _Guard(self, write=True)


class _Guard:
    def __init__(self, lock, *, write):
        self.lock = lock
        self.write = write

    def __enter__(self):
        c = self.lock._cond
        with c:
            if self.write:
                self.lock._waiting += 1
                while self.lock._writer or self.lock._readers:
                    c.wait()
                self.lock._waiting -= 1
                self.lock._writer = True
            else:
                # A waiting writer holds back new readers, so a steady stream of
                # status polls cannot starve a mutation.
                while self.lock._writer or self.lock._waiting:
                    c.wait()
                self.lock._readers += 1

    def __exit__(self, *exc):
        c = self.lock._cond
        with c:
            if self.write:
                self.lock._writer = False
            else:
                self.lock._readers -= 1
            c.notify_all()


_LOCK = _RWLock()

# The console is served by this process, so "same origin" is 127.0.0.1 or
# localhost on whatever port we bound. Anything else is a web page that has no
# business talking to a machine-management API.
_LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}

_INIT_LOCK = threading.Lock()     # guards one-time service construction
_SVC: Service | None = None


def _pick_backend() -> Any:
    """Which machine the console is looking at.

    Defaults to **demo**, deliberately, even on Windows. The CLI is a person
    typing a command; a browser tab is something you leave open. Anything that
    can change a real machine has to be started with `PCO_BACKEND=windows` (or
    `run.ps1 -Real`, which prints the doctor report first).

    `PCO_BACKEND=windows` refuses to start on a non-Windows OS rather than
    pretending to be one.
    """
    mode = (os.environ.get("PCO_BACKEND") or "demo").strip().lower()
    if mode == "demo":
        return None                      # Service builds its own FakeBackend
    if mode not in ("windows", "auto"):
        # A typo used to fall through to `return None`, which is the simulator.
        # Nobody setting PCO_BACKEND=windwos wants a fake machine: they want an
        # error they can read and fix.
        raise SystemExit(
            f"PCO_BACKEND={mode!r} is not a backend. Use 'windows', 'auto' or "
            f"'demo'. Refusing to start rather than silently simulate.")
    if mode == "auto" and sys.platform != "win32":
        raise SystemExit(
            "PCO_BACKEND=auto on a non-Windows machine: there is no real backend "
            "to drive. Use 'demo' if you meant the simulator.")
    # mode is "windows", or "auto" which is only still reachable on win32.
    if sys.platform != "win32":
        raise SystemExit(
            f"PCO_BACKEND={mode!r} needs Windows. This is {sys.platform!r}.")
    from .windows_backend import WindowsBackend
    return WindowsBackend(
        allow_display=os.environ.get("PCO_WINDOWS_DISPLAY") == "1")


def _service() -> Service:
    """Build the service once, under a lock.

    Without the lock two request threads arriving together both saw `_SVC is
    None` and both ran the full migration path against one SQLite file. The
    per-request `_LOCK` below does not help: this happens before any handler
    takes it. A double-locked check costs nothing and removes the race.
    """
    global _SVC
    if _SVC is None:
        with _INIT_LOCK:
            if _SVC is None:
                data = os.environ.get("PCO_DATA") or str(Path.cwd() / "pco_data")
                os.makedirs(data, exist_ok=True)
                catalog = os.environ.get("PCO_CATALOG") or str(
                    Path(__file__).resolve().parent.parent / "catalog")
                _SVC = Service(os.path.join(data, "pco.db"),
                               catalog_dir=catalog,
                               vault_root=os.path.join(data, "vault"),
                               backend=_pick_backend())
    return _SVC


ROUTES: dict[str, str] = {
    "/api/status": "status",
    "/api/elevate": "elevate",
    "/api/themes": "theme.gallery",
    "/api/themes/get": "theme.get",
    "/api/themes/validate": "theme.validate",
    "/api/themes/preview": "theme.preview",
    "/api/themes/apply": "theme.apply",
    "/api/themes/apply-doc": "theme.apply_doc",
    "/api/themes/export": "theme.export",
    "/api/wallpaper/save": "wallpaper.save",
    "/api/wallpaper/apply": "wallpaper.apply",
    "/api/display": "display.mode",
    "/api/display/set": "display.set",
    "/api/display/confirm": "display.confirm",
    "/api/display/revert": "display.revert",
    "/api/checkup": "checkup.run",
    "/api/checkup/fix": "checkup.fix_safe",
    "/api/clean/scan": "clean.scan",
    "/api/clean/run": "clean.run",
    "/api/bench/run": "bench.run",
    "/api/dns/on": "dns.on",
    "/api/dns/off": "dns.off",
    "/api/dns/toggle": "dns.toggle",
    "/api/dns/pause": "dns.pause",
    "/api/dns/lists": "dns.lists",
    "/api/dns/list/set": "dns.list.set",
    "/api/drift": "drift.check",
    "/api/report": "report.weekly",
    "/api/checkup/apply": "checkup.fix",
    # One named finding. Separate from /api/checkup/fix, which has meant
    # "fix everything safe" since the first release — redefining it broke
    # the documented contract and the bridge test that pins it.
    "/api/checkup/fix-one": "checkup.fix_one",
    "/api/studio/themes": "studio.themes",
    "/api/spotify/looks": "spotify.looks",
    "/api/spotify/write": "spotify.write",
    "/api/spotify/export": "spotify.export",
    "/api/games": "game.sessions",
    "/api/verify": "verify",
    "/api/startup": "startup.inspect",
    "/api/startup/apply": "startup.apply",
    "/api/space": "space.grew",
    "/api/space/forecast": "space.forecast",
    "/api/space/sample": "space.sample",
    "/api/dupes": "dupes.scan",
    "/api/ask": "ask",
    "/api/report/md": "report.md",
    "/api/report/json": "report.json",
    "/api/power": "power.summary",
    "/api/power/sample": "power.sample",
    "/api/power/drains": "power.drains",
    "/api/workspaces": "workspace.list",
    "/api/drivers": "drivers.list",
    "/api/recipes": "recipe.list",
    "/api/safety": "safety.pending",
    "/api/boot": "boot.status",
    "/api/shell": "shell.options",
    "/api/shell/preview": "shell.preview",
    "/api/shell/apply": "shell.apply",
    "/api/shell/themes": "shell.themes",
    "/api/shell/theme/save": "shell.theme.save",
    "/api/shell/theme/import": "shell.theme.import",
    "/api/shell/prompt": "shell.prompt",
    "/api/workspace/capture": "workspace.capture",
    "/api/workspace/delete": "workspace.delete",
    "/api/workspace/restore": "workspace.restore",
    "/api/drivers/block": "drivers.block",
    "/api/drivers/rollback": "drivers.rollback",
    "/api/drivers/unblock": "drivers.unblock",
    "/api/drivers/before-install": "drivers.before_install",
    "/api/recipe/parse": "recipe.parse",
    "/api/recipe/create": "recipe.create",
    "/api/recipe/toggle": "recipe.toggle",
    "/api/recipe/fire": "recipe.fire",
    "/api/recipe/delete": "recipe.delete",
    "/api/safety/panic": "safety.panic",
    "/api/safety/confirm": "safety.confirm",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "pco/0.2"

    # ------------------------------------------------------------------ plumbing
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # No wildcard CORS. This was `Access-Control-Allow-Origin: *`, which let
        # any page you happened to visit read every response from localhost —
        # your hardware inventory, your scan results, your change journal.
        # The console is served from this same origin, so it needs nothing.
        origin = self.headers.get("Origin")
        if origin and origin in self._origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
        if " 500 " in (fmt % args):
            super().log_message(fmt, *args)

    def _cross_origin(self) -> bool:
        """Is this request coming from a page on another site?

        `Origin` is sent by browsers on cross-origin POSTs and, since 2020, on
        many GETs too. A same-origin fetch from our own console sends either no
        Origin or a localhost one, so the rule is simply: if an Origin is
        present and it speaks for a site we did not serve, refuse.
        """
        origin = self.headers.get("Origin")
        if not origin:
            return False
        if origin in self._origins():
            return False
        # A file:// page (the packaged app used to use one) sends Origin: null.
        # That is not a site we serve, and nothing legitimate needs it.
        return True

    def _origins(self) -> set[str]:
        """Local origins for the port this request actually arrived on."""
        try:
            port = str(self.server.server_address[1])
        except (AttributeError, IndexError, TypeError):
            port = os.environ.get("PORT", "7788")
        return {f"http://{h}:{port}" for h in _LOCAL_HOSTNAMES} | \
               {f"https://{h}:{port}" for h in _LOCAL_HOSTNAMES}

    def _refuse_cross_origin(self) -> bool:
        if not self._cross_origin():
            return False
        self._json(403, {"error": "cross-origin request refused: Keel only talks "
                                  "to its own window"})
        return True

    def do_OPTIONS(self) -> None:  # noqa: N802
        if self._cross_origin():
            self._json(403, {"error": "cross-origin request refused"})
            return
        self._send(204, b"", "text/plain")

    # --------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        if self._refuse_cross_origin():
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path in {"/", "/index.html"}:
            html = (Path(__file__).resolve().parent / "console.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path.startswith("/api/themes/tile/"):
            theme_id = path[len("/api/themes/tile/"):]
            try:
                png = _service().wallpaper_preview_png(theme_id, width=360)
            except KeyError:
                self._json(404, {"error": f"no theme {theme_id!r}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(png)
            return
        if path.startswith("/api/themes/full/"):
            theme_id = path[len("/api/themes/full/"):]
            try:
                from . import themes as _t
                png = _service().wallpaper_png(_t.get(theme_id))
            except (KeyError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
        if path.startswith("/api/spotify/preview/"):
            look_id = path[len("/api/spotify/preview/"):]
            # keep_blank_values matters: `?w=` is a caller who passed an empty
            # width, and parse_qs drops it by default, which made it
            # indistinguishable from "no w at all" and quietly used the default.
            # No `if v` filter: that dropped the blank value this whole change
            # is about, so keep_blank_values had no effect at all.
            q = {k: v[0] for k, v in
                 urlparse_qs(parsed.query, keep_blank_values=True).items()}
            # A width that is not a number is a caller mistake, and bad input is
            # reported, not silently replaced. The old code fell back to 720 for
            # `w=abc` *and* quietly clamped `w=0` to 240, so a caller who asked
            # for something impossible got a plausible-looking answer instead of
            # a correction — the same class of bug as silently returning nothing.
            raw_w = q.get("w", "720").strip()
            if not raw_w:
                # `?w=` and `?w=%20` mean "I passed a width and it is empty",
                # which is a mistake. Falling back to 720 here is the same
                # silent-substitution bug the clamp used to be.
                self._json(400, {"error": "w was given but is empty"})
                return
            try:
                width = int(raw_w)
            except (TypeError, ValueError):
                self._json(400, {"error": f"w must be a whole number, got {raw_w[:20]!r}"})
                return
            if not (240 <= width <= 1440):
                self._json(400, {"error": f"w must be between 240 and 1440, got {width}"})
                return
            try:
                png = _service().spotify_preview_png(look_id, width)
            except ValueError as exc:
                # Unknown look *and* a malformed id both arrive here now that the
                # service validates types. This used to catch KeyError only, so
                # tightening the validation turned a clean 404 into a 500 — which
                # is exactly the kind of regression a fuzz test is for.
                self._json(404, {"error": str(exc)})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            # A look is immutable data; the bytes never change for a given URL.
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(png)
            return
        if path == "/api/report/html":
            try:
                html = _service().health_report("html").encode("utf-8")
                self._send(200, html, "text/html; charset=utf-8")
            except Exception as exc:  # noqa: BLE001
                ref = f"keel-{uuid.uuid4().hex[:12]}"
                _log_exception(ref, exc)
                self._json(500, {"error": "Keel hit an internal error and did "
                                          "not change anything.", "ref": ref})
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "version": __version__,
                             "backend": ("windows" if _SVC is not None
                                         and getattr(_SVC.backend, "environment", "") == "windows"
                                         else os.environ.get("PCO_BACKEND", "demo"))})
            return
        try:
            svc = _service()
            if path in ROUTES:
                # Query parameters are passed through. Without this, every
                # parameterised GET router ("theme.get": theme_id) raised a
                # KeyError and returned 500 — the console asked, the server had
                # the answer, and the wire between them dropped the argument.
                if self._is_mutating(ROUTES[path]):
                    self._json(405, {"error": f"{ROUTES[path]} changes state; "
                                              "POST it to this same path",
                                     "path": path})
                    return
                query = {k: v[0] for k, v in urlparse_qs(parsed.query).items() if v}
                # Read lock: every mutating verb was refused a few lines up, so
                # everything reachable here is a query. A slow render in another
                # request must not freeze the status poll that feeds the window.
                with _LOCK.read():
                    # run_action defaults args to {}, so a verb that takes nothing
                    # needs no special case here.
                    result = svc.run_action(ROUTES[path], query)
                self._json(200, result)
                return
            if path == "/api/sens":
                with _LOCK.read():
                    self._json(200, {"games": list_game_table(svc)})
                return
            if path == "/api/receipts":
                with _LOCK.read():
                    self._json(200, {"receipts": svc.receipts(25)})
                return
            if path == "/api/feed":
                with _LOCK.read():
                    self._json(200, {"feed": svc.sentinel.feed(25)})
                return
            if path == "/api/games/library":
                with _LOCK.read():
                    self._json(200, {"games": svc.gaming.games()})
                return
            if path == "/api/palette":
                with _LOCK.read():
                    q = ""
                    if "?" in self.path:
                        q = urlparse_qs(urlparse(self.path).query).get("q", [""])[0]
                    self._json(200, {"results": svc.palette.search(q)})
                return
            if path == "/api/palette/all":
                with _LOCK.read():
                    self._json(200, {"commands": [c.as_dict() for c in svc.palette.commands()]})
                return
            if path == "/api/peripheral":
                with _LOCK.read():
                    self._json(200, {"summary": svc.peripheral.summary(),
                                     "history": svc.peripheral.history(10)})
                return
            if path == "/api/consent":
                with _LOCK.read():
                    self._json(200, svc.consent.decision().as_dict())
                return
            if path == "/api/autopsy":
                with _LOCK.read():
                    self._json(200, {"autopsies": svc.autopsy.list(20)})
                return
            if path == "/api/catalog":
                with _LOCK.read():
                    self._json(200, {"tweaks": [
                        {"id": t.id, "module": t.module, "title": t.title,
                         "plain": t.plain, "tier": t.tier, "risk": t.risk}
                        for t in svc.catalog.all()]})
                return
        except Absent as exc:
            # A lookup that found nothing: "unknown workspace nope". The object
            # is not there — 404 — and the message already says which one.
            self._json(404, {"error": str(exc.args[0]) if exc.args else str(exc)})
            return
        except KeyError as exc:
            # A missing argument: `a["run_id"]` never got a run_id. That is a
            # malformed request, and naming the field is the whole fix. The two
            # used to be the same exception, so a failed lookup came back as
            # `missing field 'unknown workspace nope'`.
            name = exc.args[0] if exc.args else ""
            self._json(400, {"error": f"missing field '{name}'"})
            return
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - the UI must show the failure
            ref = f"keel-{uuid.uuid4().hex[:12]}"
            _log_exception(ref, exc)
            self._json(500, {"error": "Keel hit an internal error and did not "
                                      "change anything.", "ref": ref})
            return
        self._json(404, {"error": f"no route {path}"})

    # Verbs that change something. Reaching them over GET would let any web page
    # fire them at localhost with an <img> tag: no CORS, no preflight, and the
    # cookie-less request still arrives. They are POST-only.
    # Which verbs change something. The old version was a hand-maintained set
    # that had drifted: twelve actions including safety.panic and clean.run
    # answered a plain GET, so any web page could fire them with an <img> tag —
    # no CORS preflight, no prompt, the request still arrives. Deriving the set
    # from the verb's name means a new `*.apply` cannot silently miss the list;
    # the explicit additions cover verbs whose names do not advertise danger.
    _MUTATING_VERBS = re.compile(
        r"\.(apply|run|on|off|toggle|fire|panic|set|confirm|revert|save|write|"
        r"delete|block|rollback|restore|create|import|capture|erase|begin|finish|"
        r"sample|inspect|scan|check|validate|preview|sweep|tune|clean|fix|"
        r"fix_safe|fix_all|apply_doc|enable|disable|mute|unmute|switch|reset|"
        r"unblock|revoke|withdraw|clear|elevate)"
        r"$")
    #: Actions with no dot in their name cannot match the verb regex, so they
    #: are listed. `elevate` launches a process — it is not a read, and a page
    #: must not be able to trigger it with a link.
    MUTATING = {"elevate"}

    #: Reads despite the suffix (they compute, they do not persist).
    _READ_ONLY = {
        "clean.scan", "dupes.scan", "startup.inspect", "drift.check",
        "theme.preview", "theme.validate", "theme.get", "theme.gallery",
        "shell.preview", "space.sample", "space.forecast", "power.sample",
        "power.drains", "power.summary", "display.mode", "checkup.run",
        # bench.run is NOT here: it records an event in the sentinel feed, so a
        # GET would let a web page write to the journal. It is also a
        # multi-second measurement, which a link-fetch should not be able to
        # trigger. POST-only.
        "verify", "ask", "status", "report.weekly",
        "report.md", "report.json", "sens.save",
        # `theme_export` and `spotify_export` return text; neither writes. They
        # were caught by the suffix rule, and keeping them POST-only would have
        # been a lie about what they do.
        "theme.export", "spotify.export",
    }

    #: Actions that write a file or a directory but touch no database row and
    #: no registry key. They are POST (a link-fetch must not be able to spend
    #: five seconds of CPU) but they take the READ lock, because serialising
    #: them against /api/status is what froze the window for the length of a
    #: render. A render is a pure function of its input document.
    _FILE_ONLY = {
        # POST (they cost seconds or write a file) but they change no database
        # row, no registry key and no service, so they need not exclude the
        # status poll. Anything that touches the machine belongs outside this
        # set — see the test that pins that list.
        "wallpaper.save", "spotify.write",
    }

    @classmethod
    def _heavy_but_stateless(cls, action: str) -> bool:
        return action in cls._FILE_ONLY

    @classmethod
    def _is_mutating(cls, action: str) -> bool:
        if action in cls._READ_ONLY:
            return False
        return bool(cls._MUTATING_VERBS.search(action)) or action in cls.MUTATING

    # -------------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        if self._refuse_cross_origin():
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            args = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except json.JSONDecodeError:
            self._json(400, {"error": "body is not JSON"})
            return
        if not isinstance(args, dict):
            self._json(400, {"error": "body must be a JSON object"})
            return
        # Query parameters are merged in, body wins on conflict. The console has
        # always sent /api/themes/get?theme_id=... as a GET, and the handler
        # looked only at a POST body — every theme detail request was a 500 with
        # a KeyError nobody could see. Accepting both is the fix; the alternative
        # is a client and server that disagree about where a parameter lives.
        for key, values in urlparse_qs(parsed.query).items():
            if key not in args and values:
                args[key] = values[0]

        try:
            svc = _service()
            if path == "/api/apply":
                with _LOCK.write():
                    out = svc.apply_fixes(args.get("tweaks", []),
                                          finding_ids=args.get("finding_ids"),
                                          source="console")
                self._json(200 if out.get("applied") else 409, out)
                return
            if path == "/api/client-error":
                # The window reports its own uncaught errors here. Without this a
                # JS throw is invisible: the button moves and nothing happens,
                # which is exactly how ten broken empty-state rows shipped.
                where = str(args.get("where", "?"))[:200]
                message = str(args.get("message", "?"))[:500]
                stack = str(args.get("stack", ""))[:2000]
                line = f"[client] {where}: {message}\n{stack}\n"
                print(line, file=sys.stderr, flush=True)
                try:
                    from .app import log_path
                    with open(log_path(), "a", encoding="utf-8") as fh:
                        fh.write(line)
                except Exception:      # noqa: BLE001 - reporting is best-effort
                    pass
                self._json(200, {"logged": True})
                return
            if path == "/api/undo":
                with _LOCK.write():
                    self._json(200, svc.undo(_ident(args, "tx_id")))
                return
            if path == "/api/clean/restore":
                with _LOCK.write():
                    self._json(200, svc.clean_restore(_ident(args, "run_id")))
                return
            if path == "/api/dns/pause":
                with _LOCK.write():
                    self._json(200, {"paused_until": svc.dns.pause(_int(args, "minutes", default=30))})
                return
            if path == "/api/sens/save":
                with _LOCK.write():
                    prof = svc.sens.save(cm360=_num(args, "cm360"),
                                         dpi=_int(args, "dpi"))
                self._json(200, prof.as_dict())
                return
            if path == "/api/sens/from-game":
                with _LOCK.write():
                    prof = svc.sens.from_game(_ident(args, "game"), _num(args, "sens"),
                                              _int(args, "dpi"))
                self._json(200, {"profile": prof.as_dict(),
                                 "table": svc.sens.table()})
                return
            if path == "/api/sens/psa/start":
                with _LOCK.write():
                    self._json(200, svc.sens.psa_start())
                return
            if path == "/api/sens/psa/step":
                with _LOCK.write():
                    self._json(200, svc.sens.psa_step(_ident(args, "state"), bool(args["better"])))
                return
            if path == "/api/consent/set":
                with _LOCK.write():
                    out = svc.set_consent(level=args.get("level"),
                                          categories=args.get("categories"),
                                          trust_everything=bool(args.get("trust_everything")))
                self._json(200, out)
                return
            if path == "/api/game/watch":
                with _LOCK.write():
                    out = svc.gaming.capture(_ident(args, "game"),
                                             seconds=_num(args, "seconds", default=45))
                self._json(200, out)
                return
            if path == "/api/game/plan":
                with _LOCK.write():
                    out = svc.gaming.plan(_ident(args, "game"), args.get("session_id"))
                self._json(200, out)
                return
            if path == "/api/game/profile/save":
                with _LOCK.write():
                    self._json(200, svc.gaming.save_profile(_ident(args, "game"),
                                             _obj(args, "settings")))
                return
            if path == "/api/studio/apply":
                with _LOCK.write():
                    self._json(200, svc.studio.apply(_ident(args, "theme_id"),
                                                     force=bool(args.get("force"))))
                return
            if path == "/api/studio/save":
                with _LOCK.write():
                    self._json(200, svc.studio.save(_obj(args, "doc"), name=args.get("name")))
                return
            if path == "/api/studio/import":
                with _LOCK.write():
                    self._json(200, svc.studio.import_doc(_obj(args, "doc")))
                return
            if path == "/api/palette/run":
                with _LOCK.read():
                    out = svc.palette.execute(_ident(args, "verb"), args.get("args"))
                self._json(200, out)
                return
            if path == "/api/peripheral/test":
                with _LOCK.write():
                    out = svc.peripheral_run(_ident(args, "kind"),
                                       _obj(args, "payload", default={}))
                self._json(200, out)
                return
            if path == "/api/autopsy/note":
                with _LOCK.write():
                    items = svc.autopsy.note(_ident(args, "autopsy_id"),
                                            _obj(args, "items", default=[], kind=list))
                self._json(200, {"recorded": len(items),
                                 "autopsy": svc.autopsy.get(_ident(args, "autopsy_id")).as_dict()})
                return
            if path == "/api/autopsy/finish":
                with _LOCK.write():
                    au = svc.autopsy.finish(_ident(args, "autopsy_id"))
                self._json(200, au.as_dict())
                return
            if path == "/api/autopsy/erase":
                with _LOCK.write():
                    out = svc.autopsy.erase(_ident(args, "autopsy_id"))
                self._json(200, out)
                return
            if path == "/api/autopsy/begin":
                with _LOCK.write():
                    self._json(200, {"autopsy_id": svc.autopsy.begin(
                        str(args.get("installer", "unknown.exe"))[:260])})
                return
            if path in ROUTES:
                # A POST exists to change something, so it takes the write lock
                # and waits for readers to finish. In-flight reads are short
                # (status, inventory) by design; the long ones are renders and
                # they are POST too, so they serialise against each other rather
                # than against every poll.
                if self._heavy_but_stateless(ROUTES[path]):
                    lock = _LOCK.read()
                elif self._is_mutating(ROUTES[path]):
                    lock = _LOCK.write()
                else:
                    lock = _LOCK.read()
                with lock:
                    out = svc.run_action(ROUTES[path], args)
                self._json(200, out)
                return
        except KeyError as exc:
            # A KeyError has two completely different meanings and the UI must
            # not confuse them. `{"run_id": ...}` reaching `a["run_id"]` raises
            # KeyError whose arg is the *short* field name. A failed lookup like
            # `self.get(workspace_id)` raises KeyError whose arg is a whole
            # sentence — "unknown workspace nope". The first is a missing field;
            # the second is not a field at all. Wrapping the sentence as
            # `missing field 'unknown workspace nope'` told the user to add a
            # field called that.
            name = exc.args[0] if exc.args else ""
            if isinstance(name, str) and " " not in name and len(name) < 40:
                self._json(400, {"error": f"missing field '{name}'"})
            else:
                self._json(404, {"error": str(name)})
            return
        except ValueError as exc:
            # bad input from the client (invalid theme, bad level, bad maths) is a
            # 400 with the reason, never a 500 with a traceback.
            self._json(400, {"error": str(exc)})
            return
        except _CLIENT_FAULTS as exc:
            # The domain's own "you asked for something that is not there"
            # errors — EngineError("nothing journaled for nope"), BackendError,
            # DisplayError, RecipeError. These are the caller's problem, not a
            # defect in Keel, and answering them with a traceback leaked absolute
            # paths and source lines to the browser. Found by posting a bogus
            # tx_id to /api/undo, which is special-cased outside ROUTES and so
            # was never reached by the route fuzzer.
            self._json(409, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            # Anything left is a Keel defect. The browser gets a fixed sentence
            # and a correlation id; the full trace goes to the log, where it can
            # be read by someone fixing it and not by a page in a WebView.
            ref = f"keel-{uuid.uuid4().hex[:12]}"
            _log_exception(ref, exc)
            self._json(500, {"error": "Keel hit an internal error and did not "
                                      "change anything.",
                             "ref": ref})
            return
        self._json(404, {"error": f"no route {path}"})


# Errors whose meaning is "the request cannot be satisfied", not "Keel is
# broken". Mapped to 409 (conflict with the state of the world) with their own
# message. StoreUnusable is deliberately absent: an unusable database is a real
# fault and deserves the log, not a tidy 409.
_CLIENT_FAULTS = (EngineError, BackendError, DisplayError, RecipeError,
                  JournalError, StoreError)


_MISSING = object()


def _num(args: dict[str, Any], key: str, *, default: Any = _MISSING) -> float:
    """Read a finite number, refusing strings, nulls, NaN and infinity.

    `float([])` and `int("x")` raise TypeError/ValueError from inside the
    handler, which used to be a 500. NaN is refused too: it compares false
    against everything, so a NaN DPI would pass every range check downstream.
    """
    if key not in args:
        if default is _MISSING:
            raise KeyError(key)
        return float(default)
    raw = args[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"'{key}' must be a number, got {type(raw).__name__}")
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"'{key}' must be a finite number")
    return value


def _int(args: dict[str, Any], key: str, *, default: Any = _MISSING) -> int:
    """A whole number. Rejects 12.5 rather than silently truncating it."""
    value = _num(args, key, default=default)
    if value != int(value):
        raise ValueError(f"'{key}' must be a whole number, got {value}")
    return int(value)


def _obj(args: dict[str, Any], key: str, *, default: Any = _MISSING,
         kind: type = dict) -> Any:
    """Read a JSON object/array argument, refusing scalars."""
    if key not in args:
        if default is _MISSING:
            raise KeyError(key)
        return default
    raw = args[key]
    if not isinstance(raw, kind):
        raise ValueError(f"'{key}' must be a JSON "
                         f"{'object' if kind is dict else 'array'}, got "
                         f"{type(raw).__name__}")
    return raw


def _ident(args: dict[str, Any], key: str) -> str:
    """Read an identifier argument, refusing anything that is not one.

    The special-cased routes below call the service directly, so they miss the
    shape check in run_action(). That is how `{"run_id": []}` reached SQLite and
    came back as `ProgrammingError: type 'list' is not supported` — a 500 for
    what is plainly a bad request. Every identifier read on these routes goes
    through here.
    """
    if key not in args:
        raise KeyError(key)
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"'{key}' must be a string or a number, got "
                         f"{type(value).__name__}")
    if isinstance(value, str) and len(value) > 4096:
        raise ValueError(f"'{key}' is {len(value)} characters; limit 4096")
    return str(value)


def _log_exception(ref: str, exc: BaseException) -> None:
    """Write a full traceback to the engine log, tagged with its reference.

    The browser sees only the reference, so a user can quote one line and I can
    find the exact failure — without shipping source paths into the page.
    """
    line = f"[{ref}] {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    print(line, file=sys.stderr, flush=True)
    try:
        from .app import log_path       # same file the crash log uses
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:      # noqa: BLE001 - logging must never break the response
        pass


def list_game_table(svc: Service) -> list[dict[str, Any]]:
    return svc.sens.table()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pco_engine.server")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7788)))
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args(argv)
    _service()                       # migrate + recover before serving
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"PC Optimizer console → http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
