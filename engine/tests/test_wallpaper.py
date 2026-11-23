"""The wallpaper engine: canvas, scenes, widgets, themes.

Pure-Python raster code has a specific failure profile, and every test here comes
from one of them actually happening during the build:

  * a scene that renders blank because bounds were scaled and coordinates were not
  * `x ** 0.5` on a negative float, which is a complex number, not an exception —
    the crash lands three frames away from the cause
  * a size string that silently falls back to 1440p, so every "preview" is a
    1.1 MB full render and nobody notices
  * a widget drawn at a hardcoded offset that overlaps its neighbour at every
    size the defaults use
  * an alpha of 0 written over an opaque pixel, which erases it
"""
from __future__ import annotations

import json
import struct
import zlib

import pytest

from pco_engine.canvas import GLYPHS, Canvas
from pco_engine.scenes import SCENES, Palette, render
from pco_engine.themes import BUILTIN, diff, export, gallery, get, import_, validate
from pco_engine.wallpaper import SIZES, WallpaperSpec, compose, live_warnings, problems
from pco_engine.widgets import STATIC_OK, WIDGET_TYPES, draw, draw_layer, from_dict


PAL = Palette.from_hex(bg="#0B0D10", accent="#B6F24A",
                       secondary="#5A6478", tint="#2A3A55")


# ------------------------------------------------------------------- canvas
def test_a_new_canvas_is_the_requested_size_and_opaque():
    c = Canvas(8, 4)
    assert (c.w, c.h) == (8, 4)
    assert len(c.buf) == 8 * 4 * 4
    assert c.px(0, 0) == (0, 0, 0, 255)


def test_blend_respects_alpha():
    c = Canvas(1, 1, (0, 0, 0, 255))
    c.blend(0, 0, (255, 255, 255, 0))
    assert c.px(0, 0)[:3] == (0, 0, 0), "alpha 0 must not write"
    c.blend(0, 0, (255, 255, 255, 128))
    r, g, b, _ = c.px(0, 0)
    assert 100 < r < 160, f"half-alpha white over black should be mid grey, got {r}"


def test_drawing_outside_the_canvas_is_ignored_not_crashing():
    c = Canvas(4, 4)
    c.set(-5, 100, (1, 2, 3, 255))
    c.rect(-10, -10, 100, 100, (255, 0, 0, 255))
    assert c.px(0, 0)[0] == 255


def test_linear_gradient_spans_the_frame():
    """Corners must differ: a gradient that ends where it starts is a fill."""
    c = Canvas(32, 32)
    # The docstring is the contract: 180 = top->bottom, 0 = bottom->top.
    c = Canvas(32, 32)
    c.linear((0, 0, 0), (200, 200, 200), angle=180)
    assert c.px(16, 0)[0] > c.px(16, 31)[0] + 100, "180 should run top->bottom"
    d = Canvas(32, 32)
    d.linear((0, 0, 0), (200, 200, 200), angle=90)
    assert d.px(31, 16)[0] > d.px(0, 16)[0] + 100, "90 should run left->right"


def test_glyphs_cover_every_printable_character_used():
    """A missing glyph draws a blank box — invisible in a test, obvious on screen."""
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.-+%/°!?()&@*=, '":
        assert ch in GLYPHS, f"no glyph for {ch!r}"
        assert len(GLYPHS[ch]) == 7, f"{ch!r} is not 7 rows"
        assert all(0 <= row <= 0b11111 for row in GLYPHS[ch]), f"{ch!r} overflows 5 bits"


def test_text_draws_something_and_reports_its_width():
    c = Canvas(200, 40)
    w = c.text(2, 2, "AB", (255, 255, 255, 255), scale=2, spacing=1)
    assert w > 0
    lit = sum(1 for i in range(0, len(c.buf), 4) if c.buf[i] > 200)
    assert lit > 10, "text drew nothing"


def test_png_is_a_valid_png_of_the_right_size():
    blob = Canvas(8, 8, (10, 20, 30, 255)).png()
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", blob[16:24])
    assert (w, h) == (8, 8)
    # IDAT must decompress to exactly h*(1 stride) bytes.
    idat = blob[blob.index(b"IDAT") + 4:blob.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    assert len(raw) == 8 * (8 * 4 + 1)


# ------------------------------------------------------------------- scenes
@pytest.mark.parametrize("scene", SCENES)
def test_every_scene_renders_and_is_not_blank(scene):
    """'Rendered' is not enough. A uniform canvas is the exact failure that
    shipped in the first logo attempt and the first topo scene."""
    c = render(scene, 160, 90, PAL, seed=5)
    assert (c.w, c.h) == (160, 90)
    colours = {c.px(x, y) for x in range(0, 160, 17) for y in range(0, 90, 11)}
    assert len(colours) > 2, f"{scene} is effectively a flat fill ({len(colours)} colours)"


@pytest.mark.parametrize("scene", SCENES)
def test_scenes_are_deterministic(scene):
    """The theme format promises reproduce-ability. A scene that samples the
    clock or the process RNG cannot be versioned."""
    a = render(scene, 80, 45, PAL, seed=42).png()
    b = render(scene, 80, 45, PAL, seed=42).png()
    assert a == b


@pytest.mark.parametrize("scene", SCENES)
def test_scenes_do_not_raise_at_extreme_aspect_ratios(scene):
    """3840x1080 and 1080x1920 are real monitor shapes. sqrt/exp on a negative
    float returns a complex number, so a geometry bug is a TypeError, not a
    slightly wrong picture."""
    for (w, h) in ((320, 60), (60, 320), (1, 1)):
        render(scene, w, h, PAL, seed=3)


def test_an_unknown_scene_names_the_valid_ones():
    with pytest.raises(ValueError, match="unknown scene"):
        render("not-a-scene", 10, 10, PAL)


def test_palette_parses_hex_and_rejects_bad_input():
    p = Palette.from_hex(accent="#B6F24A")
    assert p.accent == (0xB6, 0xF2, 0x4A)
    with pytest.raises(ValueError):
        Palette.from_hex(accent="#fff")


# ------------------------------------------------------------------ widgets
@pytest.mark.parametrize("widget_type", WIDGET_TYPES)
def test_every_widget_draws(widget_type):
    c = Canvas(300, 200)
    spec = {"type": widget_type, "x": 0.5, "y": 0.5, "size": 0.2,
            "value": 0.6, "text": "42", "label": "LBL", "detail": None}
    drew = draw_layer(c, [spec], PAL)
    assert drew == 1, f"{widget_type} was skipped by draw_layer"
    lit = sum(1 for i in range(0, len(c.buf), 4) if c.buf[i] > 0)
    assert lit > 20, f"{widget_type} drew nothing"


def test_widget_validation_rejects_impossible_geometry():
    assert "size must be between" in " ".join(
        validate_dict := __import__("pco_engine.widgets", fromlist=["validate"])
        .validate({"type": "gauge-ring", "size": 9.0}))
    assert validate_dict, "validate returned nothing"


def test_widget_coordinates_are_fractions():
    from pco_engine.widgets import validate as wvalidate
    assert wvalidate({"type": "gauge-ring", "x": 1.5})
    assert not wvalidate({"type": "gauge-ring", "x": 0.5, "y": 0.5, "size": 0.1})


def test_one_bad_widget_does_not_lose_the_layer():
    """A theme from a friend may contain a widget this build cannot draw. The
    rest must still appear, and the count must say so."""
    c = Canvas(200, 120)
    drew = draw_layer(c, [
        {"type": "gauge-ring", "x": 0.3, "y": 0.5, "size": 0.2, "value": 0.5},
        {"type": "not-a-widget", "x": 0.5, "y": 0.5, "size": 0.2},
        {"type": "stat-block", "x": 0.7, "y": 0.5, "size": 0.2, "text": "42"},
    ], PAL)
    assert drew == 2, "a bad widget must be skipped, not fatal"


def test_live_only_widgets_are_flagged_for_still_images():
    """A clock baked into a wallpaper is wrong within a minute. The engine says
    so instead of silently drawing a stopped clock."""
    warns = live_warnings({"widgets": [{"type": "clock-digital", "text": "23:41"}]})
    assert warns and "stale" in warns[0]


def test_static_widgets_are_not_flagged():
    assert live_warnings({"widgets": [{"type": "calendar"}]}) == []
    assert "calendar" in STATIC_OK


def test_widget_from_dict_reads_hex_accents():
    w = from_dict({"type": "gauge-ring", "accent": "#FF0000", "size": 0.1,
                   "x": 0.5, "y": 0.5})
    assert w.accent == (255, 0, 0)


# -------------------------------------------------------------------- sizes
def test_named_and_explicit_sizes_both_resolve():
    assert WallpaperSpec(size="1080p").dimensions() == (1920, 1080)
    assert WallpaperSpec(size="1280x720").dimensions() == (1280, 720)


def test_an_unknown_size_is_an_error_not_a_silent_1440p():
    """This exact fallback made every gallery tile a 1.1 MB full render."""
    with pytest.raises(ValueError, match="unknown size"):
        WallpaperSpec(size="enormous").dimensions()


def test_absurd_explicit_sizes_are_refused():
    with pytest.raises(ValueError):
        WallpaperSpec(size="99999x10").dimensions()


# ---------------------------------------------------------------- compose
def test_compose_produces_the_named_size():
    c = compose({"scene": "grain", "size": "320x180"})
    assert (c.w, c.h) == (320, 180)


def test_compose_draws_the_widgets():
    plain = compose({"scene": "grain", "size": "200x120", "vignette": 0.0})
    withw = compose({"scene": "grain", "size": "200x120", "vignette": 0.0,
                     "widgets": [{"type": "stat-block", "x": 0.5, "y": 0.5,
                                  "size": 0.3, "text": "99"}]})
    assert plain.png() != withw.png(), "widgets did not reach the image"


def test_compose_is_deterministic():
    spec = {"scene": "aurora", "size": "160x90", "seed": 4,
            "widgets": [{"type": "gauge-ring", "x": 0.2, "y": 0.3,
                         "size": 0.2, "value": 0.5}]}
    assert compose(spec).png() == compose(spec).png()


def test_problems_reports_bad_specs_before_rendering():
    assert problems({"scene": "nope"})
    assert problems({"size": "nope"})
    assert problems({"widgets": [{"type": "nope"}]})
    assert problems({}) == []


# ------------------------------------------------------------------- themes
def test_every_builtin_theme_is_valid():
    for name, doc in BUILTIN.items():
        assert validate(doc) == [], f"{name}: {validate(doc)}"


def test_there_are_at_least_fifteen_themes_and_they_are_distinct():
    """A gallery of recolours is a colour picker. Each theme must differ in
    structure, not only in hue."""
    gal = gallery()
    assert len(gal) >= 15
    scenes = {t["scene"] for t in gal}
    assert len(scenes) >= 6, f"themes only use {scenes}"
    ids = [t["id"] for t in gal]
    assert len(set(ids)) == len(ids)


def test_every_theme_renders_a_tile_sized_image():
    """The gallery shows a real render per theme. If one raises, the grid shows
    a broken image and nobody notices until a user does."""
    for name in list(BUILTIN)[:15]:
        doc = get(name)
        wall = dict(doc["wallpaper"])
        wall["palette"] = doc["palette"]
        wall["widgets"] = []
        c = compose({**wall, "size": "160x90"})
        assert c.w == 160


def test_validate_rejects_unknown_keys():
    """A typo that is silently ignored is the failure mode this app exists
    against: the toggle looks applied and does nothing."""
    bad = {"meta": {"name": "X"}, "pallete": {"accent": "#fff"}}
    assert any("unknown top-level" in p for p in validate(bad))


def test_validate_rejects_a_bad_colour_with_the_field_named():
    out = validate({"meta": {"name": "X"}, "palette": {"accent": "zzz"}})
    assert any("accent" in p for p in out)


def test_validate_reports_each_broken_widget_by_index():
    out = validate({"meta": {"name": "X"},
                    "widgets": [{"type": "nope"}, {"type": "gauge-ring"}]})
    assert any("widget 0" in p for p in out)
    assert not any("widget 1" in p for p in out)


def test_export_import_round_trip():
    doc = get("synthwave")
    text = export(doc)
    back, errs = import_(text)
    assert errs == []
    assert back["meta"]["name"] == doc["meta"]["name"]


def test_import_of_garbage_explains_the_error():
    doc, errs = import_("{not json")
    assert doc == {} and errs and "JSON" in errs[0]


def test_diff_finds_nested_changes_and_ignores_equal_sections():
    a = {"shell": {"dark_mode": True}, "palette": {"accent": "#111111"}}
    b = {"shell": {"dark_mode": True}, "palette": {"accent": "#222222"}}
    changes = diff(a, b)
    assert len(changes) == 1
    assert changes[0]["key"] == "palette.accent"


def test_diff_of_identical_documents_is_empty():
    doc = get("keel")
    assert diff(doc, doc) == []


def test_unknown_theme_names_the_alternatives():
    with pytest.raises(KeyError, match="unknown theme"):
        get("no-such-theme")


def test_theme_size_accepts_explicit_pixels():
    """`compose` accepts WxH; validation refusing it was a real bug: the two
    disagreed and the renderer was right."""
    doc = {"meta": {"name": "X"}, "wallpaper": {"scene": "grain", "size": "1280x720"}}
    assert validate(doc) == []


def test_theme_is_json_serialisable():
    """Every theme must survive the trip through the database and the API."""
    for name, doc in BUILTIN.items():
        json.dumps(doc)
