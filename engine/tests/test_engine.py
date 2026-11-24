"""Apply / revert / crash-recovery. These are the tests that justify the
product's central promise: you will never be stuck."""
import sqlite3
import pytest

from pco_engine import Engine, EngineError, PreflightBlocked, connect, migrate


def test_apply_single_tweak(engine, backend):
    res = engine.apply("display.set_refresh")
    assert backend.read("display:primary.mode") == {"hz": 165}
    assert len(res.effects) == 1
    assert engine.transaction(res.tx_id)["state"] == "committed"


def test_journal_records_full_lifecycle(engine):
    res = engine.apply("display.set_refresh")
    phases = [e.phase for e in engine.journal.entries(res.tx_id)]
    assert phases == ["begin", "applied", "verified"]


def test_journal_captures_prestate(engine):
    res = engine.apply("display.set_refresh")
    entries = engine.journal.entries(res.tx_id)
    assert entries[0].prestate == {
        "display:primary.mode": {"hz": 59.94, "width": 2560, "height": 1440}}


def test_revert_restores_exact_value(engine, backend):
    before = backend.read("display:primary.mode")
    res = engine.apply("display.set_refresh")
    assert backend.read("display:primary.mode") != before
    engine.revert(res.tx_id)
    assert backend.read("display:primary.mode") == before
    assert engine.transaction(res.tx_id)["state"] == "reverted"


def test_revert_clears_managed_projection(engine):
    res = engine.apply("display.set_refresh")
    assert "display:primary.mode" in engine.managed()
    engine.revert(res.tx_id)
    assert "display:primary.mode" not in engine.managed()


def test_journal_survives_revert_and_stays_append_only(engine):
    res = engine.apply("display.set_refresh")
    engine.revert(res.tx_id)
    phases = [e.phase for e in engine.journal.entries(res.tx_id)]
    assert "reverted" in phases
    ok, _, detail = engine.journal.verify_chain()
    assert ok, detail


def test_preflight_blocks_bad_apply(engine, backend):
    before = backend.snapshot()
    with pytest.raises(PreflightBlocked):
        engine.apply("does.not.exist")
    assert backend.snapshot() == before


def test_noop_apply_refused(engine):
    engine.apply("display.set_refresh")
    with pytest.raises(EngineError, match="nothing to do"):
        engine.apply("display.set_refresh")


def test_batch_apply_creates_one_transaction(engine):
    res = engine.apply(["display.set_refresh", "startup.defer.onedrive",
                        "startup.defer.steam"])
    assert len(res.effects) == 3
    assert len({e.tx_id for e in engine.journal.entries(res.tx_id)}) == 1
    assert engine.transaction(res.tx_id)["state"] == "committed"


def test_batch_takes_snapshot(engine):
    res = engine.apply(["startup.defer.onedrive", "startup.defer.steam"])
    assert res.snapshot_id is not None


def test_high_risk_requires_confirmation_and_arms(engine):
    res = engine.apply("security.smb1.disable", require_confirm=True)
    assert res.armed_until is not None
    assert engine.transaction(res.tx_id)["state"] == "armed"


def test_armed_transaction_auto_reverts_when_expired(engine, clock, backend):
    before = backend.read("registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters")
    res = engine.apply("security.smb1.disable", require_confirm=True)
    assert backend.read("registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters") != before

    clock.tick(engine.confirm_window_ms + 1)
    expired = engine.expire_armed()
    assert expired == [res.tx_id]
    assert backend.read("registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters") == before


def test_confirm_disarms_autorevert(engine, clock):
    res = engine.apply("security.smb1.disable", require_confirm=True)
    engine.confirm(res.tx_id)
    clock.tick(engine.confirm_window_ms + 1)
    assert engine.expire_armed() == []
    assert engine.transaction(res.tx_id)["state"] == "committed"


# ---------------------------------------------------------------- crash paths

def test_power_loss_mid_batch_is_recovered(engine, backend, catalog, tmp_path, clock):
    """Simulate the machine dying after the first write of a 3-write batch."""
    db = str(tmp_path / "pco.db")
    conn = connect(db)
    migrate(conn, clock=clock)

    writes = {"n": 0}
    b = backend

    def die_after_first(_backend, _address):
        writes["n"] += 1
        if writes["n"] == 2:
            raise KeyboardInterrupt("simulated power loss")

    b.on_write = die_after_first

    eng = Engine(conn, b, catalog, clock=clock)
    with pytest.raises(KeyboardInterrupt):
        eng.apply(["startup.defer.onedrive", "startup.defer.steam", "display.set_refresh"])

    # A *fresh* process starts up: same DB, and recovery runs before anything else.
    b.on_write = None
    conn2 = connect(db)
    migrate(conn2, clock=clock)
    eng2 = Engine(conn2, b, catalog, clock=clock)

    interrupted = eng2.journal.interrupted()
    assert interrupted, "the killed batch must be visible as an interrupted transaction"

    fixed = eng2.recover()
    assert fixed
    # every value is back to its original state, and the chain is intact
    assert b.read("startup:onedrive.defer_seconds") == 0
    assert b.read("startup:steam.defer_seconds") == 0
    ok, _, detail = eng2.journal.verify_chain()
    assert ok, detail
    assert eng2.journal.interrupted() == []


def test_failed_write_rolls_back_earlier_writes(engine, backend):
    backend.fail_on_write["startup:steam.defer_seconds"] = "service refused the change"
    with pytest.raises(EngineError, match="service refused"):
        engine.apply(["startup.defer.onedrive", "startup.defer.steam"])
    # the write that succeeded before the failure must not be left behind
    assert backend.read("startup:onedrive.defer_seconds") == 0


def test_failed_write_is_journaled_as_failed(engine, backend):
    backend.fail_on_write["startup:steam.defer_seconds"] = "nope"
    with pytest.raises(EngineError):
        engine.apply(["startup.defer.onedrive", "startup.defer.steam"])
    phases = [e.phase for e in engine.journal.entries()]
    assert "failed" in phases


def test_value_that_does_not_stick_is_detected(engine, backend):
    """A policy (e.g. group policy) may override the write. We must notice."""
    backend.pin("display:primary.mode", {"hz": 60})
    engine.apply("display.set_refresh")
    results = [e for e in engine.journal.entries() if e.tweak_id == "display.set_refresh"]
    assert any(e.phase == "failed" for e in results)
    assert not any(e.phase == "verified" for e in results)


# --------------------------------------------------------------------- drift

def test_drift_detected_when_outside_world_changes_value(engine, backend):
    engine.apply("display.set_refresh")
    backend.poke("display:primary.mode", {"hz": 60})   # Windows Update, say

    drift = engine.check_drift(actor_guess="WindowsUpdate")
    assert len(drift) == 1
    assert drift[0]["address"] == "display:primary.mode"
    assert drift[0]["actual"] == {"hz": 60}
    assert "display:primary.mode" not in engine.managed()


def test_no_false_drift_when_state_matches(engine):
    engine.apply("display.set_refresh")
    assert engine.check_drift() == []


# -------------------------------------------------------------- transactions

def test_history_is_newest_first(engine):
    a = engine.apply("display.set_refresh")
    b = engine.apply("startup.defer.onedrive")
    history = engine.history()
    assert [h["id"] for h in history] == [b.tx_id, a.tx_id]


def test_unknown_transaction_raises(engine):
    with pytest.raises(EngineError):
        engine.transaction("NOPE")


def test_reverts_only_touch_their_own_transaction(engine, backend):
    a = engine.apply("display.set_refresh")
    b = engine.apply("startup.defer.onedrive")
    engine.revert(a.tx_id)
    assert backend.read("startup:onedrive.defer_seconds") == 60
    assert engine.transaction(b.tx_id)["state"] == "committed"
