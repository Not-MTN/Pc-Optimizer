"""The service — the single object the UI (or CLI, or HTTP bridge) talks to.

It owns every module and exposes typed, JSON-safe operations. Nothing above it
touches the database, and nothing below it knows the UI exists.

This is also where the "one action model" lives: the palette, the CLI, the
recipes and the dashboard all call `run_action(name, args)` — one catalog of
verbs, one permission check, one receipt.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import Engine, FakeBackend, Catalog
from .bench import Bench
from .checker import Checker, Probe
from .cleaner import Cleaner, Rule
from .ask import Ask
from .autopsy import AutopsyLab
from .consent import Consent
from .dns import DnsBlocker
from .ids import new_id
from .nlrecipe import parse as parse_recipe
from .palette import Palette
from .peripheral import PeripheralLab
from .power import BatteryReading, Power
from .report import Report, build as build_report, to_html, to_markdown
from .space import Space
from .startup import BootReport, StartupItem, StartupSurgeon
from .workspace import Entry, Workspace, Workspaces
from .address import values_equal
from .engine import EngineError, PreflightBlocked
from .drivers import Driver, DriverManager
from .dupes import Duplicates
from .gaming import Gaming
from .guard import Guard
from .recipes import Recipes, Spec
from .sens import SensLab
from .sentinel import Event, Sentinel
from .store import connect, integrity, migrate
from .studio import Studio

DEFAULT_RULES = [
    Rule("temp.user", "Your temporary files", "free", "cosmetic",
         ["{temp}/*"], note="Windows and app scratch space"),
    Rule("temp.system", "System temp", "free", "cosmetic",
         ["{temp_system}/*"], note="Needs admin on a real machine"),
    Rule("cache.thumbs", "Thumbnail cache", "auto_regen", "cosmetic",
         ["{cache}/thumbs/*"], note="Rebuilds itself the next time you browse folders"),
    Rule("cache.dns", "DNS cache", "auto_regen", "cosmetic",
         ["{cache}/dns/*"], note="Rebuilds on the next lookup"),
    Rule("junk.old_logs", "Old logs and crash dumps", "free", "cosmetic",
         ["{logs}/*.log", "{logs}/*.dmp"], note="Nothing reads these after 30 days"),
    Rule("junk.installers", "Leftover installers", "free", "low",
         ["{downloads}/*.exe", "{downloads}/*.msi"],
         note="Installer downloads you have already used"),
]

DEMO_LIST = """# a tiny, honest sample list (real builds ship compiled lists)
ads.doubleclick.net
*.googlesyndication.com
*.adsrvr.org
*.criteo.com
*.taboola.com
telemetry.microsoft.com
*.vortex.data.microsoft.com
*.scorecardresearch.com
*.hotjar.com
*.mixpanel.com
*.segment.io
"""


class Service:
    def __init__(self, db_path: str, *, backend=None, catalog_dir: str | None = None,
                 vault_root: str | None = None, clock=None) -> None:
        here = Path(__file__).resolve().parent
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.catalog = Catalog.load_dir(catalog_dir or str(here.parent / "catalog"))
        self.backend = backend if backend is not None else FakeBackend(DEMO_STATE)
        self._demo = self.backend is not None and self.backend.__class__.__name__ == "FakeBackend"
        self.conn = connect(db_path)
        migrate(self.conn, clock=self.clock)
        self.engine = Engine(self.conn, self.backend, self.catalog, clock=self.clock)

        self.vault_root = vault_root or str(Path(db_path).parent / "vault")
        os.makedirs(self.vault_root, exist_ok=True)
        roots = CLEAN_ROOTS
        if self._demo:
            roots = self._seed_demo_junk(Path(db_path).parent / "demo_machine")
        self.cleaner = Cleaner(self.conn, roots, self.vault_root, self.clock,
                              engine=self.engine)
        self.cleaner.seed_rules(DEFAULT_RULES)

        # Demo metrics only on the simulated machine. On a real one, a hardcoded
        # "42 GB of junk" would be a fabricated finding — exactly what the whole
        # honesty story forbids.
        self.checker = Checker(self.conn,
                               Probe(self.backend, DEMO_METRICS if self._demo else {}),
                               self.clock)
        self.bench = Bench(self.conn, self.clock)
        self.sentinel = Sentinel(self.conn, self.clock)
        self.dns = DnsBlocker(self.conn, self.clock)
        if self._demo:
            self._install_demo_lists()
        self.recipes = Recipes(self.conn, self.clock, run_action=self.run_action)
        self.sens = SensLab(self.conn, self.clock)
        self.gaming = Gaming(self.conn, self.clock)
        self.studio = Studio(self.conn, self.clock)
        self.consent = Consent(self.conn, self.clock)
        self.startup = StartupSurgeon(self.conn, self.clock,
                                      backend=self.backend, catalog=self.catalog)
        self.palette = Palette(self.conn, self.clock, run_action=self.run_action)
        self.space = Space(self.conn, self.clock, roots=self._space_roots())
        self.peripheral = PeripheralLab(self.conn, self.clock)
        self.dupes = Duplicates(self.conn, self.clock)
        self.ask = Ask(self)
        self.guard = Guard(self)
        self.power = Power(self.conn, self.clock, reader=DEMO_BATTERY if self._demo else None)
        self.workspaces = Workspaces(self.conn, self.clock)
        self.drivers = DriverManager(
            self.conn, self.clock,
            snapshots=lambda note: self.engine.snapshots.capture(
                sorted(self.catalog.addresses()), kind="pre_risky", note=note).id)
        if self._demo:
            self._seed_demo_drivers()
        if self._demo:
            self._seed_demo_space()
        self.autopsy = AutopsyLab(
            self.conn, self.clock,
            snapshot=lambda: self.engine.snapshots.capture(
                sorted(self.catalog.addresses()), kind="pre_risky",
                note="before installer autopsy").id)

        self.boot = self.guard.boot()
        self.recover_on_start(skip_recover=True)

    # ================================================================ lifecycle
    def tick(self, *, now: int | None = None) -> dict[str, Any]:
        """The heartbeat. Called by the UI on every refresh and by the CLI.

        This is the only place an armed (risky) change is either confirmed by
        silence or auto-reverted. It used to be nobody's job, which meant a
        change marked 'auto-revert in 60s' would sit there forever.

        Offline rule: if the process was not running across the whole window, the
        elapsed time is not evidence that the user was happy. In that case the
        change is reverted *and* the reason recorded as a missed window, so the
        receipt is honest rather than flattering.
        """
        now = now if now is not None else self.clock()
        out: dict[str, Any] = {"reverted": [], "confirmed": [], "pending": 0,
                               "missed_windows": []}

        rows = self.conn.execute(
            "SELECT id,tx_id,armed_at,window_ms FROM armed_changes WHERE outcome='pending'"
        ).fetchall()
        for row in rows:
            cid, tx_id, armed_at, window_ms = row[0], row[1], row[2], row[3]
            due = armed_at + window_ms
            if now < due:
                out["pending"] += 1
                continue
            # was the app actually watching during the window?
            beats = self.conn.execute(
                "SELECT COUNT(*) FROM heartbeats WHERE at > ?", (armed_at,)).fetchone()[0]
            if beats <= 1:
                out["missed_windows"].append(tx_id)
            try:
                self.engine.revert(tx_id, reason="confirmation window expired")
                with self.conn:
                    self.conn.execute(
                        "UPDATE armed_changes SET outcome='reverted', decided_at=? "
                        "WHERE id=?", (now, cid))
                out["reverted"].append(tx_id)
                self.sentinel.record(Event(
                    kind="safety", source="tick", severity="warning",
                    narrative=("Auto-reverted an unconfirmed change" if beats > 1 else
                               "Auto-reverted an unconfirmed change (the app was not "
                               "running for most of the window — that is not consent)"),
                    payload={"tx_id": tx_id}, tx_id=tx_id))
            except Exception as exc:      # noqa: BLE001 - keep the loop alive
                out.setdefault("errors", []).append({"tx_id": tx_id, "error": str(exc)})

        with self.conn:
            self.conn.execute(
                "INSERT INTO heartbeats(id,at) VALUES(?,?)", (new_id(), now))
            # keep the table small: it is only used to reason about short windows
            self.conn.execute("DELETE FROM heartbeats WHERE at < ?", (now - 7 * 86_400_000,))
        out["at"] = now
        return out

    def register_armed(self, tx_id: str, window_ms: int) -> str:
        cid = new_id()
        with self.conn:
            self.conn.execute(
                "INSERT INTO armed_changes(id,tx_id,armed_at,window_ms) VALUES(?,?,?,?)",
                (cid, tx_id, self.clock(), window_ms))
        return cid

    def confirm(self, tx_id: str) -> dict[str, Any]:
        """The user pressed 'keep it'. Disarms the auto-revert."""
        self.engine.confirm(tx_id)
        with self.conn:
            self.conn.execute(
                "UPDATE armed_changes SET outcome='confirmed', decided_at=? "
                "WHERE tx_id=? AND outcome='pending'", (self.clock(), tx_id))
        return {"tx_id": tx_id, "confirmed": True}

    def pending_confirmations(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,tx_id,armed_at,window_ms FROM armed_changes "
            "WHERE outcome='pending' ORDER BY armed_at").fetchall()
        return [{"id": r[0], "tx_id": r[1], "armed_at": r[2], "window_ms": r[3],
                 "seconds_left": max(0, (r[2] + r[3] - self.clock()) // 1000)}
                for r in rows]

    def recover_on_start(self, *, skip_recover: bool = False) -> dict[str, Any]:
        """Run before anything else: an interrupted transaction must be repaired
        first, or every later decision is made on an unknown machine.

        `skip_recover` is used when Guard.boot() has already done it — recovery is
        idempotent, but running it twice would double-write the boot event.
        """
        fixed = [] if skip_recover else self.engine.recover()
        ok, rows, detail = self.engine.journal.verify_chain()
        if fixed:
            self.sentinel.record(Event(
                kind="recovery", source="engine", severity="warning",
                narrative=f"Recovered {len(fixed)} change(s) from an interrupted session.",
                payload={"fixed": fixed}))
        if not ok:
            self.sentinel.record(Event(
                kind="integrity", source="engine", severity="critical",
                narrative=f"The safety journal failed verification: {detail}",
                payload={"detail": detail, "rows": rows}))
        return {"recovered": len(fixed), "journal_ok": ok, "detail": detail}

    # ================================================================= dispatch
    def run_action(self, name: str, args: dict[str, Any] | None = None) -> Any:
        """The one catalog of verbs. Palette, CLI, recipes and UI all come here."""
        args = args or {}
        handler = ACTIONS.get(name)
        if handler is None:
            raise KeyError(f"unknown action: {name}")
        return handler(self, args)

    # ================================================================== modules
    def checkup(self, *, fix_safe: bool = False) -> dict[str, Any]:
        result = self.checker.run(trigger="user")
        payload = result.as_dict()
        self.sentinel.record(Event(
            kind="checkup", source="checker", severity="info",
            narrative=f"Checkup complete: {payload['counts']['total']} finding(s), "
                      f"score {result.total}.",
            payload={"run_id": result.run_id, "score": result.total}))
        if fix_safe:
            payload["fix"] = self.fix_by_consent(result)
        payload["consent"] = self.consent.decision().as_dict()
        return payload

    def fix_by_consent(self, result=None) -> dict[str, Any]:
        """Apply exactly what the consent level permits — never more.

        `result` is a CheckResult when we already have fresh findings; otherwise
        the latest persisted run is used, so the button works from the UI without
        re-running the whole checkup.
        """
        if result is not None:
            findings = result.findings
            as_dicts = [f.as_dict() for f in findings]
        else:
            latest = self.checker.latest_run()
            if latest is None:
                return {"applied": False, "reason": "no checkup has run yet"}
            as_dicts = self.checker.findings_of(latest["run_id"])
            findings = as_dicts
        chosen, withheld = [], []
        for f in as_dicts:
            has_fix = bool(f.get("fix_tweaks")) and bool(
                str(f.get("fix_tweaks")).strip("[]'\""))
            if self.consent.allows(f["category"], auto_safe=bool(f["auto_safe"]),
                                  has_fix=has_fix):
                chosen.append(f)
            else:
                withheld.append({"check_id": f["check_id"], "category": f["category"],
                                 "why": ("no declared fix" if not has_fix else
                                         "not permitted by current consent")})
        tweaks = [t for f in chosen for t in f["fix_tweaks"]]
        if not tweaks:
            return {"applied": False, "reason": "consent allows nothing here",
                    "withheld": withheld, "chosen": []}
        out = self.apply_fixes(tweaks, finding_ids=[f["id"] for f in chosen])
        out["chosen"] = [f["check_id"] for f in chosen]
        out["withheld"] = withheld
        return out

    def set_consent(self, *, level: str | None = None,
                    categories: dict[str, str] | None = None,
                    trust_everything: bool = False) -> dict[str, Any]:
        if trust_everything:
            d = self.consent.trust_everything()
        else:
            d = self.consent.set(level=level, categories=categories)
        self.sentinel.record(Event(
            kind="consent", source="consent", severity="warning",
            narrative=f"Automation level is now '{d.level}'.",
            payload=d.as_dict()))
        return d.as_dict()

    def already_satisfied(self, tweak_id: str) -> bool:
        """True when every effect of this tweak already matches reality.

        Needed because "fix everything" is a batch: one already-correct item must
        not fail the eleven that are not, and on a healthy machine the whole
        operation should be a documented no-op rather than an error.
        """
        try:
            tw = self.catalog.get(tweak_id)
        except Exception:          # noqa: BLE001 - unknown tweaks are preflight's job
            return False
        if not tw.effects:
            return False
        for eff in tw.effects:
            try:
                current = self.engine._read(eff.address)
            except Exception:      # noqa: BLE001 - unreadable means "assume not satisfied"
                return False
            if not values_equal(current, eff.to):
                return False
        return True

    def apply_fixes(self, tweak_ids: list[str], *, finding_ids: list[str] | None = None,
                    source: str = "ui") -> dict[str, Any]:
        ids = [t for t in dict.fromkeys(tweak_ids) if not self.already_satisfied(t)]
        if not tweak_ids:
            return {"applied": False, "reason": "nothing to fix"}
        if not ids:
            return {"applied": True, "already": True, "effects": [],
                    "summary": "Already applied — every value already matches.",
                    "tx_id": None}
        try:
            res = self.engine.apply(ids, source=source, kind="batch")
        except EngineError as exc:
            if "nothing to do" in str(exc).lower():
                # Everything requested is already in place. That is success with
                # nothing to write, not an error — the UI must not show a red box.
                return {"applied": True, "already": True, "effects": [],
                        "summary": "Already applied — every value already matches.",
                        "tx_id": None}
            return {"applied": False, "error": str(exc)}
        except PreflightBlocked as exc:
            return {"applied": False, "blocked": [i.as_dict() for i in exc.plan.errors]}
        if getattr(res, "armed_until", None):
            self.register_armed(res.tx_id, self.engine.confirm_window_ms)
        if finding_ids:
            self.checker.mark_fixed(finding_ids, res.tx_id)
        # Every applied batch belongs in History. Without this the most important
        # operation in the product left no receipt at all.
        self._write_receipt(kind="tweak", title=f"Applied {len(res.effects)} change(s)",
                            summary=res.summary, tx_id=res.tx_id,
                            detail={"tweaks": ids, "effects": [
                                {"address": e.address, "old": e.old, "new": e.new}
                                for e in res.effects],
                                "snapshot_id": res.snapshot_id})
        self.sentinel.record(Event(
            kind="fix", source="engine", severity="info",
            narrative=f"Applied {len(res.effects)} fix(es) — undo is one click.",
            payload={"tx_id": res.tx_id}, tx_id=res.tx_id))
        return {"applied": True, **{k: v for k, v in {
            "tx_id": res.tx_id, "summary": res.summary, "snapshot_id": res.snapshot_id,
            "needs_reboot": res.needs_reboot,
            "effects": [{"address": e.address, "old": e.old, "new": e.new}
                        for e in res.effects]}.items()}}

    def _write_receipt(self, *, kind: str, title: str, summary: str, tx_id: str | None,
                       detail: dict[str, Any]) -> str:
        rid = new_id()
        with self.conn:
            self.conn.execute(
                "INSERT INTO receipts(id,at,kind,title,summary,detail_json,tx_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (rid, self.clock(), kind, title, summary,
                 json.dumps(detail, sort_keys=True, separators=(",", ":")), tx_id))
        return rid

    def undo(self, tx_id: str) -> dict[str, Any]:
        res = self.engine.revert(tx_id, reason="ui")
        with self.conn:
            self.conn.execute("UPDATE receipts SET undone_at=? WHERE tx_id=?",
                              (self.clock(), tx_id))
        self.sentinel.record(Event(
            kind="undo", source="engine", severity="info",
            narrative=f"Undid {res.reverted} change(s).", payload={"tx_id": tx_id}))
        return {"tx_id": res.tx_id, "reverted": res.reverted, "failed": res.failed}

    def clean_scan(self, *, classes: set[str] | None = None) -> dict[str, Any]:
        scan = self.cleaner.scan(classes)
        return scan.as_dict()

    def clean_run(self) -> dict[str, Any]:
        scan = self.cleaner.scan({"free", "auto_regen"})
        if not scan.candidates:
            return {"moved": 0, "summary": "Nothing safe to clean."}
        res = self.cleaner.clean(scan.candidates, source="ui")
        self.sentinel.record(Event(
            kind="clean", source="cleaner", severity="info",
            narrative=f"Cleaned {res.moved} item(s) — {res.bytes_moved/1e9:.2f} GB in the vault, "
                      f"restorable for 30 days.",
            payload=res.as_dict(), tx_id=res.tx_id))
        return res.as_dict()

    def clean_restore(self, run_id: str) -> dict[str, Any]:
        res = self.cleaner.restore(run_id)
        self.sentinel.record(Event(
            kind="restore", source="cleaner", severity="info",
            narrative=f"Restored {res['restored']} item(s).", payload=res))
        return res

    def bench_run(self) -> dict[str, Any]:
        res = self.bench.run()
        self.sentinel.record(Event(
            kind="bench", source="bench", severity="info",
            narrative=f"Benchmark: score {res.score:.0f}" + ("" if res.stable else
                      " (noisy environment — treat small deltas with suspicion)"),
            payload=res.as_dict()))
        return res.as_dict()

    # ================================================================== startup
    def startup_items(self) -> list:
        """Demo machine inventory: real shape, modelled costs, honest nulls."""
        from .startup import StartupItem
        if not self._demo:
            return []
        raw = [
            ("DiagTrack (diagnostics)", "service", "service:diagtrack", 1450, "services"),
            ("Windows Search", "service", "service:wusearch", 900, "services"),
            ("SysMain (prefetch)", "service", "service:sysmain", 620, "services"),
            ("OneDrive", "run_key", "run_key:onedrive", 900, "logon"),
            ("Steam", "run_key", "run_key:steam", 1400, "logon"),
            ("Epic Games Launcher", "run_key", "run_key:epicgameslauncher", 1100, "logon"),
            ("RGB Suite", "run_key", "run_key:rgbsuite", 700, "shell"),
            ("Acme Updater", "run_key", "run_key:acmeupdater", 600, "logon"),
            ("Cloud Sync", "run_key", "run_key:cloudsync", 1200, "logon"),
            ("Vendor Tray", "run_key", "run_key:vendortray", 500, "shell"),
            ("Office Background Task", "task",
             "task:\\microsoft\\office\\officebackgroundtaskhandler", 800, "kernel"),
            ("Spotify", "run_key", "run_key:spotify", 400, "logon"),
            ("NVIDIA Container", "service", "service:nvcontainer", None, "unattributed"),
            ("Unknown tray helper", "run_key", "run_key:unknown1", None, "unattributed"),
        ]
        return [StartupItem(id=f"su{i}", name=n, cls=c, locator=loc, cost_ms=cost,
                            phase=ph) for i, (n, c, loc, cost, ph) in enumerate(raw)]

    def startup_report(self) -> dict[str, Any]:
        from .startup import Dependency
        items = self.startup_items()
        if not items:
            return {"available": False,
                    "reason": "startup inventory needs the Windows backend"}
        self.startup.set_dependencies([
            Dependency(dependent="wuauserv", needs="service:wuauserv"),
            Dependency(dependent="Security Center", needs="service:wusearch"),
        ])
        rep = self.startup.report(items)
        plan = self.startup.plan(items)
        kg = [self._knowledge_row(i) for i in items]
        return {"available": True, "report": rep.as_dict(), "plan": plan,
                "knowledge": kg,
                "verdict": (f"{rep.measured} item(s) have a measured cost; "
                            f"{rep.unattributed} are unattributed and are not counted.")}

    def _knowledge_row(self, item) -> dict[str, Any]:
        from .startup import knowledge_for, refusal
        k = knowledge_for(item)
        why = refusal(item, self.startup._deps)
        return {"item": item.name, "class": item.cls, "phase": item.phase,
                "cost_label": item.as_dict()["cost_label"],
                "does": (k or {}).get("does", "no knowledge entry"),
                "recommended": (k or {}).get("recommended", "unattributed — measure first"),
                "breaks": (k or {}).get("breaks", []),
                "refusal": why}

    def startup_apply(self, *, mode: str = "defer") -> dict[str, Any]:
        rep = self.startup_report()
        if not rep.get("available"):
            return {"applied": [], "reason": rep["reason"]}
        out = self.startup.apply_plan(rep["plan"], mode=mode)
        self.sentinel.record(Event(
            kind="startup", source="startup", severity="info",
            narrative=f"Startup plan applied ({mode}): {len(out['applied'])} item(s).",
            payload=out))
        self._write_receipt(kind="startup", title="Startup Surgeon",
                            summary=f"{len(out['applied'])} item(s) {mode}ed",
                            tx_id=None, detail=out)
        return out

    # ================================================================== ask/space
    def ask_question(self, question: str) -> dict[str, Any]:
        return self.ask.ask(question).as_dict()

    def space_sample(self) -> dict[str, Any]:
        s = self.space.sample()
        return s.as_dict()

    def space_grew(self, days: int = 7) -> dict[str, Any]:
        return self.space.grew(days)

    def space_forecast(self) -> dict[str, Any]:
        return self.space.forecast()

    def find_duplicates(self, roots: list[str] | None = None) -> dict[str, Any]:
        """Scan the demo temp tree, or the given roots, and report confirmed dups."""
        if roots is None:
            roots = ([r for r in (self.cleaner.roots.get("downloads"),
                                  self.cleaner.roots.get("temp")) if r]
                     if self._demo else [str(Path.home())])
        return self.dupes.scan(roots).as_dict()

    def peripheral_run(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one hardware test and store the verdict with its evidence."""
        from . import peripheral as p
        if kind == "mouse_double_click":
            res = p.double_click_test(payload.get("intervals_ms", []),
                                      device=payload.get("device", "Mouse"))
        elif kind == "keyboard_chatter":
            pairs = payload.get("events", [])
            res = p.chatter_test([(k, int(t)) for k, t in pairs],
                                 device=payload.get("device", "Keyboard"))
        elif kind == "sensor_jitter":
            res = p.sensor_jitter_test([(float(a), float(b)) for a, b in
                                        payload.get("samples", [])],
                                       device=payload.get("device", "Mouse"))
        elif kind == "polling_rate":
            res = p.polling_rate_test(payload.get("intervals_us", []),
                                      device=payload.get("device", "Mouse"))
        elif kind == "monitor":
            return p.monitor_pattern(payload.get("pattern", "dead_pixel"))
        else:
            raise ValueError(f"unknown test {kind!r}")
        self.peripheral.record(res)
        if res.verdict == "fail":
            self.sentinel.record(Event(
                kind="hardware", source="peripheral", severity="warning",
                narrative=res.headline, payload=res.as_dict()))
        return res.as_dict()

    def health_report(self, fmt: str = "json") -> Any:
        rep = build_report(self)
        if fmt == "md":
            return to_markdown(rep)
        if fmt == "html":
            return to_html(rep)
        return rep.as_dict()

    # ================================================================ power/batt
    def battery(self) -> dict[str, Any]:
        return self.power.sample() or {
            "available": False,
            "reason": "battery telemetry needs the Windows backend"}

    def thermal(self) -> dict[str, Any]:
        return self.power.thermal_verdict()

    # =============================================================== workspaces
    def save_workspace(self, name: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        from .workspace import Entry
        es = [Entry(app=e["app"], args=tuple(e.get("args", [])),
                    monitor=int(e.get("monitor", 0)), profile=e.get("profile"),
                    title=e.get("title", "")) for e in entries]
        return self.workspaces.save(name, es).as_dict()

    def restore_workspace(self, workspace_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        return self.workspaces.restore(workspace_id, dry_run=dry_run)

    # =================================================================== drivers
    def driver_inventory(self) -> dict[str, Any]:
        inv = self.drivers.inventory()
        scored = []
        for d in inv:
            s = self.drivers.score(d["version"])
            scored.append({**d, "score": s})
        return {"drivers": scored, "blocks": self.drivers.blocks()}

    def nl_recipe(self, sentence: str) -> dict[str, Any]:
        return parse_recipe(sentence).as_dict()

    def windows(self) -> list[dict[str, Any]]:
        """Open windows, read through the backend like every other machine fact.

        Addresses are `window:<pid>:<exe>` with {title, monitor} as the value — so
        the Windows backend only has to implement `keys("window:")` and `read`.
        """
        out: list[dict[str, Any]] = []
        for addr in sorted(self.backend.keys("window:")):
            try:
                info = self.backend.read(addr) or {}
            except Exception:  # noqa: BLE001 - a window vanishing mid-read is normal
                continue
            parts = addr.split(":")
            out.append({"app": parts[2] if len(parts) > 2 else addr,
                        "title": info.get("title", ""),
                        "monitor": int(info.get("monitor", 0))})
        return out

    def save_workspace_capture(self, name: str) -> dict[str, Any]:
        """Snapshot what is open *right now* into a named workspace."""
        entries = self.windows()
        if not entries:
            return {"saved": False, "reason": (
                "No window history to capture yet — the console cannot see other "
                "programs without the Windows backend, and guessing would be worse "
                "than saying so.")}
        return {"saved": True, **self.save_workspace(name, entries)}

    def drains(self) -> dict[str, Any]:
        """Per-process power draw, read from the backend at `power:drain:<exe>`.

        On Windows this comes from the energy-estimation counters; here it comes
        from the simulated machine. Nothing is invented: a process with no
        reading is missing from the list, and the remainder is called
        "(unattributed)" rather than silently folded into the top entry.
        """
        from .power import attribute_drain
        readings: dict[str, float] = {}
        for addr in sorted(self.backend.keys("power:drain:")):
            try:
                val = self.backend.read(addr)
            except Exception:      # noqa: BLE001
                continue
            if isinstance(val, (int, float)):
                readings[addr.rsplit(":", 1)[-1]] = float(val)
        bat = self.battery()
        total = None
        if bat.get("available"):
            rate = bat["reading"].get("rate_mw") or 0
            total = abs(rate)
        if total is None and readings:
            total = sum(abs(v) for v in readings.values())
        if not total:
            return {"available": False,
                    "reason": "no power draw measured — needs a battery or the Windows backend",
                    "drains": []}
        return {"available": True, "total_mw": total,
                "measured_mw": sum(abs(v) for v in readings.values()),
                "drains": [d.as_dict() for d in attribute_drain(readings, total_mw=total)]}

    def power_summary(self) -> dict[str, Any]:
        return {"battery": self.battery(), "thermal": self.thermal(),
                "throttles": self.power.throttles(10),
                "history": self.power.history(40),
                "trend": self.power.health_trend(),
                "charge_limit": self.power.charge_limit(supported=False),
                "drains": self.drains()}

    def boot_status(self) -> dict[str, Any]:
        return {"events": self.guard.events(10),
                "mode": "read_only" if not self.guard.safe_to_write() else "normal"}

    def panic(self) -> dict[str, Any]:
        return self.guard.panic()

    def driver_before_install(self, driver_id: str) -> dict[str, Any]:
        return self.drivers.before_install(driver_id)

    def driver_report(self, version: str, outcome: str, note: str = "") -> dict[str, Any]:
        return self.drivers.report_outcome(version, outcome=outcome, note=note)

    def create_recipe_from_text(self, sentence: str) -> dict[str, Any]:
        """Parse, then persist. Saving is a separate, explicit act."""
        parsed = parse_recipe(sentence)
        if not parsed.understood:
            return {**parsed.as_dict(), "saved": False}
        rec = parsed.recipe or {}
        import uuid
        rid = "nl-" + uuid.uuid4().hex[:10]
        with self.conn:
            self.conn.execute(
                "INSERT INTO recipes(id,name,enabled,trigger_json,actions_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (rid, rec["name"], 1, json.dumps(rec["trigger"]),
                 json.dumps(rec["actions"]), self.clock()))
        return {"saved": True, "recipe_id": rid, "recipe": rec,
                "preview": parsed.preview}

    def run_recipes(self, trigger: str, context: dict[str, Any] | None = None
                    ) -> list[dict[str, Any]]:
        return self.recipes.fire(trigger, context or {})

    def recipes_list(self) -> list[dict[str, Any]]:
        return self.recipes.list()

    def toggle_recipe(self, recipe_id: str, enabled: bool) -> dict[str, Any]:
        self.recipes.set_enabled(recipe_id, enabled)
        return {"recipe_id": recipe_id, "enabled": enabled}

    # =================================================================== status
    def status(self) -> dict[str, Any]:
        safety = self.tick()
        latest = self.checker.latest_run()
        ok, rows, detail = self.engine.journal.verify_chain()
        vault = self.cleaner.vault_summary()
        return {
            "engine": {
                "journal_rows": rows, "journal_ok": ok, "journal_detail": detail,
                "managed": len(self.engine.managed()),
                "interrupted": len(self.engine.journal.interrupted()),
                "transactions": self.engine.history(5),
            },
            "checkup": latest,
            "bench": self.bench.history(5),
            "baseline": self.bench.baseline(),
            "cleaner": vault,
            "dns": self.dns.status(),
            "gaming": self.gaming.sessions(3),
            "studio": self.studio.budget(),
            "autopsy": self.autopsy.list(3),
            "consent": self.consent.decision().as_dict(),
            "startup": (lambda r: {"available": r.get("available", False),
                                   "current_ms": r.get("report", {}).get("total_ms"),
                                   "unattributed": r.get("report", {}).get("unattributed_items"),
                                   "saving_ms": r.get("plan", {}).get("saving_ms")})(
                self.startup_report()),
            "space": self.space.grew(7),
            "feed": self.sentinel.feed(8),
            "digest": self.sentinel.digest(),
            "receipts": self.receipts(10),
            "integrity": integrity(self.conn),
            "safety": safety,
            "pending_confirmations": self.pending_confirmations(),
        }

    def receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,at,kind,title,summary,tx_id,undone_at FROM receipts "
            "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "at": r[1], "kind": r[2], "title": r[3],
                 "summary": r[4], "tx_id": r[5], "undone": r[6] is not None}
                for r in rows]

    def snapshot(self, kind: str = "manual") -> dict[str, Any]:
        sn = self.engine.snapshots.capture(sorted(self.catalog.addresses()), kind=kind)
        return {"id": sn.id, "kind": sn.kind, "bytes": sn.size_bytes}

    # ------------------------------------------------------------------- extras
    def _space_roots(self) -> dict[str, str]:
        """Map the space sampler onto whatever this build actually has."""
        if getattr(self, "_demo", False):
            base = Path(self.cleaner.roots.get("temp", ".")).parent if hasattr(self, "cleaner") else Path(".")
            return {"Demo temp": str(Path(self.cleaner.roots["temp"]).parent),
                    "Demo logs": self.cleaner.roots["logs"]}
        home = Path.home()
        return {"Downloads": str(home / "Downloads"),
                "Documents": str(home / "Documents"),
                "AppData": str(home / "AppData" / "Local")}

    def _seed_demo_drivers(self) -> None:
        if self.conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]:
            return
        self.drivers.record([
            Driver("gpu:nvidia", "NVIDIA GeForce RTX 4070", "display", "566.36",
                   1_732_000_000_000, "NVIDIA"),
            Driver("gpu:nvidia:prev", "NVIDIA GeForce RTX 4070 (previous)", "display",
                   "561.09", 1_727_000_000_000, "NVIDIA"),
            Driver("audio:realtek", "Realtek High Definition Audio", "audio", "6.0.9",
                   1_700_000_000_000, "Realtek"),
            Driver("net:intel", "Intel Wi-Fi 6E AX211", "network", "23.40.0",
                   1_720_000_000_000, "Intel"),
            Driver("chipset:intel", "Intel Chipset Device", "chipset", "10.1.19",
                   1_690_000_000_000, "Intel"),
        ])
        # a bad driver with history, so known-good scoring has evidence to show
        self.drivers.report_outcome("566.36", outcome="crash", detail="game crash on launch")
        self.drivers.report_outcome("566.36", outcome="crash", detail="driver timeout")
        self.drivers.report_outcome("561.09", outcome="good", detail="stable week")

    def _seed_demo_space(self) -> None:
        """A simulated machine arrives with a week of history. A feature that only
        works after seven days of uptime is a feature nobody sees."""
        import json as _json
        if self.conn.execute("SELECT COUNT(*) FROM space_samples").fetchone()[0]:
            return
        base_free = 180_000_000_000      # 512 GB drive, ~115 GB used today
        total = 512_000_000_000
        now = self.clock()
        # days 6..1 ago, then a fresh sample now
        for days_ago in range(7, 0, -1):
            grown = (7 - days_ago) * 1_900_000_000          # ~1.9 GB/day
            folders = {
                "Downloads": {"bytes": 8_000_000_000 + (7 - days_ago) * 900_000_000,
                              "files": 400 + (7 - days_ago) * 12},
                "Demo temp": {"bytes": 42_000_000 + (7 - days_ago) * 2_000_000,
                              "files": 9},
                "Demo logs": {"bytes": 4_000_000 + (7 - days_ago) * 400_000, "files": 3},
                "AppData": {"bytes": 26_000_000_000 + (7 - days_ago) * 600_000_000,
                            "files": 52_000},
            }
            with self.conn:
                self.conn.execute(
                    "INSERT INTO space_samples(id,at,folders_json,free_bytes,total_bytes) "
                    "VALUES(?,?,?,?,?)",
                    (new_id(), now - days_ago * 86_400_000, _json.dumps(folders),
                     base_free - grown, total))

    def _seed_demo_junk(self, base: Path) -> dict[str, str]:
        """A simulated machine should contain simulated junk, otherwise the
        Cleaner page has nothing honest to show. These are real files on disk, so
        the vault round-trip (move → verify → restore) is exercised for real."""
        for sub in ("temp", "cache/thumbs", "cache/dns", "logs"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        blobs = {
            "temp": [("crashpad.dmp", 3_400_000), ("install.tmp", 12_800_000),
                     ("update.tmp", 26_100_000), ("chromium.tmp", 5_200_000)],
            "cache/thumbs": [("thumbcache_256.db", 8_400_000), ("thumbcache_96.db", 2_100_000)],
            "cache/dns": [("dns_cache.bin", 640_000)],
            "logs": [("setup.log", 1_200_000), ("installer.log", 340_000),
                     ("update.log", 2_600_000)],
        }
        for sub, files in blobs.items():
            for name, size in files:
                f = base / sub / name
                if not f.exists():
                    f.write_bytes(b"\0" * size)

        # a believable duplicate set: the same installer downloaded twice, one
        # copy in Downloads and one in a backup folder
        dupdir = base / "downloads"
        dupdir.mkdir(exist_ok=True)
        # each *pair* shares bytes; the two pairs are deliberately different, so a
        # finder that only compares sizes would be caught reporting them as one.
        pairs = {
            "game": ("downloads/game-setup.exe", "temp/game-setup (1).exe"),
            "tool": ("downloads/tool-installer.msi", "temp/tool-installer (2).msi"),
        }
        for tag, (first, second) in pairs.items():
            payload = (tag.encode() * (2 * 1024 * 1024 // len(tag) + 1))[:2 * 1024 * 1024]
            for rel in (first, second):
                f = base / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                if not f.exists():
                    f.write_bytes(payload)
        return {"temp": str(base / "temp"), "temp_system": str(base / "temp"),
                "cache": str(base / "cache"), "logs": str(base / "logs"),
                "downloads": str(base / "downloads")}

    def _install_demo_lists(self) -> None:
        if self.dns.total_blocked() == 0:
            self.dns.install_list("pco-ads", "PCO ads + trackers", "ads",
                                  DEMO_LIST.splitlines(), enabled=True)
            self.dns.install_list("pco-malware", "Malware + phishing", "malware",
                                  ["*.malware.test", "*.phishing.test"], enabled=True)

    def weekly_report(self) -> dict[str, Any]:
        feed = self.sentinel.since(int(time.time() * 1000) - 7 * 86_400_000)
        drift = self.conn.execute(
            "SELECT COUNT(*) FROM drift WHERE detected_at > ?",
            (int(time.time() * 1000) - 7 * 86_400_000,)).fetchone()[0]
        return {
            "events": len(feed),
            "by_kind": _count_by(feed, "kind"),
            "drift_detected": drift,
            "dns_blocked": self.dns.total_blocked(),
            "vault_bytes": self.cleaner.vault_summary()["bytes"],
            "headline": (f"{len(feed)} thing(s) happened this week · {drift} drift repair(s) · "
                         f"{self.dns.total_blocked()} trackers blocked"),
        }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i[key]] = out.get(i[key], 0) + 1
    return out


# ---------------------------------------------------------------- demo machine
DEMO_STATE = {
    "display:primary.mode": {"hz": 59.94, "width": 2560, "height": 1440},
    "display:primary.supported_hz": 165,
    "service:diagtrack.start_type": "auto",
    "power:active_scheme": "balanced",
    "registry:hklm\\software\\acme\\telemetry": 1,
    "startup:onedrive.defer_seconds": 0,
    "startup:steam.defer_seconds": 0,
    "task:\\microsoft\\office\\officebackgroundtaskhandler.state": "ready",
    "security:defender.realtime": True,
    # SMBv1 support flag, exactly where Windows keeps it. This is what
    # `security.smb1.disable` actually writes; an invented `security:smb1.enabled`
    # address would have made the checkup's fix fail against a real machine.
    "registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters": 1,
    "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection": "full",
    # open windows, so Workspaces can capture something on the demo machine.
    # `window:<pid>:<exe>` -> {title, monitor}
    "window:4120:Code.exe": {"title": "service.py — Pc-Optimizer", "monitor": 0},
    "window:4121:Code.exe": {"title": "console.html — Pc-Optimizer", "monitor": 1},
    "window:5310:chrome.exe": {"title": "PC Optimizer docs", "monitor": 0},
    "window:5311:chrome.exe": {"title": "GitHub — Not-MTN/Pc-Optimizer", "monitor": 1},
    "window:6204:Spotify.exe": {"title": "Spotify Premium", "monitor": 1},
    "window:7020:Discord.exe": {"title": "Discord", "monitor": 1},
    "window:8110:mspaint.exe": {"title": "concept.png", "monitor": 0},
    # per-process power draw in mW while discharging (Windows: energy estimation)
    "power:drain:chrome.exe": -4_100,
    "power:drain:Code.exe": -1_800,
    "power:drain:Spotify.exe": -900,
    "power:drain:Discord.exe": -700,
    "startup:items": [
        {"name": "OneDrive", "enabled": True, "cost_ms": 900},
        {"name": "Steam", "enabled": True, "cost_ms": 1400},
        {"name": "RGB Suite", "enabled": True, "cost_ms": 700},
        {"name": "Acme Updater", "enabled": True, "cost_ms": 600},
        {"name": "Cloud Sync", "enabled": True, "cost_ms": 1200},
        {"name": "Vendor Tray", "enabled": True, "cost_ms": 500},
        {"name": "Spotify", "enabled": True, "cost_ms": 400},
    ],
}

DEMO_METRICS = {
    "storage.junk_bytes": 42_300_000_000,
    "storage.dup_bytes": 18_400_000_000,
    "reliability.crash_count_7d": 3,
}

def DEMO_BATTERY() -> BatteryReading:
    """A laptop whose battery is worth mentioning: 78% health, ~106 cycles."""
    return BatteryReading(design_mwh=70_000, full_mwh=54_600, now_mwh=41_000,
                          rate_mw=-12_400, percent=75, plugged=False, cycles=106,
                          temp_c=31.0)


CLEAN_ROOTS = {
    "temp": "temp",
    "temp_system": "temp/system",
    "cache": "cache",
    "logs": "logs",
}

# ------------------------------------------------------------- action catalog
ACTIONS = {
    "checkup.run": lambda s, a: s.checkup(),
    "checkup.fix_safe": lambda s, a: s.checkup(fix_safe=True),
    "checkup.fix": lambda s, a: s.fix_by_consent(),
    "consent.set": lambda s, a: s.set_consent(level=a.get("level"),
                                              categories=a.get("categories"),
                                              trust_everything=bool(a.get("trust_everything"))),
    "consent.explain": lambda s, a: s.consent.explain(a["findings"]),
    "game.watch": lambda s, a: s.gaming.capture(a["game"], seconds=float(a.get("seconds", 45))),
    "game.sessions": lambda s, a: s.gaming.sessions(int(a.get("limit", 10)), a.get("game")),
    "game.plan": lambda s, a: s.gaming.plan(a["game"], a.get("session_id")),
    "game.profile": lambda s, a: s.gaming.profile(a["game"]) or {},
    "game.profile.save": lambda s, a: s.gaming.save_profile(a["game"], a["settings"]),
    "game.profile.export": lambda s, a: s.gaming.export_profile(a["game"]),
    "studio.themes": lambda s, a: {"themes": s.studio.themes(),
                                   "budget": s.studio.budget()},
    "studio.apply": lambda s, a: s.studio.apply(a["theme_id"], force=bool(a.get("force"))),
    "studio.import": lambda s, a: s.studio.import_doc(a["doc"]),
    "autopsy.begin": lambda s, a: {"autopsy_id": s.autopsy.begin(a.get("installer", "unknown.exe"))},
    "autopsy.erase": lambda s, a: s.autopsy.erase(a["autopsy_id"]),
    "startup.inspect": lambda s, a: s.startup_report(),
    "startup.apply": lambda s, a: s.startup_apply(mode=a.get("mode", "defer")),
    "palette.search": lambda s, a: {"results": s.palette.search(a.get("q", ""))},
    "palette.run": lambda s, a: s.palette.execute(a["verb"], a.get("args")),
    "ask": lambda s, a: s.ask_question(a["question"]),
    "space.sample": lambda s, a: s.space_sample(),
    "space.grew": lambda s, a: s.space_grew(int(a.get("days", 7))),
    "space.forecast": lambda s, a: s.space_forecast(),
    "dupes.scan": lambda s, a: s.find_duplicates(a.get("roots")),
    "report.md": lambda s, a: {"markdown": s.health_report("md")},
    "report.html": lambda s, a: {"html": s.health_report("html")},
    "report.json": lambda s, a: s.health_report("json"),
    "peripheral.summary": lambda s, a: s.peripheral.summary(),
    "battery.read": lambda s, a: s.battery(),
    "thermal.status": lambda s, a: s.thermal(),
    "workspace.list": lambda s, a: {"workspaces": [w.as_dict() for w in s.workspaces.list()]},
    "workspace.restore": lambda s, a: s.restore_workspace(a["id"], dry_run=bool(a.get("dry_run", True))),
    "drivers.list": lambda s, a: s.driver_inventory(),
    "drivers.block": lambda s, a: s.drivers.block(a["driver_id"], a["version"], reason=a["reason"]),
    "drivers.rollback": lambda s, a: s.drivers.rollback(a["install_id"]),
    "recipe.parse": lambda s, a: s.nl_recipe(a["sentence"]),
    "recipe.create": lambda s, a: s.create_recipe_from_text(a["sentence"]),
    "recipe.list": lambda s, a: {"recipes": s.recipes_list()},
    "recipe.toggle": lambda s, a: s.toggle_recipe(a["recipe_id"], bool(a["enabled"])),
    "recipe.delete": lambda s, a: (s.recipes.delete(a["recipe_id"]),
                                   {"deleted": a["recipe_id"]})[1],
    "recipe.fire": lambda s, a: {"runs": s.run_recipes(a.get("trigger", "manual"), a.get("context"))},
    "workspace.save": lambda s, a: s.save_workspace(a["name"], a.get("entries", [])),
    "windows.list": lambda s, a: {"windows": s.windows()},
    "workspace.capture": lambda s, a: s.save_workspace_capture(a["name"]),
    "workspace.delete": lambda s, a: (s.workspaces.delete(a["id"]), {"deleted": a["id"]})[1],
    "drivers.before_install": lambda s, a: s.driver_before_install(a["driver_id"]),
    "drivers.report": lambda s, a: s.driver_report(a["version"], a["outcome"], a.get("note", "")),
    "power.summary": lambda s, a: s.power_summary(),
    "power.sample": lambda s, a: s.battery(),
    "power.drains": lambda s, a: s.drains(),
    "thermal.sample": lambda s, a: {"recorded": s.power.record_thermal(
        float(a["before_hz"]), float(a["after_hz"]),
        temp_c=float(a["temp_c"]) if a.get("temp_c") is not None else None)},
    "boot.status": lambda s, a: s.boot_status(),
    "safety.panic": lambda s, a: s.panic(),
    "safety.tick": lambda s, a: s.tick(),
    "safety.confirm": lambda s, a: s.confirm(a["tx_id"]),
    "safety.pending": lambda s, a: {"pending": s.pending_confirmations()},
    "clean.scan": lambda s, a: s.clean_scan(),
    "clean.run": lambda s, a: s.clean_run(),
    "clean.restore": lambda s, a: s.clean_restore(a["run_id"]),
    "tweak.apply": lambda s, a: s.apply_fixes(a["tweaks"]),
    "tx.undo": lambda s, a: s.undo(a["tx_id"]),
    "snapshot.take": lambda s, a: s.snapshot(a.get("kind", "manual")),
    "bench.run": lambda s, a: s.bench_run(),
    "bench.compare": lambda s, a: s.bench.compare(a["a"], a["b"]),
    "dns.on": lambda s, a: {"mode": s.dns.on()},
    "dns.off": lambda s, a: {"mode": s.dns.off()},
    "dns.toggle": lambda s, a: {"mode": s.dns.toggle()},
    "dns.pause": lambda s, a: {"paused_until": s.dns.pause(int(a.get("minutes", 30)))},
    "dns.resume": lambda s, a: (s.dns.resume(), {"paused": False})[1],
    "dns.allow": lambda s, a: (s.dns.allow(a["domain"], a.get("app", "*")), {"ok": True})[1],
    "status": lambda s, a: s.status(),
    "report.weekly": lambda s, a: s.weekly_report(),
    "drift.check": lambda s, a: s.engine.check_drift(),
    "verify": lambda s, a: dict(zip(("ok", "rows", "detail"),
                                    s.engine.journal.verify_chain())),
}
