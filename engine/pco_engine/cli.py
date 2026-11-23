"""`python -m pco_engine.cli` — drive the Guardian without a UI.

Every command prints JSON when `--json` is passed, so the future WinUI shell,
scripts and reviewers all consume the same output.

    python -m pco_engine.cli --demo plan display.set_refresh
    python -m pco_engine.cli --demo apply display.set_refresh
    python -m pco_engine.cli --demo history
    python -m pco_engine.cli --demo crash           # power-loss simulation + recovery
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from typing import Any

from . import (Catalog, Engine, EngineError, FakeBackend, PreflightBlocked,
               connect, migrate)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG = os.path.join(HERE, "..", "catalog")

DEMO_STATE = {
    "display:primary.mode": {"hz": 59.94, "width": 2560, "height": 1440},
    "display:primary.supported_hz": 165,
    "service:diagtrack.start_type": "auto",
    "power:active_scheme": "balanced",
    "registry:hklm\\software\\acme\\telemetry": 1,
    "startup:onedrive.defer_seconds": 0,
    "startup:steam.defer_seconds": 0,
    "task:\\microsoft\\office\\officebackgroundtaskhandler.state": "ready",
}


def _clock() -> int:
    return int(time.time() * 1000)


def build(args) -> Engine:
    catalog = Catalog.load_dir(args.catalog)
    if args.backend == "demo":
        backend: Any = FakeBackend(DEMO_STATE)
    else:
        raise SystemExit(
            "The real Windows backend lands in engine/pco_engine/win/\n"
            "Until then use --backend demo, which runs the identical engine.")
    db = args.db or os.path.join(tempfile.gettempdir(), "pco-demo.db")
    if os.path.exists(db) and args.fresh:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(db + suffix)
            except FileNotFoundError:
                pass
    conn = connect(db)
    migrate(conn)
    eng = Engine(conn, backend, catalog, clock=_clock)
    return eng


def out(obj: Any, as_json: bool, human: str | None = None) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(human if human is not None else json.dumps(obj, indent=2, default=str))


# --------------------------------------------------------------------- commands

def cmd_status(eng: Engine, args) -> int:
    managed = eng.managed()
    ok, rows, detail = eng.journal.verify_chain()
    payload = {
        "managed_addresses": len(managed),
        "journal_rows": rows,
        "journal_intact": ok,
        "journal_detail": detail,
        "interrupted": [e.tx_id for e in eng.journal.interrupted()],
        "recent": eng.history(5),
        "snapshots": [s.id for s in eng.snapshots.list(3)],
    }
    human = (
        f"Guardian status\n"
        f"  journal      {rows} rows · {'intact' if ok else 'BROKEN — ' + detail}\n"
        f"  managed      {len(managed)} address(es)\n"
        f"  interrupted  {len(payload['interrupted'])}\n"
        f"  snapshots    {len(payload['snapshots'])}\n"
        f"  recent       {len(payload['recent'])} transaction(s)"
    )
    out(payload, args.json, human)
    return 0 if ok else 2


def cmd_plan(eng: Engine, args) -> int:
    try:
        plan = eng.preflight.simulate(args.tweaks, allow_tier=set(args.tiers.split(",")))
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}")
        return 2
    if not args.json:
        print(f"PLAN — {plan.summary}")
        for e in plan.effects:
            print(f"  {e.address}\n      {json.dumps(e.old)} -> {json.dumps(e.new)}")
        for i in plan.issues:
            mark = {"error": "✗", "warning": "!", "info": "·"}[i.severity]
            print(f"  {mark} [{i.code}] {i.message}")
        print("  → " + ("WOULD APPLY" if plan.ok else "BLOCKED"))
    else:
        out(plan.as_dict(), True)
    return 0 if plan.ok else 1


def cmd_apply(eng: Engine, args) -> int:
    try:
        res = eng.apply(args.tweaks, source="cli", require_confirm=args.confirm)
    except PreflightBlocked as exc:
        print("BLOCKED by preflight:")
        for i in exc.plan.errors:
            print(f"  ✗ {i.message}")
        return 1
    except EngineError as exc:
        print(f"error: {exc}")
        return 2
    payload = {
        "tx_id": res.tx_id,
        "summary": res.summary,
        "effects": [{"address": e.address, "old": e.old, "new": e.new} for e in res.effects],
        "snapshot_id": res.snapshot_id,
        "needs_reboot": res.needs_reboot,
        "armed_until": res.armed_until,
    }
    human = f"APPLIED — {res.summary}\n  tx {res.tx_id}"
    for e in res.effects:
        human += f"\n  {e.address}: {json.dumps(e.old)} -> {json.dumps(e.new)}"
    human += "\n  undo:  python -m pco_engine.cli revert " + res.tx_id
    out(payload, args.json, human)
    return 0


def cmd_revert(eng: Engine, args) -> int:
    res = eng.revert(args.tx_id, reason="cli")
    out({"tx_id": res.tx_id, "reverted": res.reverted, "failed": res.failed},
        args.json, f"REVERTED — {res.summary}")
    return 0


def cmd_history(eng: Engine, args) -> int:
    rows = eng.history(args.limit)
    if args.json:
        out(rows, True)
        return 0
    if not rows:
        print("no transactions yet")
    for r in rows:
        print(f"{r['started_at']}  {r['state']:<10} {r['id']}  ({r['source']})  {r['note'] or ''}")
    return 0


def cmd_snapshot(eng: Engine, args) -> int:
    addrs = list(eng.catalog.addresses())
    snap = eng.snapshots.capture(addrs, kind=args.kind, note=args.note)
    out({"id": snap.id, "kind": snap.kind, "size_bytes": snap.size_bytes,
         "addresses": len(addrs)}, args.json,
        f"SNAPSHOT {snap.id} — {len(addrs)} addresses, {snap.size_bytes} bytes ({snap.kind})")
    return 0


def cmd_diff(eng: Engine, args) -> int:
    diff = eng.snapshots.diff(args.a, args.b)
    out(diff, args.json,
        f"DIFF {args.a} -> {args.b}\n  changed {diff['counts']['changed']}"
        f" · added {diff['counts']['added']} · removed {diff['counts']['removed']}")
    return 0


def cmd_verify(eng: Engine, args) -> int:
    ok, rows, detail = eng.journal.verify_chain()
    out({"ok": ok, "rows": rows, "detail": detail}, args.json,
        f"journal: {'INTACT' if ok else 'BROKEN'} ({rows} rows) — {detail}")
    return 0 if ok else 2


def cmd_drift(eng: Engine, args) -> int:
    found = eng.check_drift(actor_guess=args.actor)
    out(found, args.json, "no drift detected" if not found else
        "\n".join(f"DRIFT {f['address']}: expected {json.dumps(f['expected'])} "
                  f"but found {json.dumps(f['actual'])}" for f in found))
    return 0 if not found else 1


def cmd_crash(eng: Engine, args) -> int:
    """Demonstrate the promise: kill the machine mid-batch, then recover."""
    backend = eng.backend
    n = {"i": 0}

    def die(_b, _addr):
        n["i"] += 1
        if n["i"] == 2:
            raise KeyboardInterrupt("simulated power loss")

    setattr(backend, "on_write", die)
    print("… applying a 3-item batch, killing power during the second write")
    try:
        eng.apply(["startup.defer.onedrive", "startup.defer.steam", "display.set_refresh"])
    except KeyboardInterrupt:
        print("… power lost. process exited without cleanup.")

    setattr(backend, "on_write", None)
    eng2 = Engine(eng.conn, backend, eng.catalog, clock=_clock)
    interrupted = eng2.journal.interrupted()
    print(f"… restart: {len(interrupted)} interrupted transaction(s) found")
    fixed = eng2.recover()
    print(f"… recovered {len(fixed)} write(s); everything is back to how it was")
    ok, rows, detail = eng2.journal.verify_chain()
    print(f"… journal {'intact' if ok else 'BROKEN'} ({rows} rows)")
    assert backend.read("startup:onedrive.defer_seconds") == 0
    assert backend.read("display:primary.mode")["hz"] == 59.94
    print("✓ nothing left behind. This is what 'you will never be stuck' means.")
    return 0


# ---------------------------------------------------------------- arg parsing

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pco", description="PC Optimizer Guardian (reference engine)")
    p.add_argument("--backend", default="demo", help="demo | windows (windows not implemented yet)")
    p.add_argument("--catalog", default=DEFAULT_CATALOG)
    p.add_argument("--db", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--fresh", action="store_true", help="delete the demo DB first")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("catalog")
    s.set_defaults(fn=lambda e, a: (out([t.as_dict() for t in e.catalog.all()], a.json,
                                        "\n".join(f"{t.id:<34} {t.tier}  {t.risk:<7} {t.title}"
                                                  for t in e.catalog.all())), 0)[1])
    s = sub.add_parser("plan"); s.add_argument("tweaks", nargs="+")
    s.add_argument("--tiers", default="T0,T1,T2,T3"); s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("apply"); s.add_argument("tweaks", nargs="+")
    s.add_argument("--confirm", action="store_true"); s.set_defaults(fn=cmd_apply)
    s = sub.add_parser("revert"); s.add_argument("tx_id"); s.set_defaults(fn=cmd_revert)
    s = sub.add_parser("history"); s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_history)
    s = sub.add_parser("snapshot"); s.add_argument("--kind", default="manual")
    s.add_argument("--note", default=None); s.set_defaults(fn=cmd_snapshot)
    s = sub.add_parser("diff"); s.add_argument("a"); s.add_argument("b"); s.set_defaults(fn=cmd_diff)
    s = sub.add_parser("verify"); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("drift"); s.add_argument("--actor", default=None); s.set_defaults(fn=cmd_drift)
    s = sub.add_parser("crash"); s.set_defaults(fn=cmd_crash)

    args = p.parse_args(argv)
    eng = build(args)
    return args.fn(eng, args)


if __name__ == "__main__":
    sys.exit(main())
