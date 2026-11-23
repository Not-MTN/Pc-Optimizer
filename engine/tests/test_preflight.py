"""Preflight must catch problems before the machine is touched."""
import pytest

from pco_engine import Catalog, Preflight, parse_tweak
from pco_engine.preflight import Issue


def test_clean_plan_has_no_errors(catalog, backend):
    plan = Preflight(catalog, backend).simulate(["display.set_refresh"])
    assert plan.ok, [i.message for i in plan.errors]
    assert plan.effects[0].new == {"hz": 165}
    assert plan.effects[0].old == {"hz": 59.94, "width": 2560, "height": 1440}


def test_unknown_tweak_blocks(engine):
    plan = engine.preflight.simulate(["does.not.exist"])
    assert not plan.ok
    assert plan.errors[0].code == "tweak.unknown"


def test_conflict_between_two_tweaks_in_one_batch(engine, catalog, backend):
    """Two tweaks writing different values to the same address must be refused."""
    a = parse_tweak({"id": "x.a", "module": "t", "title": "A", "plain": "a", "tier": "T1",
                     "risk": "low",
                     "effects": [{"kind": "registry_value",
                                  "target": "HKLM\\Software\\Acme\\Telemetry", "to": 0}]})
    b = parse_tweak({"id": "x.b", "module": "t", "title": "B", "plain": "b", "tier": "T1",
                     "risk": "low",
                     "effects": [{"kind": "registry_value",
                                  "target": "HKLM\\Software\\Acme\\Telemetry", "to": 2}]})
    cat = Catalog([*catalog.all(), a, b])
    plan = Preflight(cat, backend).simulate(["x.a", "x.b"])
    assert not plan.ok
    codes = {i.code for i in plan.errors}
    assert "effect.conflict" in codes


def test_redundant_writes_are_a_warning_not_an_error(engine, catalog, backend):
    a = parse_tweak({"id": "y.a", "module": "t", "title": "A", "plain": "a", "tier": "T1",
                     "risk": "low",
                     "effects": [{"kind": "registry_value",
                                  "target": "HKLM\\Software\\Acme\\Telemetry", "to": 0}]})
    b = parse_tweak({"id": "y.b", "module": "t", "title": "B", "plain": "b", "tier": "T1",
                     "risk": "low",
                     "effects": [{"kind": "registry_value",
                                  "target": "HKLM\\Software\\Acme\\Telemetry", "to": 0}]})
    cat = Catalog([*catalog.all(), a, b])
    plan = Preflight(cat, backend).simulate(["y.a", "y.b"])
    assert plan.ok
    assert any(i.code == "effect.redundant" for i in plan.warnings)


def test_precondition_failure_blocks(catalog, backend):
    """`not_equals` is the honest way to gate a tweak on the *current* state, so
    a plan is refused once the machine already matches the target."""
    from pco_engine import parse_tweak
    t = parse_tweak({"id": "z.power", "module": "power", "title": "High plan", "plain": "p",
                     "tier": "T1", "risk": "low",
                     "effects": [{"kind": "power_setting", "target": "active_scheme",
                                  "to": "high"}],
                     "preconditions": [{"kind": "not_equals", "what": "power:active_scheme",
                                        "value": "high"}]})
    cat = Catalog([*catalog.all(), t])
    assert Preflight(cat, backend).simulate(["z.power"]).ok is True
    backend.write("power:active_scheme", "high")
    plan = Preflight(cat, backend).simulate(["z.power"])
    assert not plan.ok
    fails = [i for i in plan.errors
             if i.code in {"precondition.failed", "precondition.mismatch",
                           "precondition.already_present"}]
    assert fails, [i.message for i in plan.errors]


def test_tweak_whose_goal_is_its_precondition_is_still_plannable(catalog, backend):
    """Regression: power.scheme.high used to demand scheme == 'high' *before*
    applying it, so it could never be fixed from the Balanced state."""
    assert Preflight(catalog, backend).simulate(["power.scheme.high"]).ok


def test_tier_blocked_when_not_enabled(catalog, backend):
    plan = Preflight(catalog, backend).simulate(["driver.t2.example"],
                                                allow_tier={"T0", "T1"})
    assert not plan.ok
    assert plan.errors[0].code == "tier.blocked"


def test_noop_detected(engine):
    engine.apply("display.set_refresh")
    plan = engine.preflight.simulate(["display.set_refresh"])
    assert plan.ok
    assert any(i.code == "effect.noop" for i in plan.issues)


def test_requires_confirmation_for_high_risk(catalog, backend):
    plan = Preflight(catalog, backend).simulate(["security.smb1.disable"])
    assert plan.ok
    assert plan.requires_confirmation is True


def test_simulation_is_read_only(catalog, backend):
    before = backend.snapshot()
    Preflight(catalog, backend).simulate([t.id for t in catalog.all()])
    assert backend.snapshot() == before


def test_apply_order_is_deterministic(catalog, backend):
    plan = Preflight(catalog, backend).simulate(["display.set_refresh", "startup.defer.onedrive"])
    ordered = Preflight.order_effects(plan)
    kinds = [e.kind for e in ordered]
    assert kinds == sorted(kinds, key=lambda k: {"startup_defer": 4, "display_mode": 5}.get(k, 99))
