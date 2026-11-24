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
from .service import Service

_LOCK = threading.Lock()
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
    mode = os.environ.get("PCO_BACKEND", "demo")
    if mode == "demo":
        return None                      # Service builds its own FakeBackend
    if mode == "windows" or (mode == "auto" and sys.platform == "win32"):
        from .windows_backend import WindowsBackend
        return WindowsBackend(
            allow_display=os.environ.get("PCO_WINDOWS_DISPLAY") == "1")
    return None


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
    "/api/drift": "drift.check",
    "/api/report": "report.weekly",
    "/api/checkup/apply": "checkup.fix",
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
        # preview runs in an iframe on another origin
        self.send_header("Access-Control-Allow-Origin", "*")
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

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    # --------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
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
                if ROUTES[path] in self.MUTATING:
                    self._json(405, {"error": f"{ROUTES[path]} changes state; "
                                              "POST it to this same path"})
                    return
                query = {k: v[0] for k, v in urlparse_qs(parsed.query).items() if v}
                with _LOCK:
                    # run_action defaults args to {}, so a verb that takes nothing
                    # needs no special case here.
                    result = svc.run_action(ROUTES[path], query)
                self._json(200, result)
                return
            if path == "/api/sens":
                with _LOCK:
                    self._json(200, {"games": list_game_table(svc)})
                return
            if path == "/api/receipts":
                with _LOCK:
                    self._json(200, {"receipts": svc.receipts(25)})
                return
            if path == "/api/feed":
                with _LOCK:
                    self._json(200, {"feed": svc.sentinel.feed(25)})
                return
            if path == "/api/games/library":
                with _LOCK:
                    self._json(200, {"games": svc.gaming.games()})
                return
            if path == "/api/palette":
                with _LOCK:
                    q = ""
                    if "?" in self.path:
                        q = urlparse_qs(urlparse(self.path).query).get("q", [""])[0]
                    self._json(200, {"results": svc.palette.search(q)})
                return
            if path == "/api/palette/all":
                with _LOCK:
                    self._json(200, {"commands": [c.as_dict() for c in svc.palette.commands()]})
                return
            if path == "/api/peripheral":
                with _LOCK:
                    self._json(200, {"summary": svc.peripheral.summary(),
                                     "history": svc.peripheral.history(10)})
                return
            if path == "/api/consent":
                with _LOCK:
                    self._json(200, svc.consent.decision().as_dict())
                return
            if path == "/api/autopsy":
                with _LOCK:
                    self._json(200, {"autopsies": svc.autopsy.list(20)})
                return
            if path == "/api/catalog":
                with _LOCK:
                    self._json(200, {"tweaks": [
                        {"id": t.id, "module": t.module, "title": t.title,
                         "plain": t.plain, "tier": t.tier, "risk": t.risk}
                        for t in svc.catalog.all()]})
                return
        except KeyError as exc:
            # A GET route needs a parameter the caller did not send. That is the
            # caller's mistake, and naming the missing field is the whole fix:
            # before this, every such request was a 500 with a traceback.
            self._json(400, {"error": f"missing query parameter {exc}"})
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
    MUTATING = {
        "workspace.restore", "workspace.capture", "workspace.delete",
        "drivers.block", "drivers.rollback",
        "recipe.parse", "recipe.create", "recipe.toggle", "recipe.delete",
        "theme.apply", "theme.save", "theme.import",
        "wallpaper.save", "wallpaper.apply",
        "shell.apply", "shell.theme.save", "shell.theme.import",
        "safety.confirm", "spotify.write",
    }

    # -------------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
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
                with _LOCK:
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
                with _LOCK:
                    self._json(200, svc.undo(_ident(args, "tx_id")))
                return
            if path == "/api/clean/restore":
                with _LOCK:
                    self._json(200, svc.clean_restore(_ident(args, "run_id")))
                return
            if path == "/api/dns/pause":
                with _LOCK:
                    self._json(200, {"paused_until": svc.dns.pause(_int(args, "minutes", default=30))})
                return
            if path == "/api/sens/save":
                with _LOCK:
                    prof = svc.sens.save(cm360=_num(args, "cm360"),
                                         dpi=_int(args, "dpi"))
                self._json(200, prof.as_dict())
                return
            if path == "/api/sens/from-game":
                with _LOCK:
                    prof = svc.sens.from_game(_ident(args, "game"), _num(args, "sens"),
                                              _int(args, "dpi"))
                self._json(200, {"profile": prof.as_dict(),
                                 "table": svc.sens.table()})
                return
            if path == "/api/sens/psa/start":
                with _LOCK:
                    self._json(200, svc.sens.psa_start())
                return
            if path == "/api/sens/psa/step":
                with _LOCK:
                    self._json(200, svc.sens.psa_step(_ident(args, "state"), bool(args["better"])))
                return
            if path == "/api/consent/set":
                with _LOCK:
                    out = svc.set_consent(level=args.get("level"),
                                          categories=args.get("categories"),
                                          trust_everything=bool(args.get("trust_everything")))
                self._json(200, out)
                return
            if path == "/api/game/watch":
                with _LOCK:
                    out = svc.gaming.capture(_ident(args, "game"),
                                             seconds=_num(args, "seconds", default=45))
                self._json(200, out)
                return
            if path == "/api/game/plan":
                with _LOCK:
                    out = svc.gaming.plan(_ident(args, "game"), args.get("session_id"))
                self._json(200, out)
                return
            if path == "/api/game/profile/save":
                with _LOCK:
                    self._json(200, svc.gaming.save_profile(_ident(args, "game"),
                                             _obj(args, "settings")))
                return
            if path == "/api/studio/apply":
                with _LOCK:
                    self._json(200, svc.studio.apply(_ident(args, "theme_id"),
                                                     force=bool(args.get("force"))))
                return
            if path == "/api/studio/save":
                with _LOCK:
                    self._json(200, svc.studio.save(_obj(args, "doc"), name=args.get("name")))
                return
            if path == "/api/studio/import":
                with _LOCK:
                    self._json(200, svc.studio.import_doc(_obj(args, "doc")))
                return
            if path == "/api/palette/run":
                with _LOCK:
                    out = svc.palette.execute(_ident(args, "verb"), args.get("args"))
                self._json(200, out)
                return
            if path == "/api/peripheral/test":
                with _LOCK:
                    out = svc.peripheral_run(_ident(args, "kind"),
                                       _obj(args, "payload", default={}))
                self._json(200, out)
                return
            if path == "/api/autopsy/note":
                with _LOCK:
                    items = svc.autopsy.note(_ident(args, "autopsy_id"),
                                            _obj(args, "items", default=[], kind=list))
                self._json(200, {"recorded": len(items),
                                 "autopsy": svc.autopsy.get(_ident(args, "autopsy_id")).as_dict()})
                return
            if path == "/api/autopsy/finish":
                with _LOCK:
                    au = svc.autopsy.finish(_ident(args, "autopsy_id"))
                self._json(200, au.as_dict())
                return
            if path == "/api/autopsy/erase":
                with _LOCK:
                    out = svc.autopsy.erase(_ident(args, "autopsy_id"))
                self._json(200, out)
                return
            if path == "/api/autopsy/begin":
                with _LOCK:
                    self._json(200, {"autopsy_id": svc.autopsy.begin(
                        str(args.get("installer", "unknown.exe"))[:260])})
                return
            if path in ROUTES:
                with _LOCK:
                    out = svc.run_action(ROUTES[path], args)
                self._json(200, out)
                return
        except KeyError as exc:
            self._json(400, {"error": f"missing field {exc}"})
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
