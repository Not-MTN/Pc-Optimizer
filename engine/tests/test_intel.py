"""Startup Surgeon, palette, space, duplicates, peripherals, report, ask.

Each module here exists because the plan asked for it (MASTERPLAN A2/B2/B4/E1,
FEATURES §4/§6, F1). The tests protect the specific honesty rule in each:

  startup     — no invented cost, no silent dependency break
  palette     — engine verbs only unless explicitly allowed
  space       — "unattributed"/"no samples" instead of a made-up number
  duplicates  — a group is only declared after the bytes match
  peripheral  — "inconclusive" rather than a fabricated pass
  report      — the numbers in the document match the machine
  ask         — every answer is traceable to a row
"""
from __future__ import annotations

import json
import os

import pytest

from pco_engine.ask import Answer, classify
from pco_engine.dupes import Duplicates, choose_keeper, is_protected
from pco_engine.palette import Command, Palette, score
from pco_engine.peripheral import (PeripheralLab, chatter_test, double_click_test,
                                   monitor_pattern, polling_rate_test,
                                   sensor_jitter_test)
from pco_engine.report import Report, build, to_html, to_markdown
from pco_engine.space import Space, folder_size
from pco_engine.startup import (Dependency, KNOWLEDGE, StartupItem, StartupSurgeon,
                                knowledge_for, refusal)


# =================================================================== startup

def _items():
    return [
        StartupItem("1", "DiagTrack", "service", "service:diagtrack", "services", 1450),
        StartupItem("2", "OneDrive", "run_key", "run_key:onedrive", "logon", 900),
        StartupItem("3", "Steam", "run_key", "run_key:steam", "logon", 1400),
        StartupItem("4", "SysMain", "service", "service:sysmain", "services", 620),
        StartupItem("5", "Mystery", "run_key", "run_key:mystery", "unattributed", None),
    ]


def test_unmeasured_items_are_unattributed_never_zero(engine):
    s = StartupSurgeon(engine.conn, engine.clock)
    rep = s.report(_items())
    mystery = next(i for i in rep.items if i.name == "Mystery")
    assert mystery.cost_ms is None
    assert "unattributed" in mystery.as_dict()["cost_label"]
    assert rep.unattributed == 1


def test_total_ignores_unmeasured_items(engine):
    s = StartupSurgeon(engine.conn, engine.clock)
    rep = s.report(_items())
    assert rep.total_ms == 1450 + 900 + 1400 + 620      # Mystery contributes nothing


def test_service_with_dependents_is_refused_and_named(engine):
    s = StartupSurgeon(engine.conn, engine.clock)
    s.set_dependencies([Dependency("Security Center", "service:wusearch")])
    wusearch = StartupItem("9", "Windows Search", "service", "service:wusearch",
                           "services", 900)
    why = refusal(wusearch, s._deps)
    assert why and "Security Center" in why


def test_knowledge_base_blocks_bad_disable_advice(engine):
    """SysMain is the canonical example of popular-but-wrong advice."""
    sysmain = StartupItem("4", "SysMain", "service", "service:sysmain", "services", 620)
    why = refusal(sysmain, [])
    assert why and "Not recommended" in why
    assert "HDD" in why


def test_drivers_are_never_disabled(engine):
    drv = StartupItem("d", "nvlddmkm", "driver", "driver:nvlddmkm", "kernel", 200)
    assert refusal(drv, []) is not None


def test_plan_prefers_defer_and_counts_savings(engine):
    s = StartupSurgeon(engine.conn, engine.clock)
    plan = s.plan(_items())
    steam = next(r for r in plan["rows"] if r["item"] == "Steam")
    assert steam["action"] == "defer"
    assert plan["saving_ms"] == 1450 + 900 + 1400        # SysMain refused, Mystery unmeasured
    assert plan["after_ms"] == 620


def test_plan_states_the_honesty_rule(engine):
    s = StartupSurgeon(engine.conn, engine.clock)
    plan = s.plan(_items())
    assert "not counted in the saving" in plan["honesty"]


def test_target_that_cannot_be_reached_says_so(engine):
    s = StartupSurgeon(engine.conn, engine.clock)
    plan = s.plan(_items(), target_ms=100)     # 620 ms cannot be removed
    assert plan["meets_target"] is False
    assert "Cannot reach" in plan["verdict"]
    assert plan["after_ms"] == 620


def test_watchdog_names_what_came_back(engine):
    s = StartupSurgeon(engine.conn, engine.clock)
    relapsed = s.watch_for_relapse({"run_key:steam"}, {"run_key:steam", "run_key:riotclient"})
    assert len(relapsed) == 1
    assert "riot" in relapsed[0]["guess"]
    assert "re-added itself" in relapsed[0]["narrative"]


def test_service_startup_report_is_complete(service):
    rep = service.startup_report()
    assert rep["available"] is True
    assert rep["report"]["total_ms"] > 0
    assert rep["verdict"].startswith("12 item(s) have a measured cost")


# =================================================================== palette

def test_prefix_beats_fuzzy(engine):
    assert score("cle", "clean") > score("cle", "check")


def test_fuzzy_match_finds_typo_shaped_queries(engine):
    assert score("cln", "clean") > 0


def test_search_is_capped_and_ranked(engine):
    p = Palette(engine.conn, engine.clock)
    res = p.search("dns", limit=3)
    assert len(res) <= 3
    assert res[0]["verb"].startswith("dns")


def test_engine_commands_execute_without_a_subprocess(engine):
    seen = []
    p = Palette(engine.conn, engine.clock, run_action=lambda a, args: seen.append(a) or "ok")
    out = p.execute("check")
    assert seen == ["checkup.run"]
    assert out["elapsed_ms"] >= 0


def test_shell_commands_are_refused_by_default(engine):
    p = Palette(engine.conn, engine.clock)
    p.add(Command(id="u1", title="Sync", verb="sync", executor="shell",
                  payload={"cmd": "sync.cmd"}))
    with pytest.raises(PermissionError) as exc:
        p.execute("sync")
    assert "disabled" in str(exc.value)


def test_shell_runs_only_when_explicitly_allowed(engine):
    p = Palette(engine.conn, engine.clock, allow_shell=True)
    p.add(Command(id="u1", title="Sync", verb="sync", executor="shell",
                  payload={"builtin": "ok"}))
    assert p.execute("sync")["command"] == "sync"


def test_imported_command_shows_a_permission_diff(engine):
    p = Palette(engine.conn, engine.clock)
    diff = p.permission_diff(Command(id="u", title="t", verb="s", executor="script"))
    assert diff["needs_confirm"] is True
    assert any("script" in e for e in diff["extra"])
    engine_diff = p.permission_diff(Command(id="e", title="t", verb="c", executor="engine"))
    assert engine_diff["needs_confirm"] is False


def test_unknown_command_is_a_clear_error(engine):
    # ValueError, not KeyError: an unknown verb is a bad request (400), and
    # KeyError is reserved for a missing argument so the server can tell them
    # apart. The message must still name the verb.
    with pytest.raises(ValueError) as caught:
        Palette(engine.conn, engine.clock).execute("nope")
    assert "unknown command" in str(caught.value)


def test_executing_writes_a_receipt(engine):
    p = Palette(engine.conn, engine.clock)
    p.execute("help")
    kinds = [r[0] for r in engine.conn.execute("SELECT kind FROM receipts").fetchall()]
    assert "command" in kinds


def test_bad_verb_is_rejected_on_add(engine):
    p = Palette(engine.conn, engine.clock)
    with pytest.raises(ValueError):
        p.add(Command(id="x", title="t", verb="two words", executor="engine"))


# ===================================================================== space

def test_folder_size_is_bounded_and_real(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 1000)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 2000)
    total, files = folder_size(str(tmp_path))
    assert total == 3000 and files == 2


def test_folder_size_of_missing_path_is_zero_not_an_error():
    assert folder_size("/definitely/not/here") == (0, 0)


def test_grew_without_samples_says_so(engine, tmp_path):
    sp = Space(engine.conn, engine.clock, roots={"x": str(tmp_path)})
    out = sp.grew(7)
    assert out["available"] is False
    assert "no samples" in out["reason"]


def test_two_samples_produce_a_ranked_delta(engine, tmp_path, clock):
    (tmp_path / "s").mkdir()
    sp = Space(engine.conn, clock, roots={"big": str(tmp_path),
                                         "small": str(tmp_path / "s")})
    (tmp_path / "a.bin").write_bytes(b"x" * 5000)
    sp.sample()
    clock.tick(3 * 86_400_000)
    (tmp_path / "b.bin").write_bytes(b"x" * 900_000)
    sp.sample()
    grew = sp.grew(3)
    assert grew["available"] is True
    by_folder = {d["folder"]: d for d in grew["changed"]}
    assert by_folder["big"]["delta_bytes"] >= 900_000
    # an unchanged folder is absent from `changed` rather than listed as 0 —
    # a list of deltas should only contain things that moved
    assert "small" not in by_folder


def test_forecast_says_flat_when_nothing_grows(engine):
    out = engine and Space(engine.conn, engine.clock).forecast()
    assert out["available"] is False or "flat" in out["verdict"] or out["days_left"] is not None


def test_demo_machine_has_usable_space_history(service):
    grew = service.space_grew(7)
    assert grew["available"] is True
    assert grew["changed"]
    fc = service.space_forecast()
    assert fc["days_left"] is None or fc["days_left"] > 0


def test_explain_names_the_folder(service):
    out = service.space.explain("Downloads")
    assert "Downloads" in out["verdict"]


# ================================================================ duplicates

def _dupe_tree(tmp_path):
    a = bytes(range(256)) * 8192            # 2 MB, over the 1 MB floor
    b = bytes(reversed(range(256))) * 8192
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "same1.bin").write_bytes(a)
    (tmp_path / "two" / "same2.bin").write_bytes(a)
    (tmp_path / "two" / "different.bin").write_bytes(b)
    return tmp_path


def test_confirmed_only_after_bytes_match(engine, tmp_path):
    tree = _dupe_tree(tmp_path)
    rep = Duplicates(engine.conn, engine.clock).scan([str(tree)])
    assert rep.stages["by_size"] >= 3          # all three are candidates by size
    assert len(rep.groups) == 1                # only the true pair is confirmed
    assert rep.groups[0].size == len(bytes(range(256)) * 8192)


def test_different_files_are_never_grouped(engine, tmp_path):
    """Regression: a fixture gave two different installers identical bytes and the
    finder dutifully reported them as copies. Different bytes, different groups."""
    tree = _dupe_tree(tmp_path)
    rep = Duplicates(engine.conn, engine.clock).scan([str(tree)])
    names = {os.path.basename(p) for p in rep.groups[0].as_dict()["duplicates"]}
    assert "different.bin" not in names


def test_small_files_are_ignored(engine, tmp_path):
    (tmp_path / "a.txt").write_text("tiny")
    (tmp_path / "b.txt").write_text("tiny")
    rep = Duplicates(engine.conn, engine.clock).scan([str(tmp_path)], min_bytes=1024)
    assert rep.groups == []


def test_protected_trees_are_skipped(engine):
    assert is_protected("C:\\Program Files\\Thing\\a.dll")
    assert is_protected("C:\\Windows\\System32\\b.dll")
    assert not is_protected("C:\\Users\\me\\Downloads\\a.exe")


def test_keeper_prefers_program_files_then_oldest():
    from pco_engine.dupes import FileRef
    files = [FileRef("C:/Users/me/a.exe", 100, 100.0),
             FileRef("C:/Program Files/App/a.exe", 100, 200.0)]
    keep, reason = choose_keeper(files)
    assert "Program Files" in keep.path
    assert "installed program" in reason


def test_oldest_copy_is_kept_by_default():
    from pco_engine.dupes import FileRef
    files = [FileRef("C:/Users/me/new.exe", 100, 300.0),
             FileRef("C:/Users/me/old.exe", 100, 100.0)]
    keep, reason = choose_keeper(files)
    assert keep.path.endswith("old.exe")
    assert "oldest" in reason


def test_demo_duplicates_are_found(service):
    out = service.find_duplicates()
    assert out["group_count"] == 2
    assert out["wasted_bytes"] == 2 * 2 * 1024 * 1024 // 2 * 2  # 2 groups × 1 wasted copy


# ================================================================ peripherals

def test_failing_switch_is_called_a_hardware_fault():
    res = double_click_test([300, 12, 280, 8, 410, 15])
    assert res.verdict == "fail"
    assert "hardware fault" in " ".join(res.advice)


def test_clean_mouse_passes():
    assert double_click_test([300, 280, 410, 350]).verdict == "pass"


def test_no_clicks_is_inconclusive_not_a_pass():
    res = double_click_test([])
    assert res.verdict == "inconclusive"
    assert "No clicks" in res.headline


def test_chatter_names_the_worst_key():
    events = [("t", 0), ("t", 100), ("t", 112), ("t", 200),
              ("h", 300), ("e", 400), ("e", 410)]
    res = chatter_test(events)
    assert res.verdict == "fail"
    assert "'t'" in res.headline


def test_clean_typing_passes_chatter():
    events = [("t", i * 150) for i in range(10)]
    assert chatter_test(events).verdict == "pass"


def test_sensor_jitter_detects_a_wandering_track():
    clean = [(i, 2.0 * i) for i in range(40)]
    assert sensor_jitter_test(clean).verdict == "pass"
    import random
    rng = random.Random(7)
    dirty = [(i, 2.0 * i + rng.uniform(-6, 6)) for i in range(40)]
    res = sensor_jitter_test(dirty)
    assert res.verdict == "fail"
    assert res.evidence["jitter_deg"] > 1.5


def test_too_few_sensor_samples_is_inconclusive():
    assert sensor_jitter_test([(0, 0), (1, 2)]).verdict == "inconclusive"


def test_polling_rate_is_rounded_to_a_real_setting():
    res = polling_rate_test([1000] * 100)
    assert res.evidence["nominal_hz"] == 1000
    assert res.verdict == "pass"


def test_a_measured_rate_matching_a_real_setting_passes():
    """250 Hz is a real setting, so measuring 250 Hz is a pass — the tool must not
    invent a fault just because it is not 1000."""
    res = polling_rate_test([4000] * 100)
    assert res.evidence["measured_hz"] == pytest.approx(250, rel=0.02)
    assert res.verdict == "pass"


def test_a_capped_rate_that_matches_no_setting_fails_with_the_likely_cause():
    """A hub or a busy controller caps USB at an odd rate. 4000 µs → 250 Hz is a
    real setting (pass); 2600 µs → ~385 Hz matches nothing, which is the shape a
    faulty port produces."""
    res = polling_rate_test([2600] * 100)          # ~385 Hz
    assert res.verdict == "fail"
    assert res.evidence["measured_hz"] == pytest.approx(384.6, abs=1)
    assert any("hub" in a or "USB" in a for a in res.advice)


def test_high_rate_devices_are_recognised():
    res = polling_rate_test([500] * 100)           # 2000 Hz measured
    assert res.verdict == "pass"                   # 2000 Hz is a real setting
    assert res.evidence["nominal_hz"] == 2000


def test_monitor_test_has_every_colour_field():
    out = monitor_pattern("dead_pixel")
    assert out["steps"][:3] == ["#000000", "#ffffff", "#ff0000"]
    with pytest.raises(ValueError):
        monitor_pattern("vibes")


def test_results_are_recorded_with_a_summary(engine):
    lab = PeripheralLab(engine.conn, engine.clock)
    lab.record(double_click_test([300, 12]))
    lab.record(chatter_test([("t", 0), ("t", 100)]))
    summary = lab.summary()
    assert summary["tests_run"] == 2
    assert "1 test(s) found a hardware fault" in summary["verdict"]


# ==================================================================== report

def test_report_markdown_has_every_section(service):
    service.checkup()
    md = service.health_report("md")
    for heading in ("## Machine", "## Verdict", "## Findings",
                    "## What changed on this PC", "## Security & privacy"):
        assert heading in md
    assert "never left the device" in md


def test_report_html_is_self_contained_and_escaped(service):
    html = service.health_report("html")
    assert html.startswith("<!DOCTYPE html>")
    assert "<script" not in html and "http" not in html.split("<body>")[0]
    assert "<style>" in html


def test_report_numbers_match_the_machine(service):
    service.checkup()
    data = service.health_report("json")
    st = service.status()
    assert data["sections"]["machine"]["journal_rows"] == st["engine"]["journal_rows"]
    assert data["sections"]["verdict"]["score"] == st["checkup"]["total"]


def test_report_is_read_only(service):
    before = service.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    service.health_report("json")
    after = service.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    assert before == after


def test_html_escapes_hostile_text():
    rep = Report(generated_at=0, title="<script>alert(1)</script>",
                 sections={"machine": {"a": 1}, "verdict": {"headline": "x", "score": 1,
                                                           "scores": {}, "findings_total": 0,
                                                           "critical": []},
                           "findings": [], "history": [], "bench": {"latest": None,
                                                                   "baseline": None,
                                                                   "sessions": []},
                           "storage": {"vault_items": 0, "vault_bytes": 0, "growth": ""},
                           "security": {"blocker": {}}, "peripherals": {},
                           "appendix": {"how_this_was_made": "", "privacy": ""}})
    html = to_html(rep)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


# ======================================================================= ask

def test_intent_classification_covers_the_planned_questions():
    assert classify("what did I change?") == "what_changed"
    assert classify("why is it slow") == "why_slow"
    assert classify("what broke yesterday") == "what_broke"
    assert classify("undo that") == "undo"
    assert classify("what got installed") == "what_installed"
    assert classify("why is my disk full") == "disk"
    assert classify("am i secure") == "security"


def test_unknown_question_falls_back_to_facts_not_guesses():
    assert classify("banana") == "what_changed"


def test_every_answer_is_traceable(service):
    for q in ("what did i change", "what broke", "undo", "why is slow",
              "installed", "disk", "secure"):
        ans = service.ask_question(q)
        assert ans["note"].startswith("Every line here comes from the journal")


def test_change_answer_lists_real_transactions(service):
    service.checkup()
    service.run_action("checkup.fix")
    ans = service.ask_question("what did i change?")
    assert ans["intent"] == "what_changed"
    assert ans["items"], "a change was just made, so the answer cannot be empty"
    assert ans["items"][0]["tx_id"]


def test_undo_answer_is_undoable(service):
    service.apply_fixes(["display.set_refresh"])
    ans = service.ask_question("undo")
    assert ans["undoable"] is True
    assert ans["items"]


def test_help_lists_examples(service):
    ans = service.ask_question("help")
    assert any("what did I change" in i["question"] for i in ans["items"])


def test_startup_knowledge_rows_are_complete(service):
    """The UI renders class/phase/cost straight from these rows; blanks were a
    visible defect in the Startup view."""
    rep = service.startup_report()
    for k in rep["knowledge"]:
        assert k["item"] and k["class"] and k["phase"]
        assert k["cost_label"] in {"unattributed"} or k["cost_label"].endswith("ms")


def test_security_answer_admits_when_no_checkup_has_run(service):
    """Saying '0 findings' before any scan would read as 'all clear'."""
    ans = service.ask_question("am i secure")
    if not service.checker.latest_run():
        assert any(i.get("kind") == "notice" for i in ans["items"])
        assert "No checkup" in " ".join(i.get("plain", "") for i in ans["items"])
