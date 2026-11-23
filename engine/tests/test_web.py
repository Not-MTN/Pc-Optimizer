"""Sens Lab + the HTTP bridge, tested end to end over a real socket."""
from __future__ import annotations

import json
import os
import threading
import urllib.request

import pytest

from pco_engine.sens import (GAMES, YAW, SensLab, cm_per_360, edpi, sens_for,
                             turn_space_cm)


# ------------------------------------------------------------------ pure maths

def test_cm360_matches_the_published_formula():
    # cs2 @ 800 dpi, sens 1.6 → 32.47 cm/360
    assert round(cm_per_360(800, 1.6, 0.022), 2) == 32.47


def test_round_trip_is_exact():
    cm = cm_per_360(800, 1.6, 0.022)
    assert round(sens_for(cm, 800, 0.022), 9) == 1.6


def test_cs2_to_valorant_is_the_known_ratio():
    """Community constant: CS2 sens ÷ 3.18 ≈ Valorant sens."""
    cs = 1.6
    val = sens_for(cm_per_360(800, cs, YAW["cs2"]), 800, YAW["valorant"])
    assert round(cs / val, 2) == 3.18


def test_edpi_is_scoped_to_one_game():
    assert edpi(800, 1.6) == pytest.approx(1280)


def test_turn_space_helper():
    # a 40 cm/360 player on a 45 cm pad with 80% usable width
    assert round(turn_space_cm(40, 45), 2) == 0.9


def test_bad_input_is_refused():
    with pytest.raises(ValueError):
        cm_per_360(0, 1.6, 0.022)
    with pytest.raises(ValueError):
        sens_for(40, 800, 0)


# --------------------------------------------------------------------- the lab

def test_from_game_stores_cm360(engine):
    lab = SensLab(engine.conn, engine.clock)
    prof = lab.from_game("cs2", 1.6, 800)
    assert prof.cm360 == pytest.approx(32.472, abs=1e-2)
    assert lab.get().dpi == 800


def test_table_has_every_game_and_flags_non_linear(engine):
    lab = SensLab(engine.conn, engine.clock)
    lab.save(cm360=40, dpi=800)
    table = {g["id"]: g for g in lab.table()}
    assert set(table) == set(GAMES)
    assert table["cs2"]["sens"] is not None
    assert table["r6s"]["sens"] is None          # non-linear ADS: refused, not faked
    assert table["r6s"]["exact"] is False


def test_table_sens_is_consistent_across_engines(engine):
    """Same physical value must give the same cm/360 everywhere."""
    lab = SensLab(engine.conn, engine.clock)
    lab.save(cm360=40, dpi=800)
    for g in lab.table():
        if g["sens"] is None:
            continue
        back = cm_per_360(800, g["sens"], YAW[g["id"]])
        assert back == pytest.approx(40, rel=1e-3)


def test_profiles_are_one_row_per_game(engine):
    lab = SensLab(engine.conn, engine.clock)
    lab.save(cm360=40, dpi=800, game="cs2")
    lab.save(cm360=45, dpi=1200, game="cs2")
    rows = engine.conn.execute("SELECT COUNT(*) FROM sens_profiles").fetchone()[0]
    assert rows == 1                      # upsert, not append
    assert lab.get("cs2").cm360 == 45 and lab.get("cs2").dpi == 1200


def test_psa_converges_below_one_centimetre(engine):
    """Simulate a player whose true optimum is 33 cm/360: every round they pick
    the candidate closer to it, exactly as a human would pick 'felt better'."""
    lab = SensLab(engine.conn, engine.clock)
    lab.save(cm360=40, dpi=800)
    state = lab.psa_start()
    target, rounds = 33.0, 0
    while not state.get("done"):
        rounds += 1
        better = "a" if abs(state["a"] - target) < abs(state["b"] - target) else "b"
        state = lab.psa_step(state, better)
        assert rounds <= 7
    assert rounds == 6                    # halving: 36 → 18 → … → 0.56 cm
    assert state["width"] < 1.0
    assert abs(state["value"] - target) < 1.0
    assert lab.get().cm360 == pytest.approx(state["locked_cm360"])


def test_psa_rejects_a_nonsense_answer(engine):
    lab = SensLab(engine.conn, engine.clock)
    with pytest.raises(ValueError):
        lab.psa_step(lab.psa_start(), "middle")


# ------------------------------------------------------------------ http bridge

@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """A real HTTP server on a real socket — the bridge is what the UI talks to,
    so it deserves a test that goes through sockets rather than function calls."""
    from http.server import ThreadingHTTPServer
    from pco_engine import server as srv

    data = tmp_path_factory.mktemp("srv")
    os.environ["PCO_DATA"] = str(data)
    # Pin the simulated machine. Without this, `auto` picks the *real* backend on
    # Windows and the demo fixtures stop existing — which is exactly how this
    # suite failed on windows-latest while passing everywhere else.
    os.environ["PCO_BACKEND"] = "demo"
    os.environ["PCO_CATALOG"] = os.path.join(os.path.dirname(__file__), "..", "catalog")
    srv._SVC = None
    srv._service()
    os.environ["PCO_VAULT"] = str(data / "vault")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    srv._SVC = None
    os.environ.pop("PCO_BACKEND", None)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.loads(r.read())


def _post(base, path, body):
    req = urllib.request.Request(base + path, method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_bridge_serves_the_console(live_server):
    with urllib.request.urlopen(live_server + "/", timeout=20) as r:
        html = r.read().decode()
    assert "PC Optimizer" in html
    assert "/api/status" in html


def test_bridge_health_and_status(live_server):
    assert _get(live_server, "/api/health")["ok"] is True
    st = _get(live_server, "/api/status")
    assert st["integrity"][0] is True
    assert "dns" in st and "engine" in st


def test_bridge_checkup_then_fix_then_undo(live_server):
    ck = _get(live_server, "/api/checkup")
    assert ck["counts"]["total"] >= 1

    code, resp = _post(live_server, "/api/checkup/fix", {})
    assert code == 200
    fix = resp["fix"]
    assert fix["applied"] is True, fix
    tx = fix["tx_id"]

    code, undone = _post(live_server, "/api/undo", {"tx_id": tx})
    assert code == 200 and undone["reverted"] >= 1

    st = _get(live_server, "/api/status")
    assert st["engine"]["journal_ok"] is True


def test_bridge_clean_scan_then_clean(live_server):
    scan = _post(live_server, "/api/clean/scan", {})[1]
    assert scan["total_files"] >= 3            # the demo machine has demo junk
    run = _post(live_server, "/api/clean/run", {})[1]
    assert run["moved"] >= 3
    restored = _post(live_server, "/api/clean/restore", {"run_id": run["run_id"]})[1]
    assert restored["restored"] == run["moved"]


def test_bridge_dns_toggle_and_pause(live_server):
    assert _post(live_server, "/api/dns/on", {})[1]["mode"] == "on"
    assert _post(live_server, "/api/dns/off", {})[1]["mode"] == "off"
    assert _post(live_server, "/api/dns/pause", {"minutes": 5})[1]["paused_until"] > 0
    st = _get(live_server, "/api/status")
    assert st["dns"]["paused"] is True


def test_bridge_sens_round_trip(live_server):
    _, conv = _post(live_server, "/api/sens/from-game",
                    {"game": "cs2", "sens": 1.6, "dpi": 800})
    assert conv["profile"]["cm360"] == pytest.approx(32.47, abs=0.01)
    table = {g["id"]: g for g in _get(live_server, "/api/sens")["games"]}
    # the stored value round-trips: CS2 asks for the same 1.6 back
    assert table["cs2"]["sens"] == pytest.approx(1.6, abs=0.01)
    # and VALORANT is the published ÷3.18 away
    assert table["valorant"]["sens"] == pytest.approx(1.6 / 3.18, abs=0.01)


def test_bridge_bench(live_server):
    res = _post(live_server, "/api/bench/run", {})[1]
    assert res["score"] > 0
    assert _get(live_server, "/api/status")["bench"]


def test_bridge_errors_are_json_not_stack_traces_to_the_ui(live_server):
    code, body = _post(live_server, "/api/apply", {"tweaks": []})
    assert code in (400, 409)
    assert "error" in body or "reason" in body or "applied" in body
    assert "Traceback" not in json.dumps(body)


# ============================================================ product endpoints

def test_bridge_gaming_watch_and_plan(live_server):
    _, res = _post(live_server, "/api/game/watch", {"game": "fortnite", "seconds": 12})
    assert res["grade"] and res["stats"]["fps_p1"] > 0
    assert res["bottleneck"]["key"] in {
        "gpu", "cpu", "vram", "ram", "thermal", "storage", "network", "balanced"}
    assert res["verdict"]

    _, plan = _post(live_server, "/api/game/plan",
                    {"game": "fortnite", "session_id": res["session_id"]})
    assert plan["settings"]
    assert "%" in plan["expected"]


def test_bridge_game_library_has_the_known_games(live_server):
    lib = _get(live_server, "/api/games/library")["games"]
    ids = {g["id"] for g in lib}
    assert {"cs2", "valorant", "apex", "fortnite", "tarkov"} <= ids
    cs2 = next(g for g in lib if g["id"] == "cs2")
    assert cs2["yaw"] == 0.022
    assert cs2["engine"] == "Source 2"
    # sessions are counted live, not asserted against a fixed number
    assert isinstance(cs2["sessions"], int)


def test_bridge_consent_default_then_trusted(live_server):
    c = _get(live_server, "/api/consent")
    assert c["level"] in {"safe", "trusted"}

    _, trusted = _post(live_server, "/api/consent/set", {"trust_everything": True})
    assert trusted["level"] == "trusted"
    assert all(v == "trusted" for v in trusted["categories"].values())

    _, back = _post(live_server, "/api/consent/set",
                    {"level": "safe", "categories": {"privacy": "off"}})
    assert back["categories"]["privacy"] == "off"


def test_bridge_fix_respects_consent_and_reports_withheld(live_server):
    _post(live_server, "/api/consent/set", {"level": "safe",
                                            "categories": {"privacy": "off",
                                                           "storage": "off",
                                                           "reliability": "off",
                                                           "performance": "safe",
                                                           "security": "safe"}})
    _get(live_server, "/api/checkup")
    code, res = _post(live_server, "/api/checkup/apply", {})
    assert code == 200
    assert "withheld" in res
    assert any(w["why"] == "no declared fix" for w in res["withheld"])


def test_bridge_studio_applies_the_heavy_theme_and_reports_cost(live_server):
    """Over HTTP, the heavy theme must apply and carry its cost. The old
    assertion here was that it was refused; the product rule changed because a
    cost is not a permission slip."""
    themes = _get(live_server, "/api/studio/themes")["themes"]
    heavy = max(themes, key=lambda t: t["cost_pct"])
    _, res = _post(live_server, "/api/studio/apply", {"theme_id": heavy["id"]})
    assert res["applied"] is True
    assert res["cost"]["blocked"] is False
    assert res["cost_pct"] > 0


def test_bridge_studio_import_is_not_apply(live_server):
    _, res = _post(live_server, "/api/studio/import",
                   {"doc": {"name": "imported", "features": {"widgets_light": True}}})
    assert res["valid"] is True
    assert res["cost"]["blocked"] is False
    assert res["zero_cost"]["features"]["widgets_light"] is False


def test_bridge_autopsy_flow_over_http(live_server):
    _, begun = _post(live_server, "/api/autopsy/begin", {"installer": "nonsense.exe"})
    aid = begun["autopsy_id"]
    assert aid
    listed = _get(live_server, "/api/autopsy")["autopsies"]
    assert any(a["id"] == aid for a in listed)


# ----------------------------------------- autopsy over the bridge, end to end

def test_bridge_autopsy_records_erases_and_keeps_critical(live_server):
    """The demo watcher's findings must survive the round trip over HTTP: the
    console posts what it discovered, then erases exactly that."""
    _, begun = _post(live_server, "/api/autopsy/begin", {"installer": "nonsense.exe"})
    aid = begun["autopsy_id"]

    items = [
        {"class": "service", "locator": "NonsenseAgent"},
        {"class": "registry", "locator": "HKLM\\SOFTWARE\\Nonsense"},
        {"class": "file", "locator": "C:\\Program Files\\Nonsense\\app.exe", "size": 84_000_000},
        {"class": "file", "locator": "C:\\Windows\\System32\\vcruntime140.dll",
         "size": 420_000, "critical": True},
    ]
    _, noted = _post(live_server, "/api/autopsy/note", {"autopsy_id": aid, "items": items})
    assert noted["recorded"] == 4
    assert noted["autopsy"]["total_items"] == 4
    assert len(noted["autopsy"]["critical_items"]) == 1

    _, finished = _post(live_server, "/api/autopsy/finish", {"autopsy_id": aid})
    assert finished["total_items"] == 4

    _, erased = _post(live_server, "/api/autopsy/erase", {"autopsy_id": aid})
    assert erased["erased"] == 3, erased
    assert erased["kept"] == 1          # the runtime is immune
    assert erased["snapshot_id"]


def test_bridge_rejects_bad_theme_json_with_400_not_500(live_server):
    code, body = _post(live_server, "/api/studio/import",
                       {"doc": {"name": "bad", "features": {"hologram": True}}})
    assert code == 400
    assert "hologram" in body["error"]
    assert "Traceback" not in body["error"]


def test_bridge_rejects_bad_consent_level_with_400(live_server):
    code, body = _post(live_server, "/api/consent/set", {"level": "godmode"})
    assert code == 400
    assert "level must be one of" in body["error"]


# ============================================== intel endpoints over the bridge

def test_bridge_startup_inventory_and_plan(live_server):
    d = _get(live_server, "/api/startup")
    assert d["available"] is True
    assert d["report"]["measured_items"] >= 10
    assert d["report"]["unattributed_items"] >= 1        # never invented
    assert d["plan"]["saving_ms"] > 0
    assert any(k["refusal"] for k in d["knowledge"])      # at least one refusal


def test_bridge_palette_search_and_run(live_server):
    hits = _get(live_server, "/api/palette?q=dns")
    assert hits["results"] and hits["results"][0]["verb"].startswith("dns")
    code, out = _post(live_server, "/api/palette/run", {"verb": "help"})
    assert code == 200 and out["command"] == "help"


def test_bridge_palette_refuses_a_shell_command(live_server):
    code, body = _post(live_server, "/api/palette/run", {"verb": "shell-anything"})
    assert code == 400
    assert "unknown command" in body["error"]


def test_bridge_space_history_and_forecast(live_server):
    grew = _get(live_server, "/api/space?days=7")
    assert grew["available"] is True and grew["changed"]
    fc = _get(live_server, "/api/space/forecast")
    assert fc["available"] is True


def test_bridge_duplicates_are_confirmed(live_server):
    d = _post(live_server, "/api/dupes", {})[1]
    assert d["group_count"] >= 1
    assert all(len(g["files"]) >= 2 for g in d["groups"])
    assert d["method"].startswith("size")


def test_bridge_ask_answers_from_the_journal(live_server):
    out = _post(live_server, "/api/ask", {"question": "what did I change?"})[1]
    assert out["intent"] == "what_changed"
    assert "journal" in out["note"]


def test_bridge_report_renders_printable_html(live_server):
    with urllib.request.urlopen(live_server + "/api/report/html", timeout=20) as r:
        html = r.read().decode()
    assert html.startswith("<!DOCTYPE html>")
    assert "PC Health Report" in html


def test_bridge_peripheral_test_records_a_verdict(live_server):
    _, out = _post(live_server, "/api/peripheral/test",
                   {"kind": "mouse_double_click",
                    "payload": {"intervals_ms": [300, 11, 290, 9, 280]}})
    assert out["verdict"] == "fail"
    assert "hardware fault" in " ".join(out["advice"])
    hist = _get(live_server, "/api/peripheral")["history"]
    assert hist and hist[0]["verdict"] == "fail"


# ------------------------------------------------- v0.5 over the real socket

def test_bridge_power_reports_health_and_drain(live_server):
    out = _get(live_server, "/api/power")
    assert out["battery"]["available"] is True
    diag = out["battery"]["diagnosis"]
    assert diag["health_pct"] == pytest.approx(78.0)
    assert diag["level"] == "warn"
    dr = _get(live_server, "/api/power/drains")
    names = [d["process"] for d in dr["drains"]]
    # a named process always outranks the residual bucket
    assert names[-1] == "(unattributed)"
    assert "chrome.exe" in names
    shares = sum(d["share_pct"] for d in dr["drains"])
    assert 99.0 <= shares <= 101.0


def test_bridge_thermal_never_claims_throttling_from_heat_alone(live_server):
    out = _get(live_server, "/api/power")
    assert out["thermal"]["throttled"] is False
    assert "No throttling recorded" in out["thermal"]["verdict"]
    assert out["charge_limit"]["supported"] is False


def test_bridge_workspace_capture_then_preview_then_restore(live_server):
    _, saved = _post(live_server, "/api/workspace/capture", {"name": "Bridge"})
    assert saved["saved"] is True
    assert len(saved["entries"]) >= 5

    ws = _get(live_server, "/api/workspaces")["workspaces"]
    assert ws and ws[0]["name"] == "Bridge"

    dry = _post(live_server, "/api/workspace/restore",
                {"id": ws[0]["id"], "dry_run": True})[1]
    assert dry["dry_run"] is True and "Nothing started" in dry["summary"]

    real = _post(live_server, "/api/workspace/restore",
                 {"id": ws[0]["id"], "dry_run": False})[1]
    # no launcher exists on this build: every item must say so, not pretend
    assert real["started"] == 0
    assert real["failed"] and "no launcher" in real["failed"][0]["error"]


def test_bridge_drivers_grade_from_local_history_only(live_server):
    out = _get(live_server, "/api/drivers")
    by_version = {d["version"]: d["score"] for d in out["drivers"]}
    assert by_version["566.36"]["grade"] == "bad"
    assert by_version["561.09"]["grade"] == "good"
    unknown = [s for v, s in by_version.items() if s["grade"] == "unknown"]
    assert unknown, "silence must be reported as unknown, not assumed good"
    assert all("not" in s["verdict"] or "no" in s["verdict"].lower() for s in unknown)


def test_bridge_recipe_parse_never_saves_or_runs(live_server):
    before = len(_get(live_server, "/api/recipes")["recipes"])
    out = _post(live_server, "/api/recipe/parse",
                {"sentence": "clean temp files older than 14 days"})[1]
    assert out["understood"] is True
    assert out["recipe"]["actions"]
    assert len(_get(live_server, "/api/recipes")["recipes"]) == before

    bad = _post(live_server, "/api/recipe/parse",
                {"sentence": "make me a sandwich"})[1]
    assert bad["understood"] is False
    assert bad["problems"], "a refusal must name what it could not parse"
    assert len(_get(live_server, "/api/recipes")["recipes"]) == before


def test_bridge_recipe_save_toggle_fire(live_server):
    created = _post(live_server, "/api/recipe/create",
                    {"sentence": "every day at 03:00, run a checkup"})[1]
    assert created["saved"] is True
    rid = created["recipe_id"]

    listed = _get(live_server, "/api/recipes")["recipes"]
    assert rid in [r["id"] for r in listed]

    off = _post(live_server, "/api/recipe/toggle",
                {"recipe_id": rid, "enabled": False})[1]
    assert off["enabled"] is False

    runs = _post(live_server, "/api/recipe/fire",
                 {"trigger": "manual", "context": {}})[1]["runs"]
    assert runs == [] or rid not in [r.get("recipe_id") for r in runs]

    _post(live_server, "/api/recipe/toggle", {"recipe_id": rid, "enabled": True})
    _post(live_server, "/api/recipe/delete", {"recipe_id": rid})
    assert rid not in [r["id"] for r in _get(live_server, "/api/recipes")["recipes"]]


def test_bridge_boot_status_and_panic(live_server):
    boot = _get(live_server, "/api/boot")
    assert boot["mode"] in {"normal", "read_only"}
    assert boot["events"], "the boot guard must have recorded a boot"

    _post(live_server, "/api/checkup/fix", {})
    out = _post(live_server, "/api/safety/panic", {})[1]
    assert "Reverted" in out["summary"]
    assert out["dns_mode"] == "off"


def test_bridge_safety_pending_is_honest_when_empty(live_server):
    out = _get(live_server, "/api/safety")
    assert isinstance(out["pending"], list)


# --------------------------------------------------- desktop lab over the wire

def test_bridge_shell_offers_only_what_windows_supports(live_server):
    out = _get(live_server, "/api/shell")
    assert out["supported"] >= 12
    assert out["refused"] >= 4
    refused = [o for o in out["options"] if not o["supported"]]
    assert all(len(o["plain"]) > 60 for o in refused), (
        "a refusal that does not explain itself is just a missing feature")
    supported = [o for o in out["options"] if o["supported"]]
    assert all(o["address"].startswith("registry:hkcu\\") for o in supported), (
        "desktop preferences are per-user; writing HKLM would need elevation "
        "for something that is a matter of taste")


def test_bridge_shell_preview_writes_nothing(live_server):
    before = _get(live_server, "/api/shell")
    align = next(o for o in before["options"] if o["key"] == "taskbar.align")
    out = _post(live_server, "/api/shell/preview",
                {"settings": {"taskbar.align": "left"}})[1]
    assert out["ok"] is True
    assert out["changes"][0]["value"] == 0
    after = _get(live_server, "/api/shell")
    still = next(o for o in after["options"] if o["key"] == "taskbar.align")
    assert still["current"] == align["current"], "preview must not write"


def test_bridge_shell_apply_then_undo_round_trips(live_server):
    out = _post(live_server, "/api/shell/apply",
                {"settings": {"taskbar.align": "left"}})[1]
    assert out["applied"] is True
    current = [o for o in _get(live_server, "/api/shell")["options"]
               if o["key"] == "taskbar.align"][0]["current"]
    assert current == "left"

    _post(live_server, "/api/undo", {"tx_id": out["tx_id"]})
    back = [o for o in _get(live_server, "/api/shell")["options"]
            if o["key"] == "taskbar.align"][0]["current"]
    assert back == "center"


def test_bridge_refuses_a_taskbar_position_change_with_a_reason(live_server):
    out = _post(live_server, "/api/shell/apply",
                {"settings": {"taskbar.position": "top"}})[1]
    assert out["applied"] is False
    assert "Windows 11" in out["refused"][0]["reason"]


def test_bridge_shell_theme_save_and_import(live_server):
    saved = _post(live_server, "/api/shell/theme/save",
                  {"name": "Bridge Theme",
                   "settings": {"taskbar.widgets": "off"}})[1]
    assert saved["saved"] is True
    names = [t["name"] for t in _get(live_server, "/api/shell/themes")["themes"]]
    assert "Bridge Theme" in names

    bad = _post(live_server, "/api/shell/theme/import",
                {"doc": {"name": "nope", "settings": {"bogus.key": 1}}})[1]
    assert bad["imported"] is False
    assert bad["problems"]


def test_bridge_prompt_identity_names_its_evidence(live_server):
    out = _post(live_server, "/api/shell/prompt", {"command": "gh pr list"})[1]
    assert out["key"] == "github"
    assert out["glyph"], "the mark must be present when the provider is known"
    assert out["evidence"]

    vague = _get(live_server, "/api/shell/prompt")
    assert vague["confidence"] in {"confirmed", "likely", "none"}


def test_every_get_route_answers_without_a_body(live_server):
    """A GET route that needs a parameter must fail with a 4xx that names it,
    never a 500. This exists because `/api/themes/get?theme_id=…` returned 500
    for hours: the handler read `theme_id` from a POST body the console never
    sent, and the only symptom was a button that did nothing."""
    import urllib.error
    import urllib.request
    for path in ("/api/shell", "/api/themes", "/api/display", "/api/status",
                 "/api/palette/all", "/api/catalog"):
        try:
            with urllib.request.urlopen(live_server + path, timeout=10) as r:
                assert r.status == 200, f"{path} -> {r.status}"
        except urllib.error.HTTPError as exc:
            raise AssertionError(f"{path} -> {exc.code}, expected 200") from exc


def test_query_parameters_reach_the_action(live_server):
    """The console sends /api/themes/get?theme_id=keel. The server must read the
    query string, not only a request body."""
    r = _get(live_server, "/api/themes/get?theme_id=keel")
    assert r["ok"] is True
    assert r["doc"]["meta"]["name"] == "Keel"


def test_an_unknown_theme_is_a_4xx_not_a_500(live_server):
    """Bad input is the caller's problem and must be reported as such."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(
                live_server + "/api/themes/get?theme_id=nope", timeout=10) as r:
            body = r.read()
        # either a 200 carrying ok:false, or a 4xx — never a 5xx
        assert b"nope" in body
    except urllib.error.HTTPError as exc:
        assert 400 <= exc.code < 500, f"expected 4xx, got {exc.code}"


def test_the_export_route_answers_over_http(live_server):
    r = _get(live_server, "/api/themes/export?theme_id=dusk")
    assert r["ok"] is True
    assert '"name": "Dusk"' in r["text"]
