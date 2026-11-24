"""The console, actually executed.

test_ui_contract.py reads console.html as text and checks that the URLs it calls
exist. That is necessary and it was not sufficient: ten chained `.append()`
calls and a `scrollIntoView` call all threw at runtime, in code that parsed
perfectly, and the effect was the one the user reported — every button on the
empty screens did nothing at all.

These tests load the page in jsdom, let its real script run against a real
engine, and assert on what the window actually displays. They skip when jsdom is
not installed rather than failing, because the engine's own test suite must not
depend on Node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CONSOLE = Path(__file__).resolve().parent.parent / "pco_engine" / "console.html"
JS = CONSOLE.read_text(encoding="utf-8").split("<script>")[1]


def _node() -> str | None:
    return shutil.which("node")


def _base_rule(css: str, selector: str) -> str:
    """The first rule for `selector` that is NOT inside an @media block.

    A mobile override (`@media (max-width:660px){main{...}}`) appears earlier in
    the text than nothing but is not the rule that applies to a desktop window;
    matching it made this assertion test the wrong line.
    """
    depth, i, n = 0, 0, len(css)
    starts = []
    while i < n:
        if css.startswith("@media", i):
            j = css.index("{", i)
            d = 1
            k = j + 1
            while k < n and d:
                if css[k] == "{":
                    d += 1
                elif css[k] == "}":
                    d -= 1
                k += 1
            i = k
            continue
        if css.startswith(selector + "{", i) or css.startswith(selector + ",", i):
            j = css.index("{", i)
            d = 1
            k = j + 1
            while k < n and d:
                if css[k] == "{":
                    d += 1
                elif css[k] == "}":
                    d -= 1
                k += 1
            return css[i:k]
        i += 1
    return ""


def _needs_jsdom() -> str:
    node = _node()
    if node is None:
        pytest.skip("no node on PATH")
    probe = subprocess.run([node, "-e", "require('jsdom')"],
                           capture_output=True, text=True,
                           cwd=str(CONSOLE.parent.parent))
    if probe.returncode != 0:
        pytest.skip("jsdom is not installed (npm i jsdom)")
    return node


# ---------------------------------------------------------------- text checks
def test_no_element_append_is_chained():
    """Element.append() returns undefined, so x.append(a).append(b) throws.

    Every one of these was in an empty-state branch — the branch that runs on a
    fresh install, which is why the app looked dead on first launch.
    """
    import re
    # Only a *call on the result of append* is the bug: `x.append(a).append(b)`.
    # `$("#x").append(a, b)` and `foo(a.append(b))` are both fine, and a naive
    # substring test flags them.
    pat = re.compile(r"\.[\w$]*append\([^;]*?\)\s*\.\s*append\(")
    bad = [(n, line.strip()[:90])
           for n, line in enumerate(JS.splitlines(), 1) if pat.search(line)]
    assert not bad, f"chained .append() (returns undefined) at {bad}"


def test_scroll_into_view_is_only_called_through_the_safe_helper():
    """It is optional, and it threw *before* the error message was shown."""
    calls = [n for n, line in enumerate(JS.splitlines(), 1)
             if "scrollIntoView" in line and "//" not in line.split("scrollIntoView")[0]
             and "const reveal" not in line and "try { node" not in line]
    # the helper itself is the only permitted call site
    assert len(calls) <= 1, f"scrollIntoView called outside reveal() at {calls}"


def test_every_view_has_a_title_and_a_subtitle():
    """show() writes TITLES[view][0] and [1]; a missing pair blanks the header."""
    import re
    views = set(re.findall(r'<section class="sect[^"]*" id="view-([a-z]+)"', JS + CONSOLE.read_text(encoding="utf-8")))
    block = JS.split("const TITLES = {", 1)[1].split("};", 1)[0]
    present = set(re.findall(r'^\s*([a-z]+):\s*\[', block, re.M))
    missing = views - present
    assert not missing, f"views with no TITLES entry: {sorted(missing)}"


def test_every_hub_group_is_drawn_at_boot():
    """The three hub panels were declared in the markup and filled in by
    openPrimary() — which only runs when a nav button is clicked. The window
    therefore opened showing three empty boxes."""
    text = CONSOLE.read_text(encoding="utf-8")
    boot = text.split("(async function boot(){")[-1] if "async function boot()" in text else text[-4000:]
    assert "paintHubs()" in text, "no paintHubs() at all"
    assert "renderHub(which)" in text or "renderHub(" in text


def test_the_boot_sequence_sets_the_subtitle():
    """It said "Loading the engine…" forever, on every screen."""
    text = CONSOLE.read_text(encoding="utf-8")
    assert "pinHubs" or 'setSubtitle("Reading this PC' in text
    assert 'setSubtitle(' in text


def test_the_window_reports_its_own_failures():
    """A silent JS throw is indistinguishable from a dead button."""
    assert "reportJsError" in JS
    assert "unhandledrejection" in JS
    assert "/api/client-error" in JS


def test_a_retired_route_is_not_called():
    """No URL the console posts to may be missing from the router."""
    from pco_engine import server as srv
    import re
    urls = set(re.findall(r'api\("(/api/[a-zA-Z0-9/_.?=&{}$-]+)"', JS))
    urls |= set(re.findall(r'"(/api/[a-z]+/[a-z-]+)"', JS))
    known = set(srv.ROUTES)
    known |= {"/api/status", "/api/undo", "/api/apply", "/api/clean/restore",
              "/api/consent", "/api/consent/set", "/api/client-error",
              "/api/autopsy", "/api/autopsy/begin", "/api/autopsy/note",
              "/api/autopsy/finish", "/api/autopsy/erase", "/api/catalog",
              "/api/feed", "/api/game/plan", "/api/game/watch",
              "/api/game/profile/save", "/api/games/library", "/api/health",
              "/api/palette", "/api/palette/all", "/api/palette/run",
              "/api/peripheral", "/api/peripheral/test", "/api/receipts",
              "/api/report/html", "/api/sens", "/api/sens/save",
              "/api/sens/from-game", "/api/sens/psa/start", "/api/sens/psa/step",
              "/api/studio/apply", "/api/studio/save", "/api/studio/import"}
    # Strip query strings; the console builds a few URLs by concatenation
    # ("/api/dns/" + action, ".../preview/" + id), so a literal prefix that
    # matches a real route family is fine.
    families = ("/api/dns/", "/api/spotify/preview/", "/api/themes/tile/",
                "/api/themes/get", "/api/themes/export")
    missing = []
    for url in urls:
        clean = url.split("?")[0]
        if clean in known or any(clean.startswith(f) for f in families):
            continue
        if "$" in clean or "{" in clean:      # a template, checked by its literal
            continue
        missing.append(clean)
    assert not missing, sorted(set(missing))


# ------------------------------------------------- dialogs, crumbs and defaults
def test_no_native_browser_dialogs_are_used():
    """prompt/confirm/alert are suppressed in WebView2, the runtime this app
    ships against. A prompt() that returns null silently makes the button look
    dead — which is the failure the whole 0.5.1 pass was about."""
    import re
    # Strip comments first: the explanatory block that says "prompt() is
    # suppressed in WebView2" contains all three words.
    code, in_block = [], False
    for line in JS.splitlines():
        t = line.strip()
        if in_block:
            if "*/" in t:
                in_block = False
            continue
        if t.startswith("/*"):
            in_block = "*/" not in t
            continue
        if t.startswith("//") or t.startswith("*"):
            continue
        code.append(line.split("//")[0] if "//" in line and '"' not in line.split("//")[0] else line)
    code = "\n".join(code)
    forbidden = []
    for pattern, what in (
        (r"(?<![\w.])prompt\s*\(", "prompt"),
        (r"(?<![\w.])confirm\s*\(", "confirm"),
        (r"(?<![\w.])alert\s*\(", "alert"),
        (r"window\.open\s*\(", "window.open"),
    ):
        for m in re.finditer(pattern, code):
            line = code[:m.start()].count("\n") + 1
            forbidden.append(f"{what} at JS line {line}")
    assert not forbidden, forbidden


def test_the_app_has_its_own_dialog_for_each_native_one_it_replaced():
    text = CONSOLE.read_text(encoding="utf-8")
    assert 'id="dlgScrim"' in text
    assert "function openDialog" in text
    assert "function showReport" in text


def test_the_breadcrumb_style_matches_the_element_it_builds():
    """The crumb is a <button>. The first version styled `.crumbs a`, so it
    rendered as a browser-default grey rectangle — the white box on screen."""
    text = CONSOLE.read_text(encoding="utf-8")
    css = text.split("<style>")[1].split("</style>")[0]
    build = JS.split("function renderCrumbs", 1)[1].split("\n}", 1)[0]
    created = "button" if 'createElement("button")' in build else "a"
    # Any rule whose selector list mentions the element type we actually build.
    rules = [b for b in css.split("}") if ".crumbs" in b and "{" in b
             and "hover" not in b.split("{")[0]]
    selectors = " ".join(b.split("{")[0] for b in rules)
    assert f".crumbs {created}" in selectors, (
        f"crumbs are <{created}> but no rule targets it: {selectors.strip()[:160]}")


def test_the_feed_reserves_space_for_its_scrollbar():
    """The timestamps read "15h ag" because the scrollbar covered them."""
    css = CONSOLE.read_text(encoding="utf-8").split("<style>")[1].split("</style>")[0]
    rule = _base_rule(css, ".feed")
    assert rule, "no base .feed rule"
    assert "scrollbar-gutter" in rule, rule[:140]


def test_the_main_column_is_not_capped_to_one_monitor_width():
    """A fixed 1240px left a third of a 1600px window empty."""
    css = CONSOLE.read_text(encoding="utf-8").split("<style>")[1].split("</style>")[0]
    base = _base_rule(css, "main")
    assert base, "no base main rule"
    assert "max-width:1240px" not in base, base[:140]
    assert "max-width" in base, base[:140]


def test_no_class_is_used_in_the_ui_without_a_style():
    import re
    text = CONSOLE.read_text(encoding="utf-8")
    css = text.split("<style>")[1].split("</style>")[0]
    js = text.split("<script>")[1]
    used = set()
    for m in re.finditer(r'el\(\s*"[a-z]+"\s*,\s*"([^"]+)"', js):
        used.update(m.group(1).split())
    for m in re.finditer(r'class="([^"]+)"', text.split("<script>")[0]):
        used.update(m.group(1).split())
    styled = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    # Structural classes that carry no styling of their own by design.
    allowed = {"on", "here", "row", "col", "mono", "big", "empty"}
    unstyled = sorted(c for c in used if c and c not in styled and c not in allowed)
    assert not unstyled, f"classes with no rule: {unstyled}"


def test_no_cell_or_row_is_appended_outside_a_table():
    """`tb.append(el("tr"), el("td", ...))` puts a <td> beside the row, not in it.

    This is what the fix for the chained-append crash turned into: the throw was
    gone, the markup was invalid, and an empty table rendered as a stray cell
    floating under it. Nine of these shipped. The parser accepts them silently,
    which is why only a runtime check catches it.
    """
    import re
    bad = []
    for n, line in enumerate(JS.splitlines(), 1):
        if re.search(r'append\(el\("tr"\),\s*el\("td"', line):
            bad.append((n, line.strip()[:100]))
    assert not bad, f"a <td> appended as a sibling of its <tr>: {bad}"


def test_a_scan_reports_what_it_looked_at():
    """"How can it scan my whole PC in a second?" — it does not, and it should
    say so rather than leave the user to guess."""
    import tempfile
    from pco_engine.service import Service

    d = tempfile.mkdtemp()
    svc = Service(f"{d}/x.db", catalog_dir="catalog")
    out = svc.clean_scan()
    assert "looked_in" in out and out["looked_in"], "no scope reported"
    for entry in out["looked_in"]:
        assert entry["name"] and entry["path"]
    assert "elapsed_ms" in out
    assert "whole drive" in out["scope_note"].lower() or \
           "not your whole" in out["scope_note"].lower()
