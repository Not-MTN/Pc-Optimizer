"""Desktop Lab and prompt identity.

Two things are being defended here:

* **A refusal is a feature.** Windows 11 will not do a transparent taskbar, a
  moved taskbar, or Windows-7 labels. The app must say so by name instead of
  shipping a toggle that quietly writes a useless value.
* **A logo means something.** The GitHub mark appears because a remote, a token
  or a CLI named GitHub — never because a word matched. A local repo called
  `github-tools` must not light it up.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pco_engine.backend import FakeBackend  # noqa: E402
from pco_engine.providers import (  # noqa: E402
    PROVIDERS, detect, host_of, provider_for_remote,
)
from pco_engine.shell_lab import (  # noqa: E402
    REFUSED, SHELL, ShellLab, TASKBAR,
)

EXPLORER = ("registry:hkcu\\software\\microsoft\\windows\\currentversion\\"
            "explorer\\advanced")
SEARCH = "registry:hkcu\\software\\microsoft\\windows\\currentversion\\search"
PERSONALIZE = ("registry:hkcu\\software\\microsoft\\windows\\currentversion\\"
               "themes\\personalize")


@pytest.fixture
def lab():
    # Values are keyed by name, because Windows keeps ten settings in one key.
    be = FakeBackend({
        EXPLORER + "\\TaskbarAl": 1,
        EXPLORER + "\\TaskbarDa": 1,
        SEARCH + "\\SearchboxTaskbarMode": 2,
        PERSONALIZE + "\\AppsUseLightTheme": 1,
    })
    return ShellLab(be, lambda: 1_700_000_000_000)


# ============================================================== the offer
def test_every_option_names_the_registry_value_it_writes(lab):
    """The bug this prevents is bug 27 all over again: a setting whose label and
    whose address disagree. Every entry must carry a path and a value name."""
    for spec in list(TASKBAR.values()) + list(SHELL.values()):
        assert spec["path"].strip(), "a setting with no registry path"
        assert spec["value"].strip(), "a setting with no value name"
        assert spec["options"], "a setting with no choices"
        assert spec["default"] in spec["options"], (
            f"default {spec['default']!r} is not one of {sorted(spec['options'])}")
        assert len(spec["plain"]) > 20, "every setting must explain itself"


def test_the_refusals_are_real_and_explained(lab):
    """Windows 11 genuinely does not do these. Each must carry a reason long
    enough to be useful, not a shrug."""
    assert REFUSED, "the refusal list must not be empty"
    for key, why in REFUSED.items():
        assert len(why) > 60, f"{key}: the refusal is too terse to help anyone"
    refused_keys = {o["key"] for o in lab.options() if not o["supported"]}
    assert refused_keys == set(REFUSED)


def test_options_read_the_current_value_off_the_machine(lab):
    by_key = {o["key"]: o for o in lab.options()}
    assert by_key["taskbar.align"]["current"] == "center"   # value 1
    assert by_key["taskbar.search"]["current"] == "box"     # value 2
    assert by_key["shell.dark_mode"]["current"] == "light"


def test_an_absent_value_is_reported_as_none_not_as_a_default(lab):
    bare = ShellLab(FakeBackend({}), lambda: 0)
    by_key = {o["key"]: o for o in bare.options()}
    # nothing has been set on this machine; saying "center" would be a fabrication
    assert by_key["taskbar.align"]["current"] is None
    assert by_key["taskbar.align"]["default"] == "center"


# ============================================================== planning
def test_a_plan_lists_the_exact_writes_and_needs_no_engine(lab):
    plan = lab.plan({"taskbar.align": "left", "taskbar.widgets": "off"})
    assert plan["ok"] is True
    assert len(plan["changes"]) == 2
    addresses = {c["address"] for c in plan["changes"]}
    assert EXPLORER in addresses
    assert plan["explorer_restart_needed"] is True
    # planning must not touch the machine
    assert lab.backend.read(EXPLORER, value_name="TaskbarAl") == 1


def test_a_value_already_in_place_is_not_a_change(lab):
    """Writing 1 over 1 is still a write, and a write is a journal row. Skip it."""
    plan = lab.plan({"taskbar.align": "center"})
    assert plan["changes"] == []
    assert plan["ok"] is False            # nothing to do


def test_a_choice_outside_the_allowed_set_is_refused_with_the_list(lab):
    plan = lab.plan({"taskbar.align": "sideways"})
    assert plan["ok"] is False
    assert "sideways" in plan["problems"][0]
    assert "left" in plan["problems"][0] and "center" in plan["problems"][0]


def test_an_unknown_key_is_refused_not_written(lab):
    plan = lab.plan({"taskbar.magic": "yes"})
    assert plan["ok"] is False
    assert "not a setting this app knows" in plan["problems"][0]


def test_asking_for_a_refused_thing_reports_why_without_failing_the_rest(lab):
    plan = lab.plan({"taskbar.align": "left", "taskbar.transparent": "yes"})
    assert plan["ok"] is True                    # the supported part can proceed
    assert len(plan["changes"]) == 1
    assert plan["refused"][0]["key"] == "taskbar.transparent"
    assert "Explorer patching" in plan["refused"][0]["reason"]


def test_a_plan_that_is_only_refusals_changes_nothing(lab):
    plan = lab.plan({"taskbar.position": "top"})
    assert plan["ok"] is False
    assert plan["changes"] == []
    assert "refuses" in plan["summary"]


# ============================================================== themes
def test_a_valid_theme_round_trips(lab):
    doc = lab.theme_document("Midnight", {"taskbar.align": "left",
                                          "shell.dark_mode": "dark"})
    assert lab.validate_theme(doc) == []


def test_a_theme_with_an_unknown_key_is_rejected_whole(lab):
    """An import that half-applies is how a desktop ends up in a state nobody
    planned. One bad key rejects the document."""
    problems = lab.validate_theme({"name": "x", "settings": {"taskbar.align": "left",
                                                             "nope.nope": "yes"}})
    assert problems and "nope.nope" in problems[0]


def test_a_theme_that_changes_nothing_is_not_a_theme(lab):
    assert lab.validate_theme({"name": "Empty", "settings": {}})


def test_a_theme_cannot_smuggle_in_a_refused_setting(lab):
    problems = lab.validate_theme({"name": "Sneaky",
                                   "settings": {"startmenu.win7": "yes"}})
    assert problems and "shell replacement" in problems[0]


def test_a_theme_needs_a_name(lab):
    assert lab.validate_theme({"settings": {"taskbar.align": "left"}})


# ============================================================== icons
def test_only_icons_windows_supports_per_user_are_offered(lab):
    out = lab.plan_icon("icons.this_pc", "C:\\icons\\pc.ico")
    assert out["ok"] is False            # not in the per-user CLSID table
    ok = lab.plan_icon("icons.recycle_bin_empty", "C:\\icons\\bin.ico")
    assert ok["ok"] is True
    assert ok["address"].startswith("registry:hkcu\\")      # per-user, never HKLM
    assert "645ff040" in ok["address"]                      # the recycle-bin CLSID
    assert ok["registry_value_name"] == "Empty"


def test_full_and_empty_bins_are_different_values_in_the_same_key(lab):
    """Both icons live in one registry key as two named values. Writing the
    default value would change the wrong picture and look like a no-op."""
    full = lab.plan_icon("icons.recycle_bin_full", "C:\\i\\a.ico")
    empty = lab.plan_icon("icons.recycle_bin_empty", "C:\\i\\b.ico")
    assert full["address"] == empty["address"], "same key, by design"
    assert full["registry_value_name"] == "Full"
    assert empty["registry_value_name"] == "Empty"


def test_a_non_icon_path_is_refused(lab):
    out = lab.plan_icon("icons.recycle_bin_empty", "C:\\icons\\not-an-icon.png")
    assert out["ok"] is False
    assert ".ico" in out["problems"][0]


def test_an_icon_can_point_into_a_dll_or_exe(lab):
    assert lab.plan_icon("icons.recycle_bin_full",
                         "%SystemRoot%\\System32\\imageres.dll,-54")["ok"] is True


# ============================================================== terminal
def test_terminal_settings_are_named_keys_not_a_whole_file(lab):
    t = lab.terminal_settings()
    assert "profiles.defaults" in t
    assert t["profiles.defaults"]["font"]["face"] == "Cascadia Code"
    assert "note" in t
    # it must say it preserves the rest, because that is the safety argument
    assert "preserv" in t["note"]


# ============================================================== providers
@pytest.mark.parametrize("url,expected_host", [
    ("https://github.com/a/b.git", "github.com"),
    ("git@github.com:a/b.git", "github.com"),
    ("ssh://git@gitlab.com/a/b.git", "gitlab.com"),
    ("git://github.com/a/b", "github.com"),
    ("git+https://github.com/a/b", "github.com"),
    ("not a url at all", None),
    ("", None),
])
def test_host_extraction_handles_every_git_remote_shape(url, expected_host):
    """No URL parser handles the scp-style remote git actually uses, which is why
    a naive implementation silently fails to recognise half the world's repos."""
    assert host_of(url) == expected_host


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/a/b", "github"),
    ("git@github.com:a/b", "github"),
    ("https://gitlab.com/a/b", "gitlab"),
    ("https://registry.npmjs.org/pkg", "npm"),
    ("https://pypi.org/simple/x", "pypi"),
    ("https://bitbucket.org/a/b", None),      # unsupported, and says so
    ("https://my-nas.local/repo", None),
])
def test_provider_is_matched_by_host(url, expected):
    assert provider_for_remote(url) == expected


def test_a_local_repo_called_github_is_not_github():
    """The whole point. A folder name is not evidence."""
    ident = detect(remotes=["https://gitbox.internal/github-tools.git"],
                   env={}, which=lambda c: None)
    assert ident.key is None
    assert ident.confidence == "none"
    assert "gitbox.internal" in ident.evidence


def test_a_remote_is_confirmed_evidence():
    ident = detect(remotes=["git@github.com:Not-MTN/Pc-Optimizer.git"],
                   env={}, which=lambda c: None)
    assert ident.key == "github" and ident.confidence == "confirmed"
    assert ident.color.startswith("#")
    assert len(ident.glyph) > 100, "the mark must be real path data, not a placeholder"


def test_running_a_provider_cli_names_that_provider():
    """`gh pr create` is about GitHub even in a repo with no remote."""
    ident = detect(command="gh pr create", env={}, which=lambda c: None)
    assert ident.key == "github" and "gh" in ident.evidence


def test_git_alone_does_not_claim_a_provider():
    ident = detect(command="git push", env={}, which=lambda c: None)
    assert ident.key is None


def test_a_token_is_likely_evidence_not_confirmed():
    ident = detect(env={"GH_TOKEN": "x"}, which=lambda c: None)
    assert ident.key == "github"
    assert ident.confidence == "likely", (
        "an environment variable is an intent, not proof of a remote")


def test_an_installed_cli_is_the_weakest_signal_and_says_so():
    ident = detect(env={}, which=lambda c: c == "gh")
    assert ident.key == "github"
    assert ident.confidence == "likely"
    assert "installed" in ident.evidence


def test_nothing_detected_shows_nothing_rather_than_a_guess():
    ident = detect(env={}, which=lambda c: None)
    assert ident.key is None
    assert ident.glyph == ""
    assert ident.name == "No provider"


def test_every_provider_has_a_legible_colour_for_both_themes():
    for key, spec in PROVIDERS.items():
        assert spec["color"].startswith("#") and len(spec["color"]) == 7, key
        if "color_light" in spec:
            assert spec["color_light"].startswith("#")
        assert spec["glyph"], f"{key} has no mark — an empty glyph is a lie"
        assert spec["hosts"] and spec["commands"], key


# ============================================================== through service
def test_service_apply_writes_journals_and_undoes(service):
    """Desktop customization goes through the same journal as everything else."""
    before = service.backend.read(EXPLORER, value_name="TaskbarAl")

    preview = service.shell_preview({"taskbar.align": "left"})
    assert preview["ok"] is True
    assert service.backend.read(EXPLORER, value_name="TaskbarAl") == before

    out = service.shell_apply({"taskbar.align": "left"})
    assert out["applied"] is True
    assert service.backend.read(EXPLORER, value_name="TaskbarAl") == 0
    ok, rows, _ = service.engine.journal.verify_chain()
    assert ok and rows >= 3

    service.undo(out["tx_id"])
    assert service.backend.read(EXPLORER, value_name="TaskbarAl") == before


def test_service_apply_treats_a_refusal_as_a_refusal(service):
    out = service.shell_apply({"taskbar.position": "top"})
    assert out["applied"] is False
    assert out["refused"][0]["key"] == "taskbar.position"
    # and nothing was journaled
    assert service.backend.read(EXPLORER, value_name="TaskbarAl") == 1


def test_a_partial_set_applies_the_supported_half_and_says_so(service):
    out = service.shell_apply({"shell.show_extensions": "show",
                               "taskbar.transparent": "yes"})
    assert out["applied"] is True
    assert len(out["changes"]) == 1
    assert out["refused"][0]["key"] == "taskbar.transparent"


def test_themes_save_import_and_never_apply_on_import(service):
    saved = service.save_shell_theme("Midnight", {"taskbar.align": "left"})
    assert saved["saved"] is True
    listed = service.shell_themes()["themes"]
    assert "Midnight" in [t["name"] for t in listed]

    bad = service.import_shell_theme({"name": "Bad", "settings": {"x.y": "z"}})
    assert bad["imported"] is False and bad["problems"]

    good = service.import_shell_theme({"name": "Clean",
                                       "settings": {"shell.dark_mode": "dark"}})
    assert good["imported"] is True
    assert "not applied" in good["note"]
    detail = service.backend.read(PERSONALIZE, value_name="AppsUseLightTheme")
    assert detail == 1, "importing a theme must not write to the machine"


def test_prompt_identity_is_reported_with_its_evidence(service):
    out = service.prompt_identity(command="gh workflow run")
    assert out["key"] == "github"
    assert out["evidence"]
    assert out["glyph"]


def test_undo_restores_the_right_value_when_ten_share_a_key(service):
    """The subtlest bug in this feature.

    The journal records `address -> old value`. For Explorer preferences the
    address is a *key* holding ten settings, so a naive revert writes the old
    value back to whichever name the backend defaults to — restoring a setting
    the user never touched. The revert path recovers the name from the tweak id.
    """
    before_align = service.backend.read(EXPLORER, value_name="TaskbarAl")
    before_widgets = service.backend.read(EXPLORER, value_name="TaskbarDa")

    out = service.shell_apply({"taskbar.widgets": "off"})
    assert out["applied"] is True
    assert service.backend.read(EXPLORER, value_name="TaskbarDa") == 0
    assert service.backend.read(EXPLORER, value_name="TaskbarAl") == before_align

    service.undo(out["tx_id"])
    assert service.backend.read(EXPLORER, value_name="TaskbarDa") == before_widgets
    # and the untouched neighbour must be exactly as it was
    assert service.backend.read(EXPLORER, value_name="TaskbarAl") == before_align


def test_two_settings_in_one_key_can_be_changed_together_and_undone(service):
    out = service.shell_apply({"taskbar.align": "left", "taskbar.widgets": "off"})
    assert out["applied"] is True
    assert len(out["changes"]) == 2
    assert service.backend.read(EXPLORER, value_name="TaskbarAl") == 0
    assert service.backend.read(EXPLORER, value_name="TaskbarDa") == 0
    service.undo(out["tx_id"])
    assert service.backend.read(EXPLORER, value_name="TaskbarAl") == 1
    assert service.backend.read(EXPLORER, value_name="TaskbarDa") == 1
