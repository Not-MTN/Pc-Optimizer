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
from .dns import DnsBlocker
from .engine import EngineError, PreflightBlocked
from .recipes import Recipes, Spec
from .sens import SensLab
from .sentinel import Event, Sentinel
from .store import connect, integrity, migrate

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

        self.checker = Checker(self.conn, Probe(self.backend, DEMO_METRICS), self.clock)
        self.bench = Bench(self.conn, self.clock)
        self.sentinel = Sentinel(self.conn, self.clock)
        self.dns = DnsBlocker(self.conn, self.clock)
        self._install_demo_lists()
        self.recipes = Recipes(self.conn, self.clock, run_action=self.run_action)
        self.sens = SensLab(self.conn, self.clock)

        self.recover_on_start()

    # ================================================================ lifecycle
    def recover_on_start(self) -> dict[str, Any]:
        """Run before anything else: an interrupted transaction must be repaired
        first, or every later decision is made on an unknown machine."""
        fixed = self.engine.recover()
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
            safe = [f for f in result.fixable_safe if f.fix_tweaks]
            applied = self.apply_fixes([t for f in safe for t in f.fix_tweaks],
                                       finding_ids=[f.id for f in safe])
            payload["fix"] = applied
        return payload

    def apply_fixes(self, tweak_ids: list[str], *, finding_ids: list[str] | None = None,
                    source: str = "ui") -> dict[str, Any]:
        ids = list(dict.fromkeys(tweak_ids))
        if not ids:
            return {"applied": False, "reason": "nothing to fix"}
        try:
            res = self.engine.apply(ids, source=source, kind="batch")
        except PreflightBlocked as exc:
            return {"applied": False, "blocked": [i.as_dict() for i in exc.plan.errors]}
        except EngineError as exc:
            return {"applied": False, "error": str(exc)}
        if finding_ids:
            self.checker.mark_fixed(finding_ids, res.tx_id)
        self.sentinel.record(Event(
            kind="fix", source="engine", severity="info",
            narrative=f"Applied {len(res.effects)} fix(es) — undo is one click.",
            payload={"tx_id": res.tx_id}, tx_id=res.tx_id))
        return {"applied": True, **{k: v for k, v in {
            "tx_id": res.tx_id, "summary": res.summary, "snapshot_id": res.snapshot_id,
            "needs_reboot": res.needs_reboot,
            "effects": [{"address": e.address, "old": e.old, "new": e.new}
                        for e in res.effects]}.items()}}

    def undo(self, tx_id: str) -> dict[str, Any]:
        res = self.engine.revert(tx_id, reason="ui")
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

    # =================================================================== status
    def status(self) -> dict[str, Any]:
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
            "feed": self.sentinel.feed(8),
            "digest": self.sentinel.digest(),
            "receipts": self.receipts(10),
            "integrity": integrity(self.conn),
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
        return {"temp": str(base / "temp"), "temp_system": str(base / "temp"),
                "cache": str(base / "cache"), "logs": str(base / "logs")}

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
    "security:smb1.enabled": True,
    "privacy:telemetry.level": "full",
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
