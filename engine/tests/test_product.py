"""Gaming, Autopsy, Studio, Consent — the four modules that make it a product.

Each has one non-negotiable honesty rule, and that rule is what these tests are
written to protect:

  gaming   — name the limiter, or admit there isn't one; average fps is never the answer
  autopsy  — never erase something critical, and say so in the receipt
  studio   — refuse a theme that breaks the 3% idle-GPU budget
  consent  — "fix everything" can only apply things the catalog can actually fix
"""
from __future__ import annotations

import random

import pytest

from pco_engine.autopsy import AutopsyLab, is_critical
from pco_engine.consent import Consent, DEFAULT
from pco_engine.gaming import (GAMES, Bottleneck, Readings, analyse_frames, classify,
                               grade, suggest_settings, simulated_capture)
from pco_engine.studio import (COSTS, IDLE_GPU_BUDGET_PCT, Studio, cost_of, governor,
                               validate)


# =============================================================== frame analysis

def test_average_fps_is_not_the_whole_story():
    """Two sessions with the *same* average, wildly different feel.

    Built deliberately: 588 frames at 7.483 ms + 12 frames at 50 ms has a mean of
    8.333 ms, i.e. exactly the same 120 fps average as a perfectly flat series.
    """
    flat = [1000 / 120] * 600
    spiky = [7.4830] * 588 + [50.0] * 12
    a, b = analyse_frames(flat), analyse_frames(spiky)
    assert abs(a.fps_avg - b.fps_avg) < 1.0         # indistinguishable averages…
    assert b.fps_p1 == pytest.approx(20.0, abs=0.5)  # …6× worse 1% low
    assert b.fps_p1 < a.fps_p1 * 0.2
    assert b.stutter_pct > a.stutter_pct


def test_flat_frames_are_perfectly_consistent():
    stats = analyse_frames([1000 / 144] * 500)
    assert stats.consistency > 99
    assert stats.stutter_pct == 0


def test_too_few_frames_is_refused_not_guessed():
    with pytest.raises(ValueError):
        analyse_frames([5.0] * 4)


def test_grade_reflects_frame_pacing():
    assert grade(analyse_frames([1000 / 200] * 500)) == "S"
    stuttery = analyse_frames([1000 / 200] * 480 + [1000 / 15] * 20)
    assert grade(stuttery) in {"C", "D"}


# ================================================================== bottleneck

def _r(**kw):
    base = dict(gpu_util=50, cpu_util=40, cpu_busiest_thread=40, vram_used_mb=3000,
                vram_total_mb=8192, ram_used_mb=8000, ram_total_mb=16384,
                gpu_temp_c=65, cpu_temp_c=60, disk_queue=2)
    base.update(kw)
    return Readings(**base)


def test_gpu_limit_is_named_when_gpu_is_saturated():
    b = classify(_r(gpu_util=99, cpu_busiest_thread=60))
    assert b.key == "gpu"
    assert b.confidence >= 0.85


def test_cpu_limit_is_named_by_the_busiest_thread_not_the_average():
    """A 32-core CPU at 20% overall can still be the limiter if one thread is at 100%."""
    b = classify(_r(gpu_util=55, cpu_util=22, cpu_busiest_thread=97))
    assert b.key == "cpu"


def test_thermal_masquerading_as_gpu_is_caught_first():
    b = classify(_r(gpu_util=99, gpu_temp_c=88))
    assert b.key == "thermal"


def test_cpu_thermal_is_caught():
    assert classify(_r(cpu_temp_c=97)).key == "thermal"


def test_vram_exhaustion_beats_the_naive_gpu_reading():
    b = classify(_r(gpu_util=99, vram_used_mb=8100, vram_total_mb=8192))
    assert b.key == "vram"
    assert b.advice and "texture" in b.advice[0].lower()


def test_ram_pressure_is_detected():
    assert classify(_r(ram_used_mb=15600, ram_total_mb=16384)).key == "ram"


def test_storage_queue_is_detected():
    assert classify(_r(disk_queue=55)).key == "storage"


def test_healthy_machine_gets_no_invented_problem():
    b = classify(_r(gpu_util=70, cpu_busiest_thread=60))
    assert b.key == "balanced"
    assert "Nothing to fix" in b.advice[0]


def test_every_bottleneck_has_plain_english_advice():
    cases = [dict(gpu_util=99, cpu_busiest_thread=50), dict(cpu_busiest_thread=99),
             dict(gpu_temp_c=90), dict(vram_used_mb=8192, vram_total_mb=8192),
             dict(ram_used_mb=16384, ram_total_mb=16384), dict(disk_queue=90)]
    for case in cases:
        b = classify(_r(**case))
        assert b.headline and b.advice
        assert all(isinstance(a, str) and len(a) > 10 for a in b.advice)


# =================================================================== settings

def test_settings_target_the_actual_bottleneck():
    cpu = suggest_settings("cs2", classify(_r(cpu_busiest_thread=99, gpu_util=50)))
    gpu = suggest_settings("cs2", classify(_r(gpu_util=99, cpu_busiest_thread=50)))
    assert any("view distance" in s.label.lower() for s in cpu)
    assert any("upscal" in s.label.lower() for s in gpu)
    assert not any("upscal" in s.label.lower() for s in cpu)


def test_settings_are_ordered_by_gain():
    adv = suggest_settings("apex", classify(_r(gpu_util=99)))
    gains = [s.fps_gain_pct for s in adv]
    assert gains == sorted(gains, reverse=True)


def test_every_bottleneck_yields_at_least_one_suggestion():
    for key, readings in {
        "gpu": dict(gpu_util=99), "cpu": dict(cpu_busiest_thread=99),
        "thermal": dict(gpu_temp_c=90), "vram": dict(vram_used_mb=8192, vram_total_mb=8192),
        "balanced": {}, "storage": dict(disk_queue=90), "ram": dict(ram_used_mb=16000),
    }.items():
        b = classify(_r(**readings))
        assert suggest_settings("fortnite", b), key


def test_low_latency_option_only_where_it_exists():
    cs2 = suggest_settings("cs2", classify(_r()))
    minecraft = suggest_settings("minecraft", classify(_r()))
    assert any(s.key == "nvidia_reflex" for s in cs2)
    assert not any(s.key == "nvidia_reflex" for s in minecraft)


# ============================================================== game sessions

def test_simulation_is_deterministic_per_game():
    a = simulated_capture("cs2", 10)
    b = simulated_capture("cs2", 10)
    assert a[0] == b[0]


def test_each_game_is_classified_consistently_with_its_own_sample():
    for gid in ("cs2", "tarkov", "minecraft", "fortnite"):
        frames, readings = simulated_capture(gid, 10)
        assert classify(readings).key  # never raises, always names something


def test_session_is_persisted_with_a_receipt(engine):
    from pco_engine.gaming import Gaming
    g = Gaming(engine.conn, engine.clock)
    out = g.capture("cs2", seconds=12)
    assert out["session_id"]
    assert g.sessions()[0]["id"] == out["session_id"]
    assert g.sessions(game_id="cs2")
    assert not g.sessions(game_id="valorant")


def test_unknown_game_is_refused(engine):
    from pco_engine.gaming import Gaming
    with pytest.raises(KeyError):
        Gaming(engine.conn, engine.clock).capture("halflife3", seconds=10)


def test_plan_explains_the_biggest_single_win(engine):
    from pco_engine.gaming import Gaming
    g = Gaming(engine.conn, engine.clock)
    plan = g.plan("fortnite")
    assert "expected" in plan and "%" in plan["expected"]
    assert plan["settings"]


def test_profile_round_trip_and_export(engine):
    from pco_engine.gaming import Gaming
    g = Gaming(engine.conn, engine.clock)
    g.save_profile("cs2", {"upscaling": "Quality"})
    assert g.profile("cs2")["settings"]["upscaling"] == "Quality"
    doc = g.export_profile("cs2")
    assert doc["format"] == "pco.gameprofile.v1"
    assert doc["settings"]["upscaling"] == "Quality"


# ==================================================================== autopsy

def test_critical_paths_are_recognised():
    assert is_critical("C:\\Windows\\System32\\vcruntime140.dll")
    assert is_critical("HKLM\\SOFTWARE\\Microsoft Visual C++\\Runtime")
    assert not is_critical("C:\\Program Files\\Nonsense\\nonsense.dll")


def test_autopsy_records_and_erases_non_critical(engine):
    a = AutopsyLab(engine.conn, engine.clock)
    aid = a.begin("nonsense-setup.exe")
    a.note(aid, [
        {"class": "file", "locator": "C:\\Program Files\\Nonsense\\app.exe", "size": 9000},
        {"class": "service", "locator": "NonsenseSvc"},
        {"class": "registry", "locator": "HKLM\\SOFTWARE\\Nonsense"},
        {"class": "file", "locator": "C:\\Windows\\System32\\vcruntime140.dll", "size": 400},
    ])
    au = a.finish(aid)
    assert au.as_dict()["counts"] == {"file": 2, "registry": 1, "service": 1}
    assert len(au.as_dict()["critical_items"]) == 1

    res = a.erase(aid)
    assert res["erased"] == 3
    assert res["kept"] == 1                       # the runtime file survived
    assert "1 kept" in res["summary"]


def test_erasing_writes_a_receipt_and_takes_a_snapshot(engine):
    taken = []
    a = AutopsyLab(engine.conn, engine.clock, snapshot=lambda: taken.append(1) or "snap1")
    aid = a.begin("x.exe")
    a.note(aid, [{"class": "file", "locator": "C:\\Temp\\x.dll"}])
    res = a.erase(aid)
    assert res["snapshot_id"] == "snap1" and taken == [1]
    kinds = [r[0] for r in engine.conn.execute("SELECT kind FROM receipts").fetchall()]
    assert "autopsy" in kinds


def test_partial_failure_is_reported_not_hidden(engine):
    def remover(item):
        if "dll" in item.locator:
            raise OSError("file in use")

    a = AutopsyLab(engine.conn, engine.clock, remover=remover)
    aid = a.begin("y.exe")
    a.note(aid, [{"class": "file", "locator": "C:\\Temp\\locked.dll"},
                 {"class": "file", "locator": "C:\\Temp\\free.txt"}])
    res = a.erase(aid)
    assert res["erased"] == 1 and len(res["failed"]) == 1
    assert "in use" in res["failed"][0]["error"]


def test_keep_marks_the_install_as_intentional(engine):
    a = AutopsyLab(engine.conn, engine.clock)
    aid = a.begin("keepme.exe")
    a.keep(aid)
    assert a.get(aid).state == "kept"


def test_class_selection_limits_the_blast_radius(engine):
    a = AutopsyLab(engine.conn, engine.clock)
    aid = a.begin("partial.exe")
    a.note(aid, [{"class": "file", "locator": "C:\\A\\a.dat"},
                 {"class": "registry", "locator": "HKLM\\A"}])
    res = a.erase(aid, include_classes={"file"})
    assert res["erased"] == 1
    rows = engine.conn.execute(
        "SELECT class,action FROM autopsy_items ORDER BY class").fetchall()
    assert dict(rows)["registry"] == "pending"


# ===================================================================== studio

def test_a_light_theme_fits_the_budget():
    doc = {"name": "Quiet", "features": {"static_wallpaper": True, "taskbar_blur": True}}
    assert governor(cost_of(doc))["ok"] is True


def test_a_heavy_theme_reports_its_cost_without_refusing():
    """This used to assert a refusal. The rule changed: nothing is forced on
    anyone, so nothing is blocked either. What must survive is that the number
    is visible before the choice — a cost you cannot see is the real problem."""
    from pco_engine.cost import verify
    doc = {"name": "Overkill", "features": {"animated_wallpaper": True,
                                            "spectrum": True, "widgets_heavy": True}}
    v = verify(doc).as_dict()
    assert v["blocked"] is False, "a visual choice must never be blocked"
    assert v["cost_pct"] > IDLE_GPU_BUDGET_PCT
    assert v["over_advisory_budget"] is True
    assert v["tier"] == "full"
    assert v["recommendations"], "the user must be told what it costs"
    assert v["alternatives"], "the cheaper option must be named"


def test_the_zero_cost_option_is_always_reported_available():
    from pco_engine.cost import verify
    v = verify({"features": {"animated_wallpaper": True}}).as_dict()
    assert v["tier"] in {"light", "balanced", "full"}
    assert "zero-cost option is always available" in v["note"]


def test_a_zero_cost_document_is_the_zero_tier_and_costs_nothing():
    from pco_engine.cost import tier_of
    doc = {"name": "Still", "features": {"static_wallpaper": True,
                                         "icon_pack": True}}
    assert cost_of(doc) == 0.0
    assert tier_of(0.0) == "zero"


def test_the_zero_cost_variant_switches_every_paid_feature_off():
    from pco_engine.cost import cost_of as c, zero_cost_variant
    doc = {"features": {"animated_wallpaper": True, "spectrum": True,
                        "icon_pack": True, "taskbar_accent": True}}
    cheap = zero_cost_variant(doc)
    assert c(cheap) == 0.0
    assert cheap["features"]["icon_pack"] is True, "free features must survive"
    assert doc["features"]["animated_wallpaper"] is True, "input must not mutate"


def test_apply_always_applies_and_reports_the_cost(engine):
    """The old contract: a heavy theme was refused unless forced. The new one:
    it applies, and tells you what it costs. `force` is still accepted so no
    caller breaks, but there is nothing left to force past."""
    from pco_engine.studio import Studio
    s = Studio(engine.conn, engine.clock)
    heavy = s.themes()[-1]
    out = s.apply(heavy["id"])
    assert out["applied"] is True
    assert out["cost"]["blocked"] is False
    assert out["cost_pct"] == heavy["cost_pct"]
    # force is still accepted and still applies
    assert s.apply(heavy["id"], force=True)["applied"] is True


def test_apply_is_exclusive(engine):
    from pco_engine.studio import Studio
    s = Studio(engine.conn, engine.clock)
    light = s.themes()[0]
    s.apply(light["id"])
    s.apply(s.themes()[1]["id"])
    active = [t for t in s.themes() if t["applied_at"]]
    assert len(active) == 1


def test_unknown_feature_is_rejected_with_its_name():
    problems = validate({"name": "x", "features": {"hologram": True}})
    assert any("hologram" in p for p in problems)


def test_import_validates_without_applying(engine):
    from pco_engine.studio import Studio
    s = Studio(engine.conn, engine.clock)
    out = s.import_doc({"name": "theirs", "features": {"widgets_light": True}})
    assert out["valid"] and out["cost_pct"] > 0
    assert s.active() is None                    # import must not apply


def test_budget_reports_the_active_cost_and_tier(engine):
    """`remaining_pct` was headroom against a cap that no longer exists. What a
    user needs is what the active look costs and which tier that is."""
    from pco_engine.studio import Studio
    s = Studio(engine.conn, engine.clock)
    s.apply(s.themes()[0]["id"])
    b = s.budget()
    assert b["active"]
    assert b["active_cost_pct"] >= 0
    assert b["tier"] in {"zero", "light", "balanced", "full"}
    assert b["tier_meaning"]
    assert b["zero_cost_available"] is True
    assert "Nothing is blocked" in b["note"]


# ==================================================================== consent

def test_default_never_touches_privacy_or_storage(engine):
    c = Consent(engine.conn, engine.clock)
    assert c.allows("performance", auto_safe=True, has_fix=True)
    assert not c.allows("privacy", auto_safe=True, has_fix=True)
    assert not c.allows("storage", auto_safe=True, has_fix=True)


def test_safe_level_never_applies_review_items(engine):
    c = Consent(engine.conn, engine.clock)
    assert not c.allows("security", auto_safe=False, has_fix=True)


def test_trusted_level_applies_review_items_that_have_a_fix(engine):
    c = Consent(engine.conn, engine.clock)
    c.trust_everything()
    assert c.allows("security", auto_safe=False, has_fix=True)
    assert c.allows("privacy", auto_safe=False, has_fix=True)


def test_trust_never_applies_something_with_no_fix(engine):
    """'Fix everything' cannot fix an observation. It must not pretend to."""
    c = Consent(engine.conn, engine.clock)
    c.trust_everything()
    assert not c.allows("reliability", auto_safe=False, has_fix=False)


def test_off_category_is_off_even_when_trusted(engine):
    c = Consent(engine.conn, engine.clock)
    c.trust_everything()
    c.set(categories={"storage": "off"})
    assert not c.allows("storage", auto_safe=True, has_fix=True)


def test_consent_survives_a_restart(engine, clock):
    Consent(engine.conn, clock).trust_everything()
    again = Consent(engine.conn, clock)
    assert again.decision().level == "trusted"


def test_explain_says_what_would_and_would_not_happen(engine):
    c = Consent(engine.conn, engine.clock)
    findings = [
        {"check_id": "a", "category": "performance", "auto_safe": True, "fix_tweaks": ["t1"]},
        {"check_id": "b", "category": "privacy", "auto_safe": False, "fix_tweaks": ["t2"]},
        {"check_id": "c", "category": "performance", "auto_safe": True, "fix_tweaks": []},
    ]
    out = c.explain(findings)
    assert out["would_apply"] == ["a"]
    assert {w["check_id"] for w in out["withheld"]} == {"b", "c"}
    assert any("no declared fix" in w["why"] for w in out["withheld"])


def test_reset_returns_to_the_documented_default(engine):
    c = Consent(engine.conn, engine.clock)
    c.trust_everything()
    d = c.reset()
    assert d.level == DEFAULT.level and d.categories == DEFAULT.categories


def test_shipped_catalog_has_no_tweak_that_changes_security_posture_secretly():
    """A guard against future catalog edits: disabling Defender or firewalling
    must never be marked auto_safe, because that is 'optimising' into a breach."""
    from pco_engine.checker import CHECKS
    for check in CHECKS:
        if "defender" in check.id.lower():
            assert check.auto_safe is False


# =============================================== privacy fix + idempotent apply

def test_privacy_finding_now_has_a_real_fix():
    """Before this, 'telemetry at full level' was an observation with no tweak, so
    even the trusted tier could do nothing. An unfixable finding is a dead end."""
    from pco_engine.checker import CHECKS
    telemetry = next(c for c in CHECKS if "telemetry" in c.id)
    assert telemetry.fix_tweaks == ("privacy.telemetry.minimum",)
    assert telemetry.auto_safe is False        # still needs trust


def test_telemetry_finding_reads_the_real_windows_location():
    from pco_engine import FakeBackend
    from pco_engine.checker import TELEMETRY_ADDRESS, Probe, _telemetry
    p = Probe(FakeBackend({TELEMETRY_ADDRESS: "full"}))
    assert _telemetry(p) is not None
    p2 = Probe(FakeBackend({TELEMETRY_ADDRESS: "required"}))
    assert _telemetry(p2) is None


def test_telemetry_tweak_is_plannable_and_flips_the_value(engine):
    assert engine.preflight.simulate(["privacy.telemetry.minimum"]).ok
    engine.apply("privacy.telemetry.minimum")
    assert engine.backend.read(
        "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection") == "required"


def test_applying_something_already_applied_is_success_not_an_error(tmp_path, clock):
    """Regression: a second 'fix everything' click reported applied=False with an
    error string, which reads as a failure in the UI for a machine that is fine."""
    import os
    from pco_engine import FakeBackend
    from pco_engine.service import DEMO_STATE, Service
    svc = Service(str(tmp_path / "idem.db"), backend=FakeBackend(DEMO_STATE),
                  catalog_dir=os.path.join(os.path.dirname(__file__), "..", "catalog"),
                  vault_root=str(tmp_path / "vault"), clock=clock)
    svc.checkup()
    first = svc.run_action("checkup.fix")
    assert first["applied"] is True
    second = svc.run_action("checkup.fix")
    assert second["applied"] is True
    assert second.get("already") is True
    assert "error" not in second


def test_applying_writes_a_receipt_and_undo_marks_it(tmp_path, clock):
    """Regression: engine.apply() wrote no receipt, so History was empty after the
    single most important action in the product."""
    import os
    from pco_engine import FakeBackend
    from pco_engine.service import DEMO_STATE, Service
    svc = Service(str(tmp_path / "rc.db"), backend=FakeBackend(DEMO_STATE),
                  catalog_dir=os.path.join(os.path.dirname(__file__), "..", "catalog"),
                  vault_root=str(tmp_path / "vault"), clock=clock)
    out = svc.apply_fixes(["display.set_refresh"])
    assert out["applied"] is True

    receipts = svc.receipts()
    assert receipts, "applying must leave a receipt"
    r = receipts[0]
    assert r["kind"] == "tweak" and r["tx_id"] == out["tx_id"]
    assert r["undone"] is False

    svc.undo(out["tx_id"])
    assert svc.receipts()[0]["undone"] is True


def test_receipt_keeps_the_before_and_after_values(tmp_path, clock):
    import json as _json
    import os
    from pco_engine import FakeBackend
    from pco_engine.service import DEMO_STATE, Service
    svc = Service(str(tmp_path / "rc2.db"), backend=FakeBackend(DEMO_STATE),
                  catalog_dir=os.path.join(os.path.dirname(__file__), "..", "catalog"),
                  vault_root=str(tmp_path / "vault"), clock=clock)
    svc.apply_fixes(["display.set_refresh"])
    row = svc.conn.execute(
        "SELECT detail_json FROM receipts WHERE kind='tweak'").fetchone()
    detail = _json.loads(row[0])
    eff = detail["effects"][0]
    assert eff["old"]["hz"] == 59.94 and eff["new"]["hz"] == 165
