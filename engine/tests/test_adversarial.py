"""Adversarial tests: try to break the HTTP surface.

Every test here was written by trying to break the app, not by describing what
it does. The rule they enforce is one line long:

    **bad input produces a 4xx that names the problem, never a 5xx and never a
    plausible-looking lie.**

That distinction matters more than it looks. A 500 is a crash that leaks a
traceback and tells the caller nothing actionable; a silent fallback is worse,
because the caller walks away believing something happened. Both are failures of
the same promise: this app tells you the truth about what it did.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
import urllib.error
import urllib.request

import pytest

from pco_engine import cost, themes
from pco_engine import server as srv
from pco_engine.service import Service


# ------------------------------------------------------------------- a server
@pytest.fixture(scope="module")
def base():
    """A real HTTP server on a real socket — the same path a browser takes.

    `srv._service` is a real function guarded by double-checked locking on
    `_SVC`, so replacing the *name* did nothing: the handler called the original
    and served the user's actual data directory. Tests then wrote to real
    storage and asserted against the wrong instance, which looked exactly like a
    product bug. The fixture patches the cache instead, and restores it after.
    """
    import os
    import tempfile
    from pathlib import Path

    tmp = tempfile.mkdtemp()
    old_svc = srv._SVC
    old_env = os.environ.get("PCO_DATA")
    os.environ["PCO_DATA"] = tmp
    srv._SVC = Service(str(Path(tmp) / "adv.db"), catalog_dir="catalog")

    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        srv._SVC = old_svc
        if old_env is None:
            os.environ.pop("PCO_DATA", None)
        else:
            os.environ["PCO_DATA"] = old_env


def post(base, path, body=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data,
                                headers={"content-type": "application/json"},
                                method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ------------------------------------------------- every route, junk bodies
ROUTES = sorted(set(srv.ROUTES))


@pytest.mark.parametrize("body", [
    {}, {"x": 1}, [], "string", 123, None,
    {"theme_id": ["a"]}, {"look": {"a": 1}}, {"doc": 7},
])
def test_no_route_ever_returns_500_for_junk(base, body):
    """The broadest net: whatever you post anywhere, it is not a crash."""
    failures = []
    for path in ROUTES:
        if path in {"/api/safety/confirm"}:     # deliberately writes nothing
            continue
        status, raw = post(base, path, body)
        if status >= 500:
            failures.append(f"{path} -> {status}: {raw[:120]!r}")
    assert not failures, "500s on junk input:\n" + "\n".join(failures)


def test_a_non_json_body_is_a_400_with_a_reason(base):
    status, raw = post(base, "/api/spotify/write", raw=b"<html>nope</html>")
    assert status == 400
    assert b"not JSON" in raw


def test_a_json_scalar_body_is_rejected_cleanly(base):
    """`[]`, `"x"` and `1` are valid JSON and are not objects. Iterating one used
    to be the difference between a 400 and a traceback."""
    for raw in (b"[]", b'"scalar"', b"1", b"null"):
        status, out = post(base, "/api/spotify/export", raw=raw)
        assert 400 <= status < 500, f"{raw!r} -> {status}"


# --------------------------------------------------------- Spotify specifics
@pytest.mark.parametrize("look", [["glass"], {"a": 1}, 42, 1.5, True, None])
def test_a_look_that_is_not_a_string_is_a_400(base, look):
    status, raw = post(base, "/api/spotify/write", {"look": look})
    assert status == 400, f"{look!r} -> {status}"
    assert b"must be a string" in raw


@pytest.mark.parametrize("look", [
    "", "nope", "../../etc/passwd", "glass\x00", "🎵",
    "A" * 5000, "glass ", "GLASS",
])
def test_a_look_that_is_not_known_is_a_400_that_lists_the_real_ones(base, look):
    status, raw = post(base, "/api/spotify/export", {"look": look})
    assert status == 400
    # The contract is "refused with a reason", not one fixed sentence: an
    # over-long id is caught by the length guard before the look validator
    # sees it, which is a better error, not a worse one.
    body = raw.decode("utf-8", "replace").lower()
    assert "look" in body, body
    assert any(word in body for word in
               ("known looks are", "limit", "must be a string")), body


@pytest.mark.parametrize("directory,why", [
    ("spice", "relative"),
    ("/tmp/../etc", "traversal"),
    ("../../etc", "traversal"),
    ("/no/such/place/at/all", "does not exist"),
    ("   ", "empty"),
    ("/etc/passwd", "not a directory"),
    ("/tmp/" + "a" * 5000, "absurdly long"),
])
def test_a_bad_write_destination_is_refused_and_explained(base, directory, why):
    status, raw = post(base, "/api/spotify/write",
                       {"look": "glass", "dir": directory})
    assert status == 400, f"{directory[:40]!r} -> {status}"
    body = raw.decode("utf-8", "replace").lower()
    assert any(word in body for word in
               ("absolute", "..", "exist", "not a directory", "empty", "long",
                "limit")), body


def test_a_symlink_destination_is_followed_not_escaped(base, tmp_path):
    """A symlink is the user's own decision, but the resolved target is what we
    must report back, so nobody is surprised about where files landed."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    status, raw = post(base, "/api/spotify/write",
                       {"look": "glass", "dir": str(link)})
    assert status == 200
    out = json.loads(raw)
    assert out["wrote"] is True
    assert (real / "Keel Glass" / "color.ini").exists()


def test_a_write_reports_the_real_path_it_used(base, tmp_path):
    status, raw = post(base, "/api/spotify/write",
                       {"look": "frost", "dir": str(tmp_path)})
    assert status == 200
    out = json.loads(raw)
    assert out["path"].startswith(str(tmp_path))


# ------------------------------------------------------------- amplification
def test_an_error_does_not_echo_a_megabyte_back(base):
    """Rejecting a 1 MB id must not produce a 1 MB response. An error is a
    response; a response must not scale with the request."""
    big = "A" * 1_000_000
    status, raw = post(base, "/api/spotify/export", {"look": big})
    assert status == 400
    assert len(raw) < 4096, f"error response was {len(raw):,} bytes for a {len(big):,}-byte id"


def test_a_long_dir_is_rejected_before_it_reaches_the_filesystem(base):
    status, raw = post(base, "/api/spotify/write",
                       {"look": "glass", "dir": "/tmp/" + "x" * 100_000})
    assert status == 400
    assert len(raw) < 4096


# -------------------------------------------------------------- preview width
@pytest.mark.parametrize("w", ["abc", "", "-5", "0", "1", "2000", "1e9", " ",
                               "720.5", "NaN"])
def test_a_meaningless_preview_width_is_a_400_not_a_guess(base, w):
    status, raw = get(base, f"/api/spotify/preview/glass?w={w}")
    assert status == 400, f"w={w!r} -> {status}: {raw[:80]!r}"
    # The message differs by cause ("w must be a whole number" vs "w was given
    # but is empty"), and both are correct. Assert the field is named, not the
    # exact sentence — a test that pins wording fails on an improvement.
    body = raw.decode("utf-8", "replace")
    assert "w" in body and ("must" in body or "empty" in body), body


@pytest.mark.parametrize("w", [240, 440, 720, 1440])
def test_a_legal_width_renders(base, w):
    status, raw = get(base, f"/api/spotify/preview/glass?w={w}")
    assert status == 200
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_default_width_works_with_no_query_string(base):
    status, raw = get(base, "/api/spotify/preview/arctic")
    assert status == 200 and raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_unknown_look_preview_is_a_404_not_a_500(base):
    status, raw = get(base, "/api/spotify/preview/nope?w=400")
    assert status == 404


# ------------------------------------------------------------- method safety
@pytest.mark.parametrize("path", [
    "/api/spotify/write", "/api/themes/apply", "/api/workspace/capture",
    "/api/drivers/block", "/api/shell/apply", "/api/wallpaper/apply",
])
def test_a_state_change_never_answers_a_get(base, path):
    """Any page on the internet can point an <img> at localhost. A GET that
    changes state is a CSRF with extra steps."""
    status, _ = get(base, path + "?look=glass&theme_id=keel")
    assert status == 405, f"{path} answered a GET with {status}"


def test_an_unknown_path_is_a_404_not_a_crash(base):
    for path in ("/api/nope", "/api/", "/api/spotify", "/api/spotify/preview"):
        status, _ = get(base, path)
        assert status in (404, 400), f"{path} -> {status}"


# ---------------------------------------------------------------- the race
def test_concurrent_writes_produce_exactly_one_winner(tmp_path):
    """Two threads that both see "folder does not exist" must not both write.

    The check and the mkdir are two steps. With `exist_ok=True` both writers
    proceeded and interleaved, so a folder could end up holding one look's
    color.ini and another look's user.css — a half-applied theme that looks like
    a rendering bug and is impossible to explain. `mkdir(exist_ok=False)` makes
    the filesystem do the arbitration.
    """
    import threading

    from pco_engine import spotify

    results = []
    lock = threading.Lock()

    def write():
        r = spotify.write_theme(spotify.LOOKS["glass"], themes_dir=tmp_path)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=write) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r.get("wrote")]
    assert len(winners) == 1, f"{len(winners)} writers succeeded, expected 1"
    losers = [r for r in results if not r.get("wrote")]
    assert all("already exists" in r["reason"] for r in losers)


def test_a_written_theme_is_complete_and_parses(tmp_path):
    """The two files must agree with each other. A folder with one file and not
    the other is the failure mode the race above could have produced."""
    import configparser

    from pco_engine import spotify

    out = spotify.write_theme(spotify.LOOKS["mint"], themes_dir=tmp_path)
    folder = Path(out["path"])
    cp = configparser.ConfigParser()
    cp.read_string((folder / "color.ini").read_text(encoding="utf-8"))
    assert "Mint" in cp
    css = (folder / "user.css").read_text(encoding="utf-8")
    assert css.count("{") == css.count("}") and css.strip().endswith("}")


def test_a_write_that_fails_partway_does_not_look_finished(tmp_path, monkeypatch):
    """If the second file cannot be written, the result must say so — a folder
    containing only color.ini looks complete to `spicetify config` and then
    renders as nothing at all."""
    from pco_engine import spotify

    real_write = Path.write_text

    def half_fail(self, *a, **kw):
        if self.name == "user.css":
            raise OSError(28, "No space left on device")
        return real_write(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", half_fail)
    out = spotify.write_theme(spotify.LOOKS["rose"], themes_dir=tmp_path)
    assert out["ok"] is False and out["wrote"] is False
    assert "No space left" in out["reason"] or "could not" in out["reason"]


# ------------------------------------------------------------ store corruption
def test_a_corrupt_database_is_named_not_traced(tmp_path):
    """The power-loss case: a file that is present, non-empty, and not a
    database. SQLite reports it from inside the first pragma, which reaches the
    user as `DatabaseError: file is not a database` and no way forward."""
    from pco_engine.store import StoreUnusable

    db = tmp_path / "pco.db"
    db.write_bytes(b"definitely not sqlite" * 100)
    with pytest.raises(StoreUnusable) as exc:
        Service(str(db), catalog_dir="catalog")
    message = str(exc.value)
    assert str(db) in message          # names the file
    assert "rename" in message         # tells them what to do
    assert ".damaged" in message
    # and it must not have touched the file
    assert db.read_bytes().startswith(b"definitely not sqlite")


def test_a_truncated_sqlite_header_is_also_caught(tmp_path):
    from pco_engine.store import StoreUnusable

    db = tmp_path / "pco.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 40)
    with pytest.raises(StoreUnusable):
        Service(str(db), catalog_dir="catalog")


def test_an_empty_file_is_a_first_run_not_a_corruption(tmp_path):
    """Zero bytes is what SQLite creates for a new database. Treating that as
    damage would refuse to start on a perfectly good first launch."""
    db = tmp_path / "pco.db"
    db.write_bytes(b"")
    svc = Service(str(db), catalog_dir="catalog")
    assert svc.status()          # it opened and migrated


def test_a_real_database_still_opens(tmp_path):
    db = tmp_path / "pco.db"
    svc = Service(str(db), catalog_dir="catalog")
    svc.status()
    again = Service(str(db), catalog_dir="catalog")
    assert again.status()        # reopening an existing store works


# --------------------------------------------------------------- theme intake
def test_an_enormous_theme_name_is_refused():
    """A 100 KB name is not a theme, and it lands in the gallery and the title."""
    problems = themes.validate({"meta": {"name": "A" * 100_000}})
    assert any("limit is 80" in p for p in problems), problems


def test_a_theme_name_cannot_contain_control_characters():
    """A name with newlines can forge lines in anything that logs or tabs it."""
    problems = themes.validate({"meta": {"name": "Aurora\x00\x1b[31m"}})
    assert any("control characters" in p for p in problems), problems


def test_a_theme_with_ten_thousand_problems_reports_twelve():
    """Otherwise the dialog cannot show it and the real problem scrolls away."""
    problems = themes.validate({"meta": {"name": "x"}, "widgets": [{}] * 10_000})
    assert len(problems) == 13, len(problems)
    assert problems[-1].startswith("and "), problems[-1]
    assert "more problem" in problems[-1]


def test_a_good_theme_is_still_accepted():
    good = {"schema": themes.SCHEMA,
            "meta": {"name": "Aurora", "author": "me"},
            "palette": {"bg": "#101014", "accent": "#7aa2f7"}}
    assert themes.validate(good) == []


def test_zero_cost_variant_never_raises_on_a_non_document():
    """The cheap version of nothing is nothing, and its cost is still zero."""
    for junk in (None, [], "text", 5, [1, 2]):
        out = cost.zero_cost_variant(junk)
        assert isinstance(out, dict)
        assert cost.cost_of(out) == 0.0


# ------------------------------------------------- the routes ROUTES never had
# The 69-route fuzzer covered srv.ROUTES. Thirty-one endpoints are special-cased
# in do_POST *outside* that table, so they were never fuzzed — which is exactly
# where `{"run_id": []}` reached SQLite and came back as a 500, and where a bogus
# tx_id returned a full traceback with absolute paths to the browser.
SPECIAL_ROUTES = [
    "/api/apply", "/api/autopsy", "/api/autopsy/begin", "/api/autopsy/erase",
    "/api/autopsy/finish", "/api/autopsy/note", "/api/catalog",
    "/api/clean/restore", "/api/consent", "/api/consent/set", "/api/feed",
    "/api/game/plan", "/api/game/profile/save", "/api/game/watch",
    "/api/games/library", "/api/health", "/api/palette", "/api/palette/all",
    "/api/palette/run", "/api/peripheral", "/api/peripheral/test",
    "/api/receipts", "/api/report/html", "/api/sens", "/api/sens/psa/start",
    "/api/sens/psa/step", "/api/sens/save", "/api/studio/apply",
    "/api/studio/import", "/api/studio/save", "/api/undo",
]

JUNK = [
    {}, {"x": 1}, {"tx_id": ["a"]}, {"tx_id": {"a": 1}}, {"tx_id": None},
    {"run_id": []}, {"run_id": {}}, {"autopsy_id": []}, {"autopsy_id": {}},
    {"id": []}, {"doc": []}, {"cm360": [], "dpi": 800}, {"game": {}},
    {"theme_id": []}, {"verb": []}, {"kind": []}, {"state": [], "better": {}},
]


@pytest.mark.parametrize("route", SPECIAL_ROUTES)
def test_no_special_route_answers_5xx_to_junk(base, route):
    """Every special-cased endpoint, every shape of junk — none may 5xx."""
    offenders = []
    for body in JUNK:
        status, raw = post(base, route, body)
        if status >= 500:
            offenders.append((body, status, raw[:120]))
    assert not offenders, offenders


def test_a_bogus_tx_id_is_a_409_not_a_traceback(base):
    """This returned a full stack trace with absolute paths — a real leak."""
    status, raw = post(base, "/api/undo", {"tx_id": "nope"})
    assert status == 409, raw
    body = raw.decode("utf-8", "replace")
    assert "nothing journaled for nope" in body
    for leak in ("Traceback", "File \"", "pco_engine", "/home/"):
        assert leak not in body, f"{leak} leaked to the client: {body[:200]}"


def test_no_endpoint_puts_a_traceback_in_a_response(base):
    """Whatever goes wrong, the browser gets a sentence and a reference."""
    leaked = []
    for route in SPECIAL_ROUTES:
        for body in ({"x": 1}, {}):
            _, raw = post(base, route, body)
            text = raw.decode("utf-8", "replace")
            if "Traceback (most recent call last)" in text or 'File "' in text:
                leaked.append((route, text[:120]))
    assert not leaked, leaked


@pytest.mark.parametrize("route,body,key", [
    ("/api/sens/save", {"cm360": "abc", "dpi": 800}, "cm360"),
    ("/api/sens/save", {"cm360": None, "dpi": 800}, "cm360"),
    ("/api/sens/save", {"cm360": [], "dpi": 800}, "cm360"),
    ("/api/sens/save", {"cm360": {}, "dpi": 800}, "cm360"),
    ("/api/sens/save", {"cm360": 1e999, "dpi": 800}, "cm360"),
    ("/api/sens/save", {"cm360": 30.0, "dpi": "800"}, "dpi"),
    ("/api/sens/save", {"cm360": 30.0, "dpi": []}, "dpi"),
])
def test_numeric_fields_reject_non_numbers_with_a_reason(base, route, body, key):
    """float([]) and int("x") used to raise from inside the handler — a 500."""
    status, raw = post(base, route, body)
    assert status == 400, f"{key}={body[key]!r} -> {status}: {raw[:100]}"
    assert key.encode() in raw, f"the message must name {key}: {raw[:120]}"


def test_a_fractional_dpi_is_refused_rather_than_truncated(base):
    """Truncating 800.5 to 800 would quietly change what the user asked for."""
    status, raw = post(base, "/api/sens/save", {"cm360": 30.0, "dpi": 800.5})
    assert status == 400
    assert b"whole number" in raw


def test_an_identifier_that_is_not_a_scalar_is_refused(base):
    for route, key in (("/api/undo", "tx_id"), ("/api/clean/restore", "run_id"),
                       ("/api/autopsy/finish", "autopsy_id"),
                       ("/api/studio/apply", "theme_id")):
        for value in ([], {}, ["a"], {"a": 1}):
            status, raw = post(base, route, {key: value})
            assert status == 400, f"{route} {key}={value!r} -> {status}"
            assert key.encode() in raw, raw[:120]


# ------------------------------------------------- nothing may change silently
def test_a_setting_that_is_already_correct_says_so():
    """Choosing "transparent taskbar" on a machine that already had it reported
    zero changes and no reason — indistinguishable from a dead button."""
    import tempfile
    from pco_engine.service import Service

    d = tempfile.mkdtemp()
    svc = Service(f"{d}/x.db", catalog_dir="catalog")
    svc.shell_apply({"taskbar.transparency": "on"})
    again = svc.shell_apply({"taskbar.transparency": "on"})
    assert again["applied"] is True
    assert again["already"] is True
    assert again["already_set"], "an already-set choice must be named"
    assert "Already set" in again["summary"]
    assert "transparency" in again["summary"]


def test_transparency_carries_the_windows_11_limitation():
    """Windows 11 has no supported full-transparency taskbar. Saying so is the
    difference between a limit and a bug the user blames on the app."""
    import tempfile
    from pco_engine.service import Service

    d = tempfile.mkdtemp()
    svc = Service(f"{d}/x.db", catalog_dir="catalog")
    off = svc.shell.plan({"taskbar.transparency": "off"})
    caveat = off["changes"][0]["caveat"] if off["changes"] else \
        svc.shell.plan({"taskbar.transparency": "on"})["already"][0]["caveat"]
    assert "no supported" in caveat.lower() or "not a fully transparent" in caveat.lower()
