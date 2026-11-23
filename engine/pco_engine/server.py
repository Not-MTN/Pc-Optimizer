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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs as urlparse_qs
from urllib.parse import urlparse

from . import __version__
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
            q = {k: v[0] for k, v in urlparse_qs(parsed.query).items() if v}
            try:
                width = max(240, min(1440, int(q.get("w", 720))))
            except ValueError:
                width = 720
            try:
                png = _service().spotify_preview_png(look_id, width)
            except KeyError:
                self._json(404, {"error": f"no look {look_id!r}"})
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
            except Exception:  # noqa: BLE001
                self._json(500, {"error": traceback.format_exc(limit=3)})
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
        except Exception:  # noqa: BLE001 - the UI must show the failure, not crash
            self._json(500, {"error": traceback.format_exc(limit=3)})
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
            if path == "/api/undo":
                with _LOCK:
                    self._json(200, svc.undo(args["tx_id"]))
                return
            if path == "/api/clean/restore":
                with _LOCK:
                    self._json(200, svc.clean_restore(args["run_id"]))
                return
            if path == "/api/dns/pause":
                with _LOCK:
                    self._json(200, {"paused_until": svc.dns.pause(int(args.get("minutes", 30)))})
                return
            if path == "/api/sens/save":
                with _LOCK:
                    prof = svc.sens.save(cm360=float(args["cm360"]),
                                         dpi=int(args["dpi"]))
                self._json(200, prof.as_dict())
                return
            if path == "/api/sens/from-game":
                with _LOCK:
                    prof = svc.sens.from_game(args["game"], float(args["sens"]),
                                              int(args["dpi"]))
                self._json(200, {"profile": prof.as_dict(),
                                 "table": svc.sens.table()})
                return
            if path == "/api/sens/psa/start":
                with _LOCK:
                    self._json(200, svc.sens.psa_start())
                return
            if path == "/api/sens/psa/step":
                with _LOCK:
                    self._json(200, svc.sens.psa_step(args["state"], args["better"]))
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
                    out = svc.gaming.capture(args["game"],
                                             seconds=float(args.get("seconds", 45)))
                self._json(200, out)
                return
            if path == "/api/game/plan":
                with _LOCK:
                    out = svc.gaming.plan(args["game"], args.get("session_id"))
                self._json(200, out)
                return
            if path == "/api/game/profile/save":
                with _LOCK:
                    self._json(200, svc.gaming.save_profile(args["game"], args["settings"]))
                return
            if path == "/api/studio/apply":
                with _LOCK:
                    self._json(200, svc.studio.apply(args["theme_id"],
                                                     force=bool(args.get("force"))))
                return
            if path == "/api/studio/save":
                with _LOCK:
                    self._json(200, svc.studio.save(args["doc"], name=args.get("name")))
                return
            if path == "/api/studio/import":
                with _LOCK:
                    self._json(200, svc.studio.import_doc(args["doc"]))
                return
            if path == "/api/palette/run":
                with _LOCK:
                    out = svc.palette.execute(args["verb"], args.get("args"))
                self._json(200, out)
                return
            if path == "/api/peripheral/test":
                with _LOCK:
                    out = svc.peripheral_run(args["kind"], args.get("payload", {}))
                self._json(200, out)
                return
            if path == "/api/autopsy/note":
                with _LOCK:
                    items = svc.autopsy.note(args["autopsy_id"], args.get("items", []))
                self._json(200, {"recorded": len(items),
                                 "autopsy": svc.autopsy.get(args["autopsy_id"]).as_dict()})
                return
            if path == "/api/autopsy/finish":
                with _LOCK:
                    au = svc.autopsy.finish(args["autopsy_id"])
                self._json(200, au.as_dict())
                return
            if path == "/api/autopsy/erase":
                with _LOCK:
                    out = svc.autopsy.erase(args["autopsy_id"])
                self._json(200, out)
                return
            if path == "/api/autopsy/begin":
                with _LOCK:
                    self._json(200, {"autopsy_id": svc.autopsy.begin(
                        args.get("installer", "unknown.exe"))})
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
        except Exception:  # noqa: BLE001
            self._json(500, {"error": traceback.format_exc(limit=3)})
            return
        self._json(404, {"error": f"no route {path}"})


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
