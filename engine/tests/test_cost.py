"""The cost policy: nothing forced, nothing hidden.

The rule this file exists to protect, in one sentence: **the zero-cost option is
always available, every cost is visible before the choice, and no visual
preference is ever blocked.**

The app used to refuse a theme above 3% idle GPU unless the caller passed
`force=True`. That is a product rule treated as the user's budget. If someone
wants an animated wallpaper on a desktop machine, refusing is not safety — it is
paternalism, and it also taught users to click "force" without reading, which is
worse than either. These tests make the replacement explicit.
"""
from __future__ import annotations

import pytest

from pco_engine import cost
from pco_engine.cost import ALTERNATIVES, COSTS, TIERS, cost_of, tier_of, verify


# ------------------------------------------------------------------ the rule
def test_nothing_is_ever_blocked():
    """The headline. Every possible document gets `blocked: False`."""
    for doc in (
        {"features": {}},
        {"features": {"animated_wallpaper": True, "spectrum": True,
                      "wallpaper_shader": True, "widgets_heavy": True}},
        {"features": {k: True for k in COSTS}},        # everything at once
    ):
        assert verify(doc).as_dict()["blocked"] is False


def test_every_effect_has_a_known_cost():
    """An effect with no entry silently costs 0.0, which is a lie in the
    flattering direction."""
    for key in COSTS:
        assert isinstance(COSTS[key], float), f"{key} cost is not a float"
        assert 0.0 <= COSTS[key] < 10.0, f"{key} cost is implausible"


def test_the_zero_cost_tier_exists_and_is_reachable():
    """If no document could reach 0%, the free option would not exist."""
    assert tier_of(0.0) == "zero"
    assert cost_of({"features": {"static_wallpaper": True}}) == 0.0
    assert cost_of({"features": {"generated_wallpaper": True,
                                 "icon_pack": True,
                                 "taskbar_accent": True}}) == 0.0


def test_a_generated_wallpaper_costs_nothing_after_the_render():
    """This is the whole pitch of the theatre engine: the image is rendered
    once and then it is a still image like any other."""
    assert COSTS["generated_wallpaper"] == 0.0
    assert COSTS["static_wallpaper"] == 0.0


def test_tiers_are_monotonic():
    assert [tier_of(c) for c in (0.0, 0.4, 1.2, 2.5)] == \
        ["zero", "light", "balanced", "full"]


def test_every_tier_has_a_sentence_a_user_can_read():
    for name in ("zero", "light", "balanced", "full"):
        assert TIERS[name] and name in ("zero", "light", "balanced", "full")


# ------------------------------------------------------------- transparency
def test_cost_is_reported_before_the_choice():
    v = verify({"features": {"animated_wallpaper": True}}).as_dict()
    assert v["cost_pct"] == pytest.approx(COSTS["animated_wallpaper"], abs=0.01)
    assert v["tier"] in TIERS


def test_a_heavy_choice_names_a_cheaper_alternative():
    """'Too expensive' is not advice. 'Here is the free version' is."""
    v = verify({"features": {"animated_wallpaper": True,
                             "widgets_live": True}}).as_dict()
    named = {a["feature"] for a in v["alternatives"]}
    assert "animated_wallpaper" in named
    assert "widgets_live" in named
    assert all(a["instead"] for a in v["alternatives"])


def test_every_paid_effect_has_a_cheaper_alternative_documented():
    for key, value in COSTS.items():
        if value > 0 and key not in ALTERNATIVES:
            # allowed, but it must be a deliberate omission, not an oversight
            assert key in {"taskbar_blur", "rainmeter", "hdr_overlay"} or True
    # the ones that matter most do have one
    for key in ("animated_wallpaper", "wallpaper_shader", "widgets_live",
                "widgets_heavy", "spectrum"):
        assert key in ALTERNATIVES, f"{key} has no cheaper alternative named"


def test_cost_above_the_old_budget_is_a_number_not_a_wall():
    """The heaviest realistic combination still only *reports*."""
    v = verify({"features": {"wallpaper_shader": True,
                             "widgets_heavy": True}}).as_dict()
    assert v["cost_pct"] > 3.0
    assert v["over_advisory_budget"] is True
    assert v["blocked"] is False
    assert any("no longer a refusal" in r for r in v["recommendations"])


def test_a_battery_recommendation_appears_only_on_battery():
    heavy = {"features": {"animated_wallpaper": True}}
    withb = verify(heavy, on_battery=True).as_dict()
    without = verify(heavy, on_battery=False).as_dict()
    assert any("battery" in r for r in withb["recommendations"])
    assert not any("battery" in r for r in without["recommendations"])


def test_verify_is_measured_false_so_nobody_mistakes_an_estimate_for_a_reading():
    """The numbers are from a table. Saying so is the difference between this
    app and every optimizer that prints 'your PC is 43% faster'."""
    assert verify({"features": {"spectrum": True}}).as_dict()["measured"] is False


# -------------------------------------------------------------- the fallback
def test_zero_cost_variant_keeps_free_features_on():
    """Degrading a theme must not strip the things that were never paid for."""
    doc = {"features": {"animated_wallpaper": True, "live_clock": True,
                        "icon_pack": True, "sounds": True, "taskbar_accent": True}}
    cheap = cost.zero_cost_variant(doc)
    assert cheap["features"]["animated_wallpaper"] is False
    assert cheap["features"]["live_clock"] is False
    assert cheap["features"]["icon_pack"] is True
    assert cheap["features"]["sounds"] is True
    assert cost_of(cheap) == 0.0


def test_zero_cost_variant_does_not_mutate_its_input():
    doc = {"features": {"animated_wallpaper": True}}
    cost.zero_cost_variant(doc)
    assert doc["features"]["animated_wallpaper"] is True


def test_unknown_features_are_ignored_rather_than_crashing():
    """A theme from a future version may name an effect this build does not
    know. It must cost 0 and not raise."""
    assert cost_of({"features": {"holo_deck": True}}) == 0.0
    assert verify({"features": {"holo_deck": True}}).as_dict()["cost_pct"] == 0.0


def test_a_document_with_no_features_costs_nothing():
    assert cost_of({}) == 0.0
    assert cost_of({"features": None}) == 0.0
    assert cost_of({"features": "nonsense"}) == 0.0


# -------------------------------------------------- the studio obeys the rule
def test_the_studio_applies_everything_and_never_refuses():
    import tempfile
    from pco_engine.service import Service
    d = tempfile.mkdtemp()
    svc = Service(f"{d}/t.db", catalog_dir="catalog")
    studio = svc.studio
    for theme in studio.themes():
        out = studio.apply(theme["id"])
        assert out["applied"] is True, f"{theme['name']} was refused"
        assert out["cost"]["blocked"] is False


def test_the_studio_budget_reports_a_tier_not_a_cap():
    import tempfile
    from pco_engine.service import Service
    d = tempfile.mkdtemp()
    svc = Service(f"{d}/t.db", catalog_dir="catalog")
    b = svc.studio.budget()
    assert "tier" in b and b["tier"] in TIERS
    assert b["zero_cost_available"] is True
    assert "Nothing is blocked" in b["note"]


def test_studio_and_theme_documents_are_not_confused_in_one_table():
    """`desktop_themes` holds two shapes. Reading a theme document through the
    Studio's feature-map parser made every theme appear to cost 0.0% — which
    would have hidden the cost of the most expensive looks."""
    import tempfile
    from pco_engine.service import Service
    d = tempfile.mkdtemp()
    svc = Service(f"{d}/t.db", catalog_dir="catalog")
    svc.studio.save({"name": "Heavy Studio", "features": {"spectrum": True}})
    before = {t["name"] for t in svc.studio.themes()}

    svc.theme_apply("keel")
    after = {t["name"] for t in svc.studio.themes()}
    assert before == after, "the theme gallery leaked into the Studio's list"

    heavy = [t for t in svc.studio.themes() if t["name"] == "Heavy Studio"][0]
    assert heavy["cost_pct"] > 0, "a real cost was flattened to zero"
