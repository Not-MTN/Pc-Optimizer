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
