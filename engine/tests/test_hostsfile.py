"""DNS blocking has to change name resolution, not a flag in our own database."""
from __future__ import annotations

import os
import stat

import pytest

from pco_engine.hostsfile import (BEGIN, END, HostsFile, compose,
                                  read_block_domains, strip_block)

ORIGINAL = (
    "# Copyright (c) 1993-2009 Microsoft Corp.\n"
    "#\n"
    "127.0.0.1 localhost\n"
    "::1 localhost\n"
)


@pytest.fixture()
def hosts(tmp_path):
    path = tmp_path / "hosts"
    path.write_text(ORIGINAL, encoding="utf-8")
    return HostsFile(str(path))


def test_blocking_writes_real_entries_into_the_file(hosts):
    out = hosts.apply(["ads.doubleclick.net", "telemetry.microsoft.com"])
    assert out.ok, out.reason
    text = hosts.read()
    assert f"{'0.0.0.0'} ads.doubleclick.net" in text
    assert "0.0.0.0 telemetry.microsoft.com" in text
    assert out.lines == 2


def test_the_users_own_lines_are_never_touched(hosts):
    hosts.apply(["ads.example.com"])
    text = hosts.read()
    for line in ORIGINAL.splitlines():
        assert line in text, "a line that was already in the file went missing"


def test_turning_it_off_restores_the_file_exactly(hosts):
    hosts.apply(["ads.example.com", "tracker.example.com"])
    out = hosts.clear()
    assert out.ok, out.reason
    assert hosts.read() == ORIGINAL, (
        "clearing must leave the hosts file byte-identical to what the user had")


def test_a_backup_of_the_original_is_kept(hosts):
    out = hosts.apply(["ads.example.com"])
    assert os.path.exists(out.backup)
    with open(out.backup, encoding="utf-8") as fh:
        assert fh.read() == ORIGINAL


def test_without_admin_it_refuses_and_changes_nothing(hosts):
    """A non-elevated account must be told, not quietly succeed."""
    path = hosts.path
    os.chmod(path, stat.S_IRUSR)
    try:
        out = hosts.apply(["ads.example.com"])
        assert out.ok is False
        assert out.needs_admin is True
        assert "administrator" in out.reason.lower()
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        assert hosts.read() == ORIGINAL
    finally:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def test_an_empty_list_refuses_rather_than_writing_nothing(hosts):
    out = hosts.apply([])
    assert out.ok is False
    assert "empty" in out.reason.lower()
    assert hosts.read() == ORIGINAL


def test_wildcards_are_expanded_so_they_actually_block(hosts):
    """A hosts entry matches one exact name, so a pattern must be expanded.

    Dropping wildcards instead — the first version — meant a list of fourteen
    tracker patterns produced two hosts lines: the app said "blocking on" and
    thirteen of its own rules did nothing.
    """
    hosts.apply(["*.doubleclick.net", "ads.example.com"])
    names = read_block_domains(hosts.read())
    assert "doubleclick.net" in names, "a wildcard must survive as a real entry"
    assert "ads.example.com" in names
    assert os.path.exists(hosts.path + ".keel-backup")


def test_a_mid_label_wildcard_is_skipped_not_guessed(hosts):
    hosts.apply(["*.*.bad.test", "real.example.com"])
    names = read_block_domains(hosts.read())
    assert names == ["real.example.com"], (
        "a pattern a hosts file cannot express is skipped, not approximated into "
        "something that blocks the wrong thing")


def test_reading_a_block_that_is_not_there_is_empty(hosts):
    assert read_block_domains(ORIGINAL) == []
    assert strip_block(ORIGINAL) == ORIGINAL


def test_compose_is_idempotent(hosts):
    once = compose(ORIGINAL, ["a.example.com", "b.example.com"])
    twice = compose(once, ["a.example.com", "b.example.com"])
    assert once == twice, "applying twice must not stack a second block"
    assert once.count(BEGIN) == 1 and once.count(END) == 1


def test_duplicate_and_messy_entries_are_normalised(hosts):
    out = hosts.apply(["  ADS.Example.com  ", "ads.example.com", "#comment",
                       "||track.example.com^", ""])
    assert out.ok, out.reason
    names = read_block_domains(hosts.read())
    assert names == ["ads.example.com", "track.example.com"]


def test_status_reports_what_is_actually_enforced(hosts):
    assert hosts.status()["blocked"] == 0
    hosts.apply(["a.example.com", "b.example.com"])
    assert hosts.status()["blocked"] == 2
    hosts.clear()
    assert hosts.status()["blocked"] == 0


def test_the_service_reports_enforced_separately_from_configured(tmp_path, monkeypatch):
    """`patterns` is what the lists hold; `enforced` is what the machine obeys.

    Those two numbers disagreeing is exactly the bug this release fixes, so they
    must both be visible rather than one standing in for the other.
    """
    from pco_engine.service import Service

    path = tmp_path / "hosts"
    path.write_text(ORIGINAL, encoding="utf-8")

    svc = Service(str(tmp_path / "s.db"), catalog_dir="catalog")

    class Windowsish:
        environment = "windows"

    svc.dns.backend = Windowsish()
    svc.dns.hosts = HostsFile(str(path))

    before = svc.run_action("status", {})["dns"]
    assert before["patterns"] > 0
    assert before["enforced"] == 0, "nothing is blocked before it is switched on"

    svc.run_action("dns.on", {})
    after = svc.run_action("status", {})["dns"]
    # Distinct hostnames can be fewer than raw patterns (two wildcards may name
    # the same base), but nearly all of them must survive — this is the check
    # that caught wildcards being silently dropped.
    assert after["enforced"] >= after["patterns"] - 2, (
        f"only {after['enforced']} of {after['patterns']} patterns reached the "
        "hosts file; wildcards are being dropped instead of expanded")
    assert after["enforced"] > 0

    svc.run_action("dns.off", {})
    off = svc.run_action("status", {})["dns"]
    assert off["enforced"] == 0
    assert path.read_text(encoding="utf-8") == ORIGINAL


def test_a_write_that_does_not_land_is_reported_as_failed(tmp_path):
    """Some other agent (a security suite, Windows' own reset) rewrites the file.

    The old code would have reported success — it wrote, so it assumed the write
    took. Verification is the only thing that distinguishes "I asked" from "it
    happened", and without it the window says "blocking on" over a clean file.
    """
    path = tmp_path / "hosts"
    path.write_text(ORIGINAL, encoding="utf-8")

    class Racer(HostsFile):
        """Writes, then something else replaces the file immediately after."""

        def _atomic_write(self, text: str) -> None:
            super()._atomic_write(text)
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write(ORIGINAL)          # an external agent undoes it

    out = Racer(str(path)).apply(["ads.example.com", "tracker.example.com"])
    assert out.ok is False, (
        "a write the machine did not keep must never be reported as done")
    assert "missing" in out.reason.lower() or "rewriting" in out.reason.lower()
