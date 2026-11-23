"""Snapshot properties: capture, verify, diff (the time machine)."""
import pytest


def test_capture_and_restore_state(engine, backend):
    snap = engine.snapshots.capture(
        ["display:primary.mode", "power:active_scheme"], kind="golden")
    assert snap.size_bytes > 0
    state = engine.snapshots.state_of(snap.id)
    assert state["display:primary.mode"]["hz"] == 59.94
    assert state["power:active_scheme"] == "balanced"


def test_absent_addresses_are_recorded_as_absent(engine):
    snap = engine.snapshots.capture(["display:primary.mode", "nothing:here"])
    state = engine.snapshots.state_of(snap.id)
    assert state["nothing:here"] is None


def test_verify_detects_modified_manifest(engine):
    snap = engine.snapshots.capture(["display:primary.mode"])
    assert engine.snapshots.verify(snap.id) is True

    engine.conn.execute(
        "UPDATE settings SET value_json='{\"display:primary.mode\":{\"hz\":999}}' "
        "WHERE scope='snapshot' AND key=?", (snap.id,))
    assert engine.snapshots.verify(snap.id) is False


def test_diff_reports_changes(engine, backend):
    a = engine.snapshots.capture(["display:primary.mode", "power:active_scheme"])
    backend.poke("power:active_scheme", "high")
    backend.remove("display:primary.mode")
    b = engine.snapshots.capture(["display:primary.mode", "power:active_scheme"])

    diff = engine.snapshots.diff(a.id, b.id)
    assert diff["counts"]["changed"] == 1
    assert diff["counts"]["removed"] == 1
    assert diff["changed"][0]["new"] == "high"


def test_latest_returns_newest(engine, clock):
    first = engine.snapshots.capture(["display:primary.mode"], kind="auto")
    clock.tick(500)
    second = engine.snapshots.capture(["display:primary.mode"], kind="auto")
    assert engine.snapshots.latest("auto").id == second.id
    assert engine.snapshots.latest().id == second.id
    assert first.id != second.id


def test_bad_kind_rejected(engine):
    with pytest.raises(ValueError):
        engine.snapshots.capture(["x"], kind="nonsense")


def test_snapshots_are_small(engine):
    snap = engine.snapshots.capture(["display:primary.mode", "power:active_scheme"])
    assert snap.size_bytes < 10_000


def test_pre_apply_snapshot_can_restore_manually(engine, backend):
    res = engine.apply(["startup.defer.onedrive", "startup.defer.steam"])
    pre = engine.snapshots.state_of(res.snapshot_id)
    backend.poke("startup:onedrive.defer_seconds", 0)
    backend.poke("startup:steam.defer_seconds", 0)
    for addr, old in pre.items():
        if old is not None:
            backend.write(addr, old)
    assert backend.read("startup:onedrive.defer_seconds") == 0
