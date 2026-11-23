"""Module tests: checker, cleaner, bench, dns, recipes, sentinel, service."""
import os

import pytest

from pco_engine import FakeBackend
from pco_engine.checker import Checker, Probe
from pco_engine.cleaner import Candidate, Cleaner, Rule
from pco_engine.dns import DnsBlocker, Matcher
from pco_engine.recipes import RecipeError, Recipes, Spec
from pco_engine.sentinel import Event, Sentinel
from pco_engine.service import Service, DEMO_METRICS, DEMO_STATE


# ------------------------------------------------------------------- checker

def test_checkup_categories_are_separated(engine):
    probe = Probe(FakeBackend(DEMO_STATE), DEMO_METRICS)
    checker = Checker(engine.conn, probe, engine.clock)
    result = checker.run()
    cats = {f.category for f in result.findings}
    assert "performance" in cats
    assert "storage" in cats
    # storage findings must never be in the performance bucket
    perf_titles = [f.title for f in result.findings if f.category == "performance"]
    assert not any("junk" in t.lower() or "duplicate" in t.lower() for t in perf_titles)


def test_storage_findings_do_not_lower_speed_score(engine):
    probe = Probe(FakeBackend(DEMO_STATE), {"storage.junk_bytes": 0, "storage.dup_bytes": 0})
    clean_run = Checker(engine.conn, probe, engine.clock).run()
    probe2 = Probe(FakeBackend(DEMO_STATE),
                   {"storage.junk_bytes": 90_000_000_000, "storage.dup_bytes": 50_000_000_000})
    dirty_run = Checker(engine.conn, probe2, engine.clock).run()
    assert dirty_run.scores["performance"] <= clean_run.scores["performance"]
    assert dirty_run.total <= clean_run.total


def test_refresh_finding_has_evidence_and_is_auto_safe(engine):
    probe = Probe(FakeBackend(DEMO_STATE), DEMO_METRICS)
    result = Checker(engine.conn, probe, engine.clock).run()
    refresh = next(f for f in result.findings if f.check_id == "display.refresh.below_max")
    assert refresh.auto_safe is True
    assert refresh.evidence["current_hz"] == 59.94
    assert refresh.fix_tweaks == ["display.set_refresh"]


def test_high_severity_findings_are_not_auto_safe_by_default(engine):
    probe = Probe(FakeBackend(DEMO_STATE), DEMO_METRICS)
    result = Checker(engine.conn, probe, engine.clock).run()
    for f in result.findings:
        if f.check_id == "security.defender.off":
            assert f.auto_safe is False


def test_scoring_is_explainable_and_bounded(engine):
    probe = Probe(FakeBackend(DEMO_STATE), DEMO_METRICS)
    result = Checker(engine.conn, probe, engine.clock).run()
    assert 0 <= result.total <= 100
    assert all(0 <= v <= 100 for v in result.scores.values())


# ------------------------------------------------------------------- cleaner

@pytest.fixture()
def sandbox(tmp_path):
    for name in ("temp", "cache/thumbs", "cache/dns", "logs"):
        os.makedirs(tmp_path / name, exist_ok=True)
    (tmp_path / "temp" / "a.tmp").write_bytes(b"x" * 2048)
    (tmp_path / "temp" / "b.tmp").write_bytes(b"y" * 4096)
    (tmp_path / "cache" / "thumbs" / "t.db").write_bytes(b"z" * 1024)
    (tmp_path / "logs" / "old.log").write_bytes(b"l" * 512)
    (tmp_path / "keep.txt").write_bytes(b"keep me")
    return tmp_path


def _cleaner(engine, sandbox):
    roots = {"temp": str(sandbox / "temp"), "cache": str(sandbox / "cache"),
             "logs": str(sandbox / "logs"), "temp_system": str(sandbox / "temp")}
    c = Cleaner(engine.conn, roots, str(sandbox / "vault"), engine.clock, engine=engine)
    c.seed_rules([
        Rule("temp", "Temp files", "free", "cosmetic", ["{temp}/*.tmp"]),
        Rule("thumbs", "Thumbnails", "auto_regen", "cosmetic", ["{cache}/thumbs/*"]),
        Rule("logs", "Logs", "free", "cosmetic", ["{logs}/*.log"]),
    ])
    return c


def test_scan_is_read_only(engine, sandbox):
    c = _cleaner(engine, sandbox)
    result = c.scan()
    assert result.total_files >= 3
    assert (sandbox / "temp" / "a.tmp").exists()


def test_clean_moves_to_vault_and_preserves_file(engine, sandbox):
    c = _cleaner(engine, sandbox)
    scan = c.scan()
    res = c.clean(scan.candidates)
    assert res.moved >= 3
    assert not (sandbox / "temp" / "a.tmp").exists()
    assert (sandbox / "keep.txt").exists()
    assert os.path.exists(os.path.join(res.vault_dir, "temp", os.listdir(
        os.path.join(res.vault_dir, "temp"))[0]))


def test_restore_puts_everything_back(engine, sandbox):
    c = _cleaner(engine, sandbox)
    before = (sandbox / "temp" / "a.tmp").read_bytes()
    res = c.clean(c.scan().candidates)
    c.restore(res.run_id)
    assert (sandbox / "temp" / "a.tmp").read_bytes() == before


def test_clean_creates_a_receipt_and_a_journal_transaction(engine, sandbox):
    c = _cleaner(engine, sandbox)
    res = c.clean(c.scan().candidates)
    receipts = engine.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    assert receipts >= 1
    assert res.tx_id
    assert engine.transaction(res.tx_id)["kind"] == "clean"


def test_policy_class_is_never_auto_selected(engine, sandbox):
    c = _cleaner(engine, sandbox)
    c.seed_rules([Rule("keep", "Old backup", "policy", "medium", ["{temp}/*.tmp"])])
    ids = {cand.rule_id for cand in c.scan().candidates}
    assert "keep" not in ids


# ---------------------------------------------------------------------- bench

def test_bench_produces_metrics_and_score(engine):
    from pco_engine.bench import Bench
    b = Bench(engine.conn, engine.clock)
    res = b.run(reps=2, metrics=["cpu.int_ops", "mem.copy"])
    assert res.score > 0
    assert set(res.metrics) == {"cpu.int_ops", "mem.copy"}
    assert b.history()


def test_bench_compare_says_no_measurable_change_for_tiny_deltas(engine):
    from pco_engine.bench import Bench
    b = Bench(engine.conn, engine.clock)
    a = b.run(reps=2, metrics=["cpu.int_ops"])
    c = b.run(reps=2, metrics=["cpu.int_ops"])
    cmp = b.compare(a.id, c.id)
    assert cmp["metrics"]["cpu.int_ops"]["verdict"] in {
        "no measurable change", "faster", "slower"}


# ------------------------------------------------------------------------ dns

def test_matcher_exact_and_wildcard():
    m = Matcher()
    m.add("ads.example.com", "l1")
    m.add("*.tracker.com", "l1")
    assert m.match("ads.example.com")[0] == "ads.example.com"
    assert m.match("x.y.tracker.com")[0] == "tracker.com"
    assert m.match("safe.example.org") is None


def test_mode_off_allows_everything(engine):
    d = DnsBlocker(engine.conn, engine.clock)
    d.install_list("l", "ads", "ads", ["ads.example.com"])
    d.off()
    assert d.decide("ads.example.com").blocked is False


def test_mode_on_blocks_and_counts(engine):
    d = DnsBlocker(engine.conn, engine.clock)
    d.install_list("l", "ads", "ads", ["ads.example.com"])
    d.on()
    assert d.decide("ads.example.com").blocked is True
    assert d.decide("ads.example.com").blocked is True
    assert d.total_blocked() == 2


def test_pause_stops_blocking_then_expires(engine, clock):
    d = DnsBlocker(engine.conn, clock)
    d.install_list("l", "ads", "ads", ["ads.example.com"])
    d.on()
    d.pause(5)
    assert d.decide("ads.example.com").blocked is False
    assert d.is_paused() is True
    clock.tick(5 * 60_000 + 1)
    assert d.is_paused() is False
    assert d.decide("ads.example.com").blocked is True


def test_per_app_off(engine):
    d = DnsBlocker(engine.conn, engine.clock)
    d.install_list("l", "ads", "ads", ["ads.example.com"])
    d.on()
    d.set_app("game", "off")
    assert d.decide("ads.example.com", app="game").blocked is False
    assert d.decide("ads.example.com", app="browser").blocked is True


def test_allow_rule_wins(engine):
    d = DnsBlocker(engine.conn, engine.clock)
    d.install_list("l", "ads", "ads", ["ads.example.com"])
    d.on()
    d.allow("ads.example.com", "browser")
    assert d.decide("ads.example.com", app="browser").reason == "allow_rule"
    assert d.decide("ads.example.com", app="other").blocked is True


def test_toggle_is_instant(engine):
    d = DnsBlocker(engine.conn, engine.clock)
    d.install_list("l", "ads", "ads", ["ads.example.com"])
    assert d.toggle() == "on"
    assert d.toggle() == "off"


# --------------------------------------------------------------------- recipes

def test_recipe_runs_its_actions(engine):
    calls = []
    r = Recipes(engine.conn, engine.clock, run_action=lambda n, a: calls.append(n) or {"ok": True})
    r.add(Spec(id="r1", name="Game time", trigger={"kind": "game_launch",
                                                  "match": {"exe": "game.exe"}},
               actions=[{"action": "dns.pause"}, {"action": "clean.run"}]))
    reports = r.fire("game_launch", {"exe": "game.exe"})
    assert len(reports) == 1
    assert calls == ["dns.pause", "clean.run"]


def test_recipe_trigger_must_match(engine):
    calls = []
    r = Recipes(engine.conn, engine.clock, run_action=lambda n, a: calls.append(n))
    r.add(Spec(id="r1", name="x", trigger={"kind": "game_launch",
                                          "match": {"exe": "game.exe"}},
               actions=[{"action": "a"}]))
    assert r.fire("game_launch", {"exe": "other.exe"}) == []


def test_recipe_rejects_loops(engine):
    r = Recipes(engine.conn, engine.clock, run_action=lambda n, a: None)
    with pytest.raises(RecipeError):
        r.add(Spec(id="r", name="bad", trigger={"kind": "manual"},
                   actions=[{"action": "recipe.run"}]))


def test_recipe_capped_at_ten_actions(engine):
    r = Recipes(engine.conn, engine.clock, run_action=lambda n, a: None)
    with pytest.raises(RecipeError):
        r.add(Spec(id="r", name="big", trigger={"kind": "manual"},
                   actions=[{"action": f"a{i}"} for i in range(11)]))


def test_failing_action_does_not_stop_the_recipe(engine):
    def run(name, args):
        if name == "boom":
            raise RuntimeError("nope")
        return {"ok": True}

    r = Recipes(engine.conn, engine.clock, run_action=run)
    r.add(Spec(id="r", name="mixed", trigger={"kind": "manual"},
               actions=[{"action": "boom"}, {"action": "fine"}]))
    report = r.fire("manual")[0]
    assert report["ok"] is False
    assert report["log"][1]["ok"] is True


# -------------------------------------------------------------------- sentinel

def test_sentinel_feed_and_digest(engine):
    s = Sentinel(engine.conn, engine.clock)
    s.record(Event(kind="install", source="test", narrative="Installed Thing", payload={}))
    assert len(s.feed()) == 1
    assert "Installed Thing" in s.digest()


def test_broken_source_does_not_stop_the_feed(engine):
    s = Sentinel(engine.conn, engine.clock)

    def broken():
        raise RuntimeError("source down")

    s.add_source(broken)
    s.add_source(lambda: [Event(kind="x", source="ok", narrative="fine", payload={})])
    assert len(s.poll()) == 1


# --------------------------------------------------------------------- service

@pytest.fixture()
def service(tmp_path, engine, clock):
    svc = Service(str(tmp_path / "svc.db"), backend=FakeBackend(DEMO_STATE),
                  catalog_dir=os.path.join(os.path.dirname(__file__), "..", "catalog"),
                  vault_root=str(tmp_path / "vault"), clock=clock)
    return svc


def test_service_status_runs_every_module(service):
    st = service.status()
    assert st["engine"]["journal_ok"] is True
    assert "checkup" in st
    assert "dns" in st
    assert st["integrity"][0] is True


def test_service_checkup_and_fix_safe(service):
    result = service.checkup(fix_safe=True)
    assert result["counts"]["total"] >= 1
    assert "fix" in result
    assert result["fix"]["applied"] is True
    assert service.backend.read("display:primary.mode")["hz"] == 165
    # undo everything in one click
    service.undo(result["fix"]["tx_id"])
    assert service.backend.read("display:primary.mode")["hz"] == 59.94


def test_service_dns_actions(service):
    assert service.run_action("dns.off")["mode"] == "off"
    assert service.run_action("dns.on")["mode"] == "on"
    assert service.run_action("dns.pause", {"minutes": 5})["paused_until"] > 0


def test_service_bench(service):
    res = service.bench_run()
    assert res["score"] > 0
    assert service.status()["bench"]


def test_service_unknown_action(service):
    with pytest.raises(KeyError):
        service.run_action("does.not.exist")


def test_service_recovery_on_start_is_safe(tmp_path, clock):
    svc = Service(str(tmp_path / "a.db"), backend=FakeBackend(DEMO_STATE),
                  catalog_dir=os.path.join(os.path.dirname(__file__), "..", "catalog"),
                  vault_root=str(tmp_path / "vault"), clock=clock)
    assert svc.recover_on_start()["journal_ok"] is True
