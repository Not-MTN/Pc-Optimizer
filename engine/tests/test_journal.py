"""Journal properties. These tests are the reason the product can promise
'you will never be stuck' — if any of them fail, that promise is false."""
import pytest

from pco_engine import GENESIS, JournalError, compute_checksum


def test_genesis_is_64_zeros():
    assert GENESIS == "0" * 64


def test_append_chains_checksums(engine):
    j = engine.journal
    j.ensure_transaction("TX1")
    e1 = j.append(tx_id="TX1", tweak_id="a.b", phase="begin", prestate={"x": 1})
    e2 = j.append(tx_id="TX1", tweak_id="a.b", phase="applied", prestate={"x": 1})
    assert e1.prev_checksum == GENESIS
    assert e2.prev_checksum == e1.checksum
    assert e1.seq == 1 and e2.seq == 2


def test_checksum_covers_every_field():
    base = dict(prev_checksum=GENESIS, seq=1, tx_id="T", tweak_id="a",
                phase="begin", at=10, prestate="{}", poststate=None, error=None)
    digest = compute_checksum(**base)
    for field, other in [("tx_id", "T2"), ("tweak_id", "b"), ("phase", "applied"),
                         ("at", 11), ("prestate", '{"x":1}'), ("poststate", "{}"),
                         ("error", "boom"), ("prev_checksum", "f" * 64), ("seq", 2)]:
        changed = dict(base)
        changed[field] = other
        assert compute_checksum(**changed) != digest, f"{field} is not covered by the checksum"


def test_chain_verifies_when_intact(engine):
    j = engine.journal
    j.ensure_transaction("TX")
    for i in range(5):
        j.append(tx_id="TX", tweak_id=f"t{i}", phase="begin", prestate={"i": i})
    ok, rows, detail = j.verify_chain()
    assert ok, detail
    assert rows == 5


def test_tampering_is_detected(engine):
    j = engine.journal
    j.ensure_transaction("TX")
    j.append(tx_id="TX", tweak_id="a", phase="begin", prestate={"x": 1})
    j.append(tx_id="TX", tweak_id="a", phase="applied", prestate={"x": 1})

    # The DB triggers block UPDATE; bypass them exactly like a malicious writer would
    engine.conn.execute("DROP TRIGGER journal_no_update")
    engine.conn.execute("UPDATE journal SET prestate_json='{\"x\":999}' WHERE seq=1")

    ok, row, detail = j.verify_chain()
    assert not ok and row == 1
    assert "modified" in detail


def test_truncation_is_detected(engine):
    j = engine.journal
    j.ensure_transaction("TX")
    for i in range(3):
        j.append(tx_id="TX", tweak_id=f"t{i}", phase="begin", prestate={"i": i})

    engine.conn.execute("DROP TRIGGER journal_no_delete")
    engine.conn.execute("DELETE FROM journal WHERE seq=3")

    ok, _row, detail = j.verify_chain()
    assert not ok
    assert "removed from the end" in detail


def test_storage_layer_blocks_update_and_delete(engine):
    j = engine.journal
    j.ensure_transaction("TX")
    j.append(tx_id="TX", tweak_id="a", phase="begin", prestate={"x": 1})
    with pytest.raises(Exception):
        engine.conn.execute("UPDATE journal SET error='x'")
    with pytest.raises(Exception):
        engine.conn.execute("DELETE FROM journal")


def test_bad_phase_rejected(engine):
    engine.journal.ensure_transaction("TX")
    with pytest.raises(JournalError):
        engine.journal.append(tx_id="TX", tweak_id="a", phase="nonsense", prestate={})


def test_interrupted_detects_unmatched_begin(engine):
    j = engine.journal
    j.ensure_transaction("CRASHED", state="open")       # process died mid-batch
    j.ensure_transaction("FINISHED", state="committed") # completed normally
    j.append(tx_id="CRASHED", tweak_id="a", phase="begin", prestate={"x": 1})
    j.append(tx_id="CRASHED", tweak_id="a", phase="applied", prestate={"x": 1})
    j.append(tx_id="FINISHED", tweak_id="b", phase="begin", prestate={"y": 1})
    j.append(tx_id="FINISHED", tweak_id="b", phase="applied", prestate={"y": 1})

    assert j.open_transactions() == ["CRASHED"]
    assert len(j.interrupted()) == 1


def test_open_transaction_with_failure_is_not_interrupted(engine):
    j = engine.journal
    j.ensure_transaction("FAILED")
    j.append(tx_id="FAILED", tweak_id="a", phase="begin", prestate={"x": 1})
    j.append(tx_id="FAILED", tweak_id="a", phase="failed", prestate={"x": 1}, error="nope")
    assert j.open_transactions() == []


def test_committed_transaction_is_not_interrupted(engine):
    """Regression: a normal apply must not look like a crash on next startup."""
    applied = engine.apply("display.set_refresh")
    assert applied.tx_id not in engine.journal.open_transactions()
    assert engine.journal.interrupted() == []


def test_armed_transaction_is_not_interrupted(engine):
    """An armed transaction is waiting for the user, not crashed."""
    applied = engine.apply("security.smb1.disable", require_confirm=True)
    assert applied.tx_id not in engine.journal.open_transactions()


def test_reopened_engine_sees_no_false_interruptions(engine, catalog, clock, tmp_path):
    """Simulate an app restart: same DB, fresh engine object."""
    from pco_engine import Engine, connect, migrate
    engine.apply("display.set_refresh")
    db = engine.conn.execute("PRAGMA database_list").fetchone()[2]
    conn2 = connect(db)
    migrate(conn2, clock=clock)
    eng2 = Engine(conn2, engine.backend, catalog, clock=clock)
    assert eng2.journal.interrupted() == []
    assert eng2.recover() == []
