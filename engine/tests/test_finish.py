"""The last planned modules: power, workspaces, drivers, NL recipes, guard.

The rules these protect, in the order they matter:

  guard      — a broken journal means read-only, never "helpfully repair"
  panic      — blunt and total, because a panic key that asks is not a panic key
  tick       — an armed change is undone even if the app was closed
  power      — a battery below 80% is called worn, and charge limit says unsupported
  drivers    — "no history" is not "good"
  workspace  — a dry run is the default; a failed launch is reported per item
  nlrecipe   — a sentence it does not understand names what failed
"""
from __future__ import annotations

import pytest

from pco_engine.drivers import Driver, DriverManager
from pco_engine.guard import MAX_MODE, NORMAL_MODE, PANIC_HOTKEY, Guard
from pco_engine.nlrecipe import parse
from pco_engine.power import (BatteryReading, Power, attribute_drain, diagnose,
                             throttled)
from pco_engine.workspace import Entry, Workspaces


# ==================================================================== battery

def _battery(**kw):
    base = dict(design_mwh=70_000, full_mwh=64_000, now_mwh=50_000, rate_mw=-12_000,
                percent=80, plugged=False, cycles=90, temp_c=30.0)
    base.update(kw)
    return BatteryReading(**base)


def test_health_is_capacity_against_design():
    assert _battery().health_pct == pytest.approx(91.4, abs=0.1)


def test_a_worn_battery_is_called_worn():
    diag = diagnose(_battery(full_mwh=52_000))
    assert diag["level"] == "warn"
    assert "74%" in diag["verdict"]
    assert diag["advice"].startswith("Expect roughly")


def test_an_end_of_life_battery_is_said_plainly():
    diag = diagnose(_battery(full_mwh=35_000))
    assert diag["level"] == "bad"
    assert "end of life" in diag["verdict"]
    assert "Replace the battery" in diag["advice"]


def test_over_100_percent_is_flagged_as_an_artefact_not_a_bonus():
    diag = diagnose(_battery(full_mwh=76_000))
    assert diag["level"] == "warn"
    assert "not extra capacity" in diag["verdict"]
    assert "Calibrate" in diag["advice"]


def test_minutes_left_is_computed_from_actual_draw():
    b = _battery(now_mwh=50_000, rate_mw=-10_000)
    assert b.minutes_left == 300        # 50 Wh at 10 W = 5 hours


def test_plugged_in_has_no_minutes_left():
    assert _battery(plugged=True).minutes_left is None


def test_drain_shares_are_of_the_measured_total():
    drains = attribute_drain({"chrome.exe": -6000, "steam.exe": -2000},
                             total_mw=12_000)
    top = drains[0]
    assert top.process == "chrome.exe"
    assert top.share_pct == pytest.approx(50.0)
    assert "Hardware-accelerated" in top.note


def test_unattributed_drain_is_named_not_hidden():
    drains = attribute_drain({"chrome.exe": -3000}, total_mw=12_000)
    leftover = [d for d in drains if d.process == "(unattributed)"]
    assert leftover, "the missing 9 W must appear as its own row"
    assert "not one process" in leftover[0].note


def test_throttling_requires_heat_and_a_clock_drop():
    assert throttled(2000, 1200, 96) is True
    assert throttled(2000, 1200, 70) is False      # slower but cool: just load
    assert throttled(2000, 1950, 96) is False      # hot but not throttled yet


def test_charge_limit_is_honest_when_unsupported(engine):
    out = Power(engine.conn, engine.clock).charge_limit(supported=False)
    assert out["supported"] is False
    assert "will not pretend" in out["alternative"]


def test_charge_limit_validates_its_range(engine):
    p = Power(engine.conn, engine.clock)
    assert p.charge_limit(supported=True, set_to=80)["set_to"] == 80
    with pytest.raises(ValueError):
        p.charge_limit(supported=True, set_to=20)


def test_throttle_event_is_recorded_and_summarised(engine):
    p = Power(engine.conn, engine.clock)
    ev = p.record_thermal(2400, 1400, 97, reason="sustained load")
    assert ev["drop_pct"] == pytest.approx(41.7, abs=0.1)
    verdict = p.thermal_verdict()
    assert verdict["throttled"] is True
    assert "Clean the fans" in verdict["advice"]


def test_a_cool_machine_records_no_throttle(engine):
    p = Power(engine.conn, engine.clock)
    assert p.record_thermal(2400, 2300, 65) is None
    assert p.thermal_verdict()["throttled"] is False


def test_desktop_says_no_battery_rather_than_zero(engine):
    p = Power(engine.conn, engine.clock, reader=lambda: None)
    out = p.sample()
    assert out["available"] is False
    assert "desktop" in out["reason"]


def test_demo_machine_reports_a_worn_battery(service):
    out = service.battery()
    assert out["available"] is True
    assert out["diagnosis"]["level"] == "warn"
    assert out["diagnosis"]["cycles"] == 106


# ================================================================= workspaces

def test_saving_requires_a_name_and_content(engine):
    w = Workspaces(engine.conn, engine.clock)
    with pytest.raises(ValueError):
        w.save("", [Entry("a.exe")])
    with pytest.raises(ValueError):
        w.save("Work", [])


def test_round_trip_keeps_entry_details(engine):
    w = Workspaces(engine.conn, engine.clock)
    ws = w.save("Work", [Entry("code.exe", ("--new-window",), monitor=1,
                               profile="power.scheme.high", title="VS Code")])
    again = w.get(ws.id)
    assert again.entries[0].monitor == 1
    assert again.entries[0].args == ("--new-window",)
    assert again.entries[0].profile == "power.scheme.high"


def test_restore_defaults_to_dry_run(engine):
    started = []
    w = Workspaces(engine.conn, engine.clock, launcher=lambda e: started.append(e))
    ws = w.save("Study", [Entry("notes.exe"), Entry("music.exe")])
    out = w.restore(ws.id)
    assert out["dry_run"] is True
    assert len(out["would_launch"]) == 2
    assert started == [], "a dry run must not launch anything"


def test_a_real_restore_reports_each_item(engine):
    def launcher(entry):
        if "broken" in entry.app:
            raise OSError("not found")

    w = Workspaces(engine.conn, engine.clock, launcher=launcher)
    ws = w.save("Mixed", [Entry("fine.exe"), Entry("broken.exe")])
    out = w.restore(ws.id, dry_run=False)
    assert out["started"] == 1
    assert len(out["failed"]) == 1
    assert "not found" in out["failed"][0]["error"]
    assert "1 failed" in out["summary"]


def test_no_launcher_is_reported_not_faked(engine):
    w = Workspaces(engine.conn, engine.clock)
    ws = w.save("X", [Entry("a.exe")])
    out = w.restore(ws.id, dry_run=False)
    assert out["started"] == 0
    assert "no launcher" in out["failed"][0]["error"]


def test_capture_hint_offers_after_a_repeated_sequence(engine):
    w = Workspaces(engine.conn, engine.clock)
    history = [{"kind": "tweak", "summary": s} for s in
               ("alpha", "beta", "gamma", "alpha", "beta", "gamma")]
    hint = w.capture_hint(history)
    assert hint and hint["offer"] is True
    assert "Save them as a recipe?" in hint["narrative"]


def test_no_hint_without_a_repeat(engine):
    w = Workspaces(engine.conn, engine.clock)
    history = [{"kind": "tweak", "summary": f"c{i}"} for i in range(8)]
    assert w.capture_hint(history) is None


def test_unknown_workspace_raises(engine):
    with pytest.raises(KeyError):
        Workspaces(engine.conn, engine.clock).get("nope")


# ==================================================================== drivers

def _dm(engine):
    return DriverManager(engine.conn, engine.clock, snapshots=lambda note: "snap-1")


def test_no_history_is_not_a_recommendation(engine):
    s = _dm(engine).score("9.9.9")
    assert s["grade"] == "unknown"
    assert "not a recommendation" in s["verdict"]


def test_a_version_with_crashes_is_graded_bad(engine):
    dm = _dm(engine)
    dm.report_outcome("566.36", outcome="crash")
    dm.report_outcome("566.36", outcome="crash")
    dm.report_outcome("566.36", outcome="good")
    s = dm.score("566.36")
    assert s["grade"] == "bad"
    assert "ended in a crash" in s["verdict"]


def test_a_clean_version_is_good(engine):
    dm = _dm(engine)
    dm.report_outcome("561.09", outcome="good")
    assert dm.score("561.09")["grade"] == "good"


def test_bad_outcome_is_rejected(engine):
    with pytest.raises(ValueError):
        _dm(engine).report_outcome("x", outcome="meh")


def test_before_install_takes_a_snapshot(engine):
    dm = _dm(engine)
    dm.record([Driver("d1", "GPU", "display", "566.36", 0)])
    out = dm.before_install("d1")
    assert out["snapshot_id"] == "snap-1"
    assert out["note"].startswith("Settings this app manages")


def test_block_writes_a_tombstone(engine):
    dm = _dm(engine)
    dm.record([Driver("d1", "GPU", "display", "566.36", 0)])
    dm.block("d1", "566.36", reason="crashes")
    assert dm.blocks()[0]["version"] == "566.36"
    assert "no driver files were touched" in dm.block("d1", "566.36", reason="x")["note"]


def test_rollback_is_honest_about_what_it_does_not_do(engine):
    dm = _dm(engine)
    dm.record([Driver("d1", "GPU", "display", "566.36", 0)])
    inst = dm.before_install("d1")
    row = engine.conn.execute("SELECT id FROM driver_installs").fetchone()[0]
    out = dm.rollback(row)
    assert "Device Manager, not by this app" in out["honesty"]
    assert out["restored_snapshot"] == "snap-1"


def test_inventory_scores_every_driver(service):
    inv = service.driver_inventory()
    assert len(inv["drivers"]) >= 5
    grades = {d["score"]["grade"] for d in inv["drivers"]}
    assert "bad" in grades and "good" in grades
    assert "unknown" in grades            # silence is a grade, not a pass
    bad = next(d for d in inv["drivers"] if d["score"]["grade"] == "bad")
    assert "crash" in bad["score"]["verdict"]


# ================================================================ nl recipes

def test_clean_with_an_age():
    p = parse("clean downloads older than 30 days")
    assert p.understood and p.intent == "clean_now"
    act = p.recipe["actions"][1]
    assert act["older_than_days"] == 30
    assert "{downloads}" in act["scope"]


def test_game_trigger_with_minutes():
    p = parse("when a game starts, pause the blocker for 60 minutes")
    assert p.intent == "game_pause_blocker"
    assert p.recipe["actions"][0]["minutes"] == 60


def test_game_trigger_defaults_to_30_minutes():
    p = parse("when a game launches, turn off the blocker")
    assert p.recipe["actions"][0]["minutes"] == 30


def test_daily_schedule():
    p = parse("every day at 03:00, run a checkup")
    assert p.intent == "schedule"
    assert p.recipe["trigger"]["match"]["at"] == "03:00"


def test_impossible_time_is_refused():
    p = parse("every day at 29:90, run a checkup")
    assert not p.understood
    assert "not a valid time" in p.problems[0]


def test_disk_threshold():
    p = parse("when disk is above 90%, clean temp files")
    assert p.intent == "disk_threshold"
    assert p.recipe["trigger"]["match"]["above_pct"] == 90


def test_unknown_thing_lists_what_is_known():
    p = parse("clean my feelings")
    assert not p.understood
    assert any("I know:" in x for x in p.problems)


def test_totally_unknown_sentence_teaches_the_grammar():
    p = parse("make me a sandwich")
    assert not p.understood
    joined = " ".join(p.problems)
    assert "clean downloads older than 30 days" in joined


def test_every_parse_includes_a_preview():
    p = parse("clean temp files")
    assert p.preview and "vault" in " ".join(p.preview)


def test_saving_is_a_separate_explicit_act(service):
    parsed = service.nl_recipe("clean downloads older than 30 days")
    assert parsed["understood"] is True
    assert service.recipes_list() == [], "parsing must not save anything"
    out = service.create_recipe_from_text("clean downloads older than 30 days")
    assert out["saved"] is True
    assert service.recipes_list()


def test_unparseable_sentence_saves_nothing(service):
    out = service.create_recipe_from_text("do something vague")
    assert out["saved"] is False
    assert service.recipes_list() == []


# ====================================================================== guard

def test_clean_boot_is_normal_mode(service):
    g = Guard(service)
    out = g.boot()
    assert out["mode"] == NORMAL_MODE
    assert out["journal_ok"] is True


def test_a_broken_journal_forces_read_only(service):
    """The single most important rule in the app: never 'repair' a broken chain.

    The realistic tamper is *editing* an existing row, which is also what the
    append-only triggers exist to make hard — so this goes through the trigger by
    rebuilding the table, exactly as a hostile process with file access would.
    """
    service.apply_fixes(["display.set_refresh"])
    assert service.engine.journal.verify_chain()[0] is True

    # drop the append-only triggers, tamper, put them back: a file-level attacker
    conn = service.conn
    trig = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='journal'")]
    with conn:
        for t in trig:
            conn.execute(f'DROP TRIGGER "{t}"')
        conn.execute("UPDATE journal SET poststate_json='{\"hz\":999}' "
                     "WHERE phase='verified'")
    g = Guard(service)
    out = g.boot()
    assert out["mode"] == MAX_MODE
    assert out["journal_ok"] is False
    assert "will not change this machine" in out["reason"]
    assert g.safe_to_write() is False


def test_panic_reverts_everything_and_stops_automation(service):
    service.apply_fixes(["display.set_refresh"])
    service.recipes.add(__import__("pco_engine.recipes", fromlist=["Spec"]).Spec(
        id="r1", name="auto", trigger={"kind": "manual"}, actions=[{"action": "clean.run"}]))
    service.dns.on()

    out = service.guard.panic()
    assert len(out["reverted"]) == 1
    assert service.backend.read("display:primary.mode")["hz"] == 59.94
    assert out["dns_mode"] == "off"
    assert out["recipes_disabled"] == ["r1"]
    assert service.recipes_list()[0]["enabled"] is False


def test_panic_is_recorded_as_critical(service):
    service.guard.panic()
    kinds = [e["kind"] for e in service.sentinel.feed(10)]
    assert "panic" in kinds
    assert service.guard.events()[0]["boot_guard_action"].startswith("panic")


def test_panic_hotkey_is_the_documented_one():
    assert PANIC_HOTKEY == "Ctrl+Alt+P"


def test_an_armed_change_is_reverted_after_its_window(engine, clock, service):
    """Regression: expire_armed() existed but nobody called it, so a change
    'armed to auto-revert in 60 s' would sit there forever."""
    out = service.apply_fixes(["display.set_refresh"])
    assert service.pending_confirmations() == []
    service.register_armed(out["tx_id"], window_ms=60_000)

    assert len(service.pending_confirmations()) == 1
    clock.tick(30_000)
    assert service.tick()["pending"] == 1          # not yet due

    clock.tick(31_000)
    result = service.tick()
    assert result["reverted"] == [out["tx_id"]]
    assert service.backend.read("display:primary.mode")["hz"] == 59.94


def test_confirming_cancels_the_auto_revert(service):
    service.tick()                                  # establish a heartbeat first
    out = service.apply_fixes(["display.set_refresh"])
    service.register_armed(out["tx_id"], window_ms=60_000)
    service.confirm(out["tx_id"])
    assert service.pending_confirmations() == []
    assert service.tick()["reverted"] == []
    assert service.backend.read("display:primary.mode")["hz"] == 165


def test_a_window_missed_by_an_offline_app_is_called_out(service):
    """If the process was not running, elapsed time is not evidence of consent."""
    out = service.apply_fixes(["display.set_refresh"])
    service.register_armed(out["tx_id"], window_ms=1)
    # simulate a long gap with no heartbeats recorded in the window
    result = service.tick(now=service.clock() + 600_000)
    assert out["tx_id"] in result["missed_windows"]
    assert service.backend.read("display:primary.mode")["hz"] == 59.94


# ------------------------------------------------- arming is the whole safety net
SMB1 = "registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters"


def _smb1_on(service):
    """The registry stores this as 1/0, not true/false."""
    return bool(service.backend.read(SMB1))

def test_a_risky_change_is_armed_by_default(service):
    """The regression this suite exists for: `require_confirm` was never resolved
    from the plan, so every change committed outright and nothing was ever armed.
    A high-risk tweak must land in a state the app can undo on its own."""
    out = service.apply_fixes(["security.smb1.disable"])
    assert out["applied"] is True
    state = service.conn.execute("SELECT state FROM transactions WHERE id=?",
                                 (out["tx_id"],)).fetchone()[0]
    assert state == "armed"
    pending = service.pending_confirmations()
    assert [p["tx_id"] for p in pending] == [out["tx_id"]]
    assert 0 < pending[0]["seconds_left"] <= 60


def test_a_low_risk_change_commits_without_a_window(service):
    out = service.apply_fixes(["display.set_refresh"])
    state = service.conn.execute("SELECT state FROM transactions WHERE id=?",
                                 (out["tx_id"],)).fetchone()[0]
    assert state == "committed"
    assert service.pending_confirmations() == []


def test_an_expired_window_reverts_on_its_own(service):
    out = service.apply_fixes(["security.smb1.disable"])
    assert _smb1_on(service) is False
    armed = service.conn.execute(
        "SELECT armed_at,window_ms FROM armed_changes").fetchone()

    # the app was plainly running across the window
    for offset in (10_000, 25_000, 40_000):
        service.tick(now=armed[0] + offset)

    result = service.tick(now=armed[0] + armed[1] + 5_000)
    assert result["reverted"] == [out["tx_id"]]
    assert result["missed_windows"] == []
    assert _smb1_on(service) is True
    assert service.pending_confirmations() == []


def test_a_window_that_expired_while_offline_is_named_as_missed(service):
    """Silence across a window the app slept through is not approval."""
    out = service.apply_fixes(["security.smb1.disable"])
    armed = service.conn.execute(
        "SELECT armed_at,window_ms FROM armed_changes").fetchone()
    result = service.tick(now=armed[0] + armed[1] + 5_000)
    assert result["missed_windows"] == [out["tx_id"]]
    assert _smb1_on(service) is True
    feeds = [e["narrative"] for e in service.sentinel.feed(10)]
    latest = next(f for f in feeds if "Auto-reverted" in f)
    assert "not running" in latest
    assert not latest.endswith(" ")


def test_confirming_keeps_the_change(service):
    out = service.apply_fixes(["security.smb1.disable"])
    service.confirm(out["tx_id"])
    armed = service.conn.execute("SELECT armed_at,window_ms FROM armed_changes").fetchone()
    result = service.tick(now=armed[0] + armed[1] + 5_000)
    assert result["reverted"] == []
    assert _smb1_on(service) is False
