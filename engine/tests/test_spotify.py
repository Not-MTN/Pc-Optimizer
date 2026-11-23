"""Spotify looks: a preview that always works, and an apply that tells the truth.

The design constraint this file protects is the one that is easy to lose:
**a preview may not disagree with the theme it claims to show, and an apply may
not pretend to have worked.**

The second half matters more than usual here. Spotify closed the seam Spicetify
depended on in 1.2.87, the maintainers said there was no ETA, and downgrading
stops playback — so "this will restyle your Spotify" is a promise we cannot make
on an arbitrary machine. What we can honestly do is render every look here, for
free, with no Spotify installed, and then write two files in a documented format
and leave the last step to the user.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pco_engine import spotify
from pco_engine.service import Service


@pytest.fixture()
def svc():
    d = tempfile.mkdtemp()
    return Service(f"{d}/t.db", catalog_dir="catalog")


# ------------------------------------------------------------------ the looks
def test_there_are_enough_looks_to_be_worth_browsing():
    assert len(spotify.LOOKS) >= 10


def test_look_ids_are_unique_and_match_their_keys():
    assert len(set(spotify.LOOKS)) == len(spotify.LOOKS)
    for key, look in spotify.LOOKS.items():
        assert look.id == key


def test_every_look_is_reachable_from_a_category():
    """An orphaned look is a look nobody sees."""
    categorised = {i for _, ids in spotify.CATEGORIES.values() for i in ids}
    assert categorised == set(spotify.LOOKS)


def test_a_look_named_stock_exists_and_is_visibly_plain():
    """The 'change nothing' option must always exist — same rule as the 0% GPU
    tier. Someone who wants Spotify to look like Spotify is not a failure case."""
    stock = spotify.LOOKS["stock"]
    assert stock.accent == "1db954"          # Spotify's own green
    assert stock.main == "121212"


def test_every_colour_is_a_valid_unprefixed_hex():
    """Spicetify's color.ini takes hex WITHOUT the leading #. Shipping '#1db954'
    produces a theme whose colours are silently ignored."""
    for look in spotify.LOOKS.values():
        for key, value in look.colors().items():
            assert isinstance(value, str) and len(value) == 6, f"{look.id}.{key}={value!r}"
            int(value, 16)   # raises if not hex


def test_the_colour_map_is_complete():
    """A missing key does not crash Spicetify — it falls back to a default, so
    the theme is almost right, which is harder to debug than broken."""
    required = {
        "text", "subtext", "nav-active-text", "main", "sidebar", "player",
        "card", "shadow", "main-secondary", "main-elevated", "highlight",
        "highlight-elevated", "selected-row", "button", "button-active",
        "button-secondary", "button-disabled", "nav-active", "tab-active",
        "notification", "notification-error", "play-button", "playback-bar",
        "misc",
    }
    for look in spotify.LOOKS.values():
        missing = required - set(look.colors())
        assert not missing, f"{look.id} is missing {sorted(missing)}"


def test_color_ini_parses_and_has_two_schemes():
    import configparser
    for look in spotify.LOOKS.values():
        cp = configparser.ConfigParser()
        cp.read_string(look.color_ini())
        assert look.scheme_name in cp
        assert "Keel Light" in cp
        assert cp[look.scheme_name]["main"] == look.main


def test_user_css_is_not_empty_and_declares_the_radius():
    for look in spotify.LOOKS.values():
        css = look.user_css()
        assert css.strip()
        assert f"{look.radius}px" in css
        assert css.count("{") == css.count("}")


def test_glass_looks_add_blur_and_solid_looks_do_not():
    assert "backdrop-filter" in spotify.LOOKS["glass"].user_css()
    assert "backdrop-filter" not in spotify.LOOKS["acid"].user_css()


# -------------------------------------------------------------------- preview
def test_every_look_renders_to_a_real_png():
    for look in spotify.LOOKS.values():
        png = spotify.render_preview(look, 320, 205).png()
        assert png[:8] == b"\x89PNG\r\n\x1a\n", f"{look.id} did not render a PNG"
        assert len(png) > 1000


def test_two_different_looks_render_different_images():
    """If the preview ignored the palette, this is what would catch it."""
    a = spotify.render_preview(spotify.LOOKS["glass"], 320, 205).png()
    b = spotify.render_preview(spotify.LOOKS["acid"], 320, 205).png()
    assert a != b


def test_the_preview_uses_the_looks_own_background_and_sidebar():
    """The left column is the sidebar colour, the middle is the main colour.
    Sampling (2,2) would test the sidebar and call it the background — which is
    exactly what the first version of this test did."""
    c = spotify.render_preview(spotify.LOOKS["arctic"], 200, 128)
    # x=2 is inside the sidebar
    assert c.px(2, 2)[:3] == (0x07, 0x0b, 0x11)
    # a point between the top bar and the player bar, right of the sidebar
    mid = c.px(int(200 * 0.95), int(128 * 0.5))
    assert mid[:3] != (0x07, 0x0b, 0x11)


def test_a_preview_can_be_any_size():
    for w, h in ((240, 153), (720, 460), (1280, 818)):
        assert spotify.render_preview(spotify.LOOKS["mint"], w, h).w == w


# --------------------------------------------------------------------- status
def test_status_never_claims_it_can_apply_when_spicetify_is_absent(monkeypatch):
    monkeypatch.setattr(spotify.shutil, "which", lambda _: None)
    st = spotify.detect()
    assert st.spicetify is False
    assert st.can_apply is False
    assert any("Spicetify is not installed" in r for r in st.reasons)


def test_status_always_explains_the_spotify_version_problem():
    """The user is owed this even when everything is installed, because it is the
    reason a `spicetify apply` may do nothing at all."""
    st = spotify.detect()
    assert any("1.2.87" in r for r in st.reasons)


def test_status_does_not_guess_support():
    st = spotify.detect()
    assert st.supported in (None, True, False)


# ---------------------------------------------------------------------- write
def test_write_creates_both_files_in_spicetify_layout():
    d = tempfile.mkdtemp()
    out = spotify.write_theme(spotify.LOOKS["rose"], themes_dir=Path(d))
    assert out["ok"] and out["wrote"]
    folder = Path(out["path"])
    assert folder.name == "Keel Rose"
    assert (folder / "color.ini").exists()
    assert (folder / "user.css").exists()


def test_write_never_overwrites_someone_elses_edits():
    d = tempfile.mkdtemp()
    spotify.write_theme(spotify.LOOKS["sand"], themes_dir=Path(d))
    again = spotify.write_theme(spotify.LOOKS["sand"], themes_dir=Path(d))
    assert again["ok"] is False and again["wrote"] is False
    assert "does not overwrite" in again["reason"]


def test_write_says_how_to_apply_and_warns_that_it_has_not():
    d = tempfile.mkdtemp()
    out = spotify.write_theme(spotify.LOOKS["deep"], themes_dir=Path(d))
    assert "spicetify config" in out["how_to_apply"]
    assert "Nothing was applied" in out["note"]


def test_write_lands_in_the_documented_appdata_location(monkeypatch):
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
    p = spotify.default_themes_dir()
    assert str(p).replace("\\", "/").endswith("spicetify/Themes")


def test_export_returns_both_files_as_text():
    out = spotify.export_bundle(spotify.LOOKS["mint"])
    assert set(out["files"]) == {"color.ini", "user.css"}
    assert "[Mint]" in out["files"]["color.ini"]


# -------------------------------------------------------------------- service
def test_the_service_exposes_the_whole_library(svc):
    out = svc.run_action("spotify.looks", {})
    assert out["count"] == len(spotify.LOOKS)
    assert len(out["groups"]) == len(spotify.CATEGORIES)
    assert out["status"] is not None


def test_a_colour_theme_is_zero_gpu_and_says_why(svc):
    out = svc.run_action("spotify.looks", {})
    assert out["cost_pct"] == 0.0
    assert "not an animation" in out["cost_note"]


def test_the_service_renders_previews(svc):
    png = svc.spotify_preview_png("glass", 360)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_unknown_look_raises_rather_than_returning_nothing(svc):
    """It must raise, and it must raise something the server maps to a 400.

    Not KeyError specifically: the service validates types itself now, so an
    unknown id and a malformed id take the same path and both raise ValueError.
    Pinning the exception class made this fail when the handling improved.
    """
    for call in (lambda: svc.spotify_preview_png("no-such-look"),
                 lambda: svc.run_action("spotify.export", {"look": "no-such-look"}),
                 lambda: svc.run_action("spotify.write", {"look": ["list"]})):
        with pytest.raises((KeyError, ValueError)) as exc:
            call()
        assert "look" in str(exc.value).lower()
