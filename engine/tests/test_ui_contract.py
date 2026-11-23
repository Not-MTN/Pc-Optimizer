"""Contract test: every URL the console calls must actually exist on the server.

This exists because the console cannot be tested in a browser here. A typo in a
fetch path is invisible to Python tests, invisible to JS syntax checks, and only
shows up as a dead button in the preview — which is exactly how
`/api/autopsy/erase` shipped unrouted.

The test parses the served HTML, extracts every `/api/...` literal, and asserts
the router recognises it. It also walks the reverse direction: every route the
server exposes should be reachable from the UI or be a documented internal.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from pco_engine import server as srv

CONSOLE = Path(__file__).resolve().parent.parent / "pco_engine" / "console.html"

# Endpoints deliberately not called by this console build (used by tests, the
# future WinUI shell, or the CLI). Anything else being un-routed is a bug.
INTERNAL_OK = {
    "/api/health", "/api/catalog", "/api/feed", "/api/verify",
    "/api/dns/toggle", "/api/games", "/api/checkup/fix",
    "/api/sens/save", "/api/game/profile/save", "/api/studio/save",
    "/api/palette/all", "/api/report/json",
}


def _console_text() -> str:
    return CONSOLE.read_text(encoding="utf-8")


def _client_calls() -> set[str]:
    """Paths that are actually passed to api(), including dynamic ones."""
    text = _console_text()
    calls = set(re.findall(r'api\(\s*"([^"]+)"', text))
    # a trailing slash means the path is completed at runtime (e.g. dns actions)
    calls = {c for c in calls if c.startswith("/api/") and not c.endswith("/")}
    # ...so count those dynamic targets explicitly
    calls |= {f"/api/dns/{a}" for a in ("on", "off", "pause", "toggle")}
    return calls


def _client_paths() -> set[str]:
    """Every /api/... string literal in the console, query strings stripped."""
    text = _console_text()
    found = set(re.findall(r"""["'](/api/[A-Za-z0-9_\-/.]*)["']""", text))
    found |= set(re.findall(r"""["'](/api/[A-Za-z0-9_\-/.]*)\?""", text))
    # "/api/dns/" is a prefix for string concatenation, not an endpoint
    return {p.split("?")[0] for p in found if not p.endswith("/")}


def _route_matches(path: str, routes: set[str]) -> bool:
    """Exact route, or a routed prefix (the report endpoint is opened in a new tab
    as `/api/report/html`, which the router handles by startswith)."""
    if path in routes:
        return True
    return any(path.startswith(r) for r in routes if r.count("/") >= 3)


def _server_routes() -> set[str]:
    text = (Path(srv.__file__)).read_text(encoding="utf-8")
    table = set(re.findall(r'"(/api/[A-Za-z0-9_\-/]*)"\s*:', text))
    branches = set(re.findall(r'path == "(/api/[A-Za-z0-9_\-/]*)"', text))
    # routes served by a prefix branch, declared in the ROUTES table as well
    return table | branches


def test_no_client_path_is_missing_from_the_router():
    routes = _server_routes()
    missing = sorted(p for p in _client_paths() if not _route_matches(p, routes))
    assert not missing, (
        "the console calls endpoints the server does not route — these are dead "
        f"buttons in the browser: {missing}")


def test_router_has_no_orphan_routes():
    """The other direction: a route nobody can reach is dead code or a typo.

    Uses literal api() call sites, so a call built by concatenation
    ('/api/dns/' + action) is covered by `test_dynamic_dns_paths_resolve` instead
    of being counted as unreachable here.
    """
    called = _client_calls() | _client_paths()
    orphans = sorted(r for r in _server_routes()
                     if r not in INTERNAL_OK
                     and not any(c.startswith(r) or r.startswith(c) for c in called))
    assert not orphans, f"routes exposed but never called by the console: {orphans}"


def test_dynamic_dns_paths_resolve():
    """The UI builds '/api/dns/' + action. Every action it can produce must exist."""
    text = _console_text()
    # what the DNS buttons pass in
    actions = set(re.findall(r'dns\("([a-z]+)"\)', text))
    actions |= set(re.findall(r'dns\([^)]*?"(on|off|pause)"', text))
    assert actions, "could not extract DNS actions from the console"
    routes = _server_routes()
    missing = [f"/api/dns/{a}" for a in actions if f"/api/dns/{a}" not in routes]
    assert not missing, f"DNS buttons point at unrouted endpoints: {missing}"


def test_every_element_the_script_touches_exists_in_the_markup():
    """A `$("#id")` with no matching element is a silent null dereference that
    kills the rest of that function — the classic way a view renders blank."""
    text = _console_text()
    markup, _, script = text.partition("<script>")
    ids_in_markup = set(re.findall(r'id="([^"]+)"', markup))
    touched = set(re.findall(r'\$\("#([A-Za-z0-9_\-]+)"\)', script))
    # ids created dynamically by the script itself
    created = set(re.findall(r'el\("[a-z0-9]+", "[^"]*", [^)]*\)\.id = "([^"]+)"', script))
    missing = sorted(touched - ids_in_markup - created)
    assert not missing, (
        f"the script reads elements that do not exist in the markup: {missing}")


def _groups(text: str) -> dict[str, list[str]]:
    block = re.search(r"const GROUPS = \{(.*?)\n\};", text, re.S)
    assert block, "could not find the GROUPS map"
    out = {}
    for name, views in re.findall(r"(\w+):\s*\[([^\]]*)\]", block.group(1)):
        out[name] = re.findall(r'"([a-z]+)"', views)
    return out


def test_every_view_button_has_a_section():
    """The four primary buttons must each open a view that exists."""
    text = _console_text()
    buttons = set(re.findall(r'class="nav primary" data-view="([a-z]+)"', text))
    sections = set(re.findall(r'id="view-([a-z]+)"', text))
    assert buttons, "could not find the primary nav buttons"
    assert buttons <= sections, f"nav buttons with no section: {sorted(buttons - sections)}"


def test_every_section_is_reachable():
    """A view you cannot reach is a feature you did not build.

    This is the inverse of the old assertion. The rail no longer shows every
    view — that was the problem — so the guarantee moves to reachability: the
    union of the four primary views and the More menu must cover every section,
    with nothing listed that does not exist.
    """
    text = _console_text()
    sections = set(re.findall(r'id="view-([a-z]+)"', text))
    primaries = set(re.findall(r'class="nav primary" data-view="([a-z]+)"', text))
    groups = _groups(text)
    listed = [v for views in groups.values() for v in views]

    assert sorted(listed) == sorted(set(listed)), "a view is listed twice in GROUPS"
    # A group's own screen is reached by its primary button, not from the menu.
    reachable = primaries | set(listed)
    assert not (sections - reachable), f"unreachable views: {sorted(sections - reachable)}"
    assert not (reachable - sections), f"nav names missing sections: {sorted(reachable - sections)}"


def test_the_rail_stays_small():
    """Four destinations, not fourteen.

    The whole product premise is that a settings app with a hundred buttons is
    a settings app nobody opens. This pins the number so it cannot creep back.
    """
    text = _console_text()
    primaries = re.findall(r'class="nav primary" data-view="([a-z]+)"', text)
    assert len(primaries) == 4, f"expected exactly 4 primary tabs, found {primaries}"
    assert len(_groups(text)) == 4, "GROUPS must define exactly four groups"


def test_every_view_has_a_title_entry():
    """Clicking a nav button whose view has no TITLES entry throws immediately."""
    text = _console_text()
    views = set(re.findall(r'id="view-([a-z]+)"', text)) | set(_groups(text))
    titles_block = re.search(r"const TITLES = \{(.*?)\};\n", text, re.S)
    assert titles_block, "could not find the TITLES map"
    titled = set(re.findall(r"([a-z]+):\s*\[", titles_block.group(1)))
    assert views <= titled, f"views with no title entry: {sorted(views - titled)}"


def test_console_is_self_contained():
    """The preview iframe has no network: any external fetch would render blank."""
    text = _console_text()
    externals = re.findall(r'(?:src|href)="(https?://[^"]+)"', text)
    assert not externals, f"console must not load external assets: {externals}"
    assert "<link" not in text or "rel=\"icon\"" in text
    assert not re.search(r"@import\s+url\(", text)


def test_console_never_uses_absolute_localhost_urls():
    """Relative URLs only — that is what makes it work behind the preview proxy."""
    text = _console_text()
    bad = re.findall(r'["\'](https?://(?:localhost|127\.0\.0\.1)[^"\']*)["\']', text)
    assert not bad, f"absolute URLs break behind the proxy: {bad}"


@pytest.mark.parametrize("handler", ["do_GET", "do_POST"])
def test_json_errors_never_leak_a_traceback_to_the_ui(handler):
    text = Path(srv.__file__).read_text(encoding="utf-8")
    fn = text.split(f"def {handler}", 1)[1].split("\n    def ", 1)[0]
    assert 'self._json(500' in fn, f"{handler} must answer 500 with JSON"
    assert "traceback.format_exc(limit=3)" in fn
