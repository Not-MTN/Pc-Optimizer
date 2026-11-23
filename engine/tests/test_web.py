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


def test_bridge_studio_refuses_overbudget_theme(live_server):
    themes = _get(live_server, "/api/studio/themes")["themes"]
    heavy = max(themes, key=lambda t: t["cost_pct"])
    _, res = _post(live_server, "/api/studio/apply", {"theme_id": heavy["id"]})
    assert res["applied"] is False
    assert res["over_pct"] > 0


def test_bridge_studio_import_is_not_apply(live_server):
    _, res = _post(live_server, "/api/studio/import",
                   {"doc": {"name": "imported", "features": {"widgets_light": True}}})
    assert res["valid"] is True and res["governor"]["ok"] is True


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
