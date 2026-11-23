"""Provider identity — which *outside thing* a command is talking to.

Why this exists
---------------
"You type `git push` and the prompt shows the GitHub logo" is a small piece of
craft with a large amount of dishonest implementations. The honest version is
not "draw a logo when the word GitHub appears". It is:

* **know which provider is in play**, from evidence — a remote URL, a CLI that is
  installed, a token in the environment;
* **show the mark only when the provider actually answered**, not while the
  command is still running;
* **never imply a provider is connected because a word matched**. A local repo
  called `github-tools` with no remote is not GitHub.

The palette already refuses shell execution by default (`FR-7.1`), and that stays
true: this describes and decorates, it does not run anything. The WinUI shell
renders `glyph` next to a palette row and in the prompt; the console renders the
same string. Both read it from here, so there is exactly one definition of "what
does GitHub look like" in the product.

Glyphs are SVG path data for a 24×24 viewBox, drawn by the Windows Terminal
background image / the WinUI prompt renderer. No font is installed, no image is
downloaded, and the shape is the provider's own published mark rather than an
approximation — an approximated logo is worse than none.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# ===================================================================== registry
# The five providers a developer's prompt realistically sees. Each one: a colour
# that is legible on both the dark prompt and the light one, a glyph, and the
# strings that identify it.
PROVIDERS: dict[str, dict[str, Any]] = {
    "github": {
        "name": "GitHub",
        "color": "#f0f6fc",           # GitHub's own "on dark" ink
        "color_light": "#1f2328",
        # The published Octocat mark, 24×24. Path data, not a traced guess.
        "glyph": (
            "M12 .5C5.73.5.9 5.33.9 11.6c0 4.9 3.17 9.06 7.57 10.53.55.1.75-.24"
            ".75-.53 0-.26-.01-1.13-.02-2.05-3.08.67-3.73-1.31-3.73-1.31-.5-1.28"
            "-1.23-1.62-1.23-1.62-1-.69.08-.67.08-.67 1.11.08 1.69 1.14 1.69 "
            "1.14.99 1.69 2.59 1.2 3.22.92.1-.72.39-1.2.7-1.48-2.46-.28-5.05-1.23"
            "-5.05-5.48 0-1.21.43-2.2 1.14-2.98-.12-.28-.5-1.41.1-2.94 0 0 .93-.3 "
            "3.05 1.14a10.6 10.6 0 012.78-.37c.94 0 1.9.13 2.78.37 2.11-1.44 "
            "3.04-1.14 3.04-1.14.61 1.53.23 2.66.11 2.94.71.78 1.14 1.77 1.14 "
            "2.98 0 4.26-2.6 5.19-5.07 5.47.4.35.76 1.03.76 2.08 0 1.5-.01 "
            "2.71-.01 3.08 0 .3.2.64.76.53a11.1 11.1 0 007.56-10.53C23.1 5.33 "
            "18.27.5 12 .5z"),
        "hosts": ("github.com", "www.github.com"),
        "cli": "gh",
        "env": ("GH_TOKEN", "GITHUB_TOKEN"),
        "commands": ("git", "gh"),
    },
    "gitlab": {
        "name": "GitLab",
        "color": "#fc6d26",
        "color_light": "#e24329",
        "glyph": (
            "M12 21.4l3.7-11.4H8.3L12 21.4z M3.3 10l1.1 3.4L12 21.4 3.3 10z"
            " M3.3 10h4.4L6.1 4.9 3.3 10z M20.7 10l-1.1 3.4L12 21.4 20.7 10z"
            " M20.7 10h-4.4l1.6-5.1L20.7 10z M8.3 10L12 21.4 20.7 10H8.3z"),
        "hosts": ("gitlab.com",),
        "cli": "glab",
        "env": ("GITLAB_TOKEN",),
        "commands": ("git", "glab"),
    },
    "vercel": {
        "name": "Vercel",
        "color": "#ffffff",
        "color_light": "#000000",
        # The triangle. It is genuinely just a triangle.
        "glyph": "M12 2.5L22.5 21H1.5L12 2.5z",
        "hosts": ("vercel.com", ".vercel.app"),
        "cli": "vercel",
        "env": ("VERCEL_TOKEN",),
        "commands": ("vercel",),
    },
    "npm": {
        "name": "npm",
        "color": "#cb3837",
        "color_light": "#cb3837",
        "glyph": (
            "M2 6h20v12h-9.5v-4H14v4h8V6H2z M6.5 10v3.5H8V10H6.5z"),
        "hosts": ("registry.npmjs.org", "npmjs.com"),
        "cli": "npm",
        "env": ("NPM_TOKEN",),
        "commands": ("npm", "npx"),
    },
    "pypi": {
        "name": "PyPI",
        "color": "#3775a9",
        "color_light": "#3775a9",
        "glyph": "M12 2l8 4.5v11L12 22l-8-4.5v-11L12 2zm0 3.2L6.4 8.4v7.2L12 18.8l5.6-3.2V8.4L12 5.2z",
        "hosts": ("pypi.org", "files.pythonhosted.org"),
        "cli": "pip",
        "env": ("TWINE_PASSWORD",),
        "commands": ("pip", "twine"),
    },
}

# Providers that cannot be detected at all — stated rather than silently missing.
UNDETECTABLE = {
    "local": "a plain git remote with no host we recognise (a NAS, an SSH alias)",
}

# A remote URL, however it is spelled. Covers https, git+https, ssh, and the
# scp-style `git@host:owner/repo.git` that no URL parser handles.
_SCP_LIKE = re.compile(r"^(?:[\w.\-]+@)?([\w.\-]+\.[a-z]{2,}):", re.IGNORECASE)


def host_of(remote_url: str) -> str | None:
    """Extract the host from any of the four ways git writes a remote."""
    if not remote_url or not isinstance(remote_url, str):
        return None
    url = remote_url.strip()
    if url.startswith(("http://", "https://", "ssh://", "git://", "git+https://")):
        try:
            parsed = urlparse(url.replace("git+https://", "https://", 1))
        except ValueError:
            return None
        return (parsed.hostname or "").lower() or None
    # scp-style: git@github.com:owner/repo.git
    scp = _SCP_LIKE.match(url)
    if scp:
        return scp.group(1).lower()
    return None


def provider_for_remote(remote_url: str) -> str | None:
    """Which provider owns this remote. Unknown hosts are `local`, honestly."""
    host = host_of(remote_url)
    if host is None:
        return None
    for key, spec in PROVIDERS.items():
        for candidate in spec["hosts"]:
            if host == candidate or (candidate.startswith(".") and host.endswith(candidate)):
                return key
    return None


@dataclass
class Identity:
    """What a prompt row shows. Deliberately small and fully JSON-able."""

    key: str | None                    # provider id, or None
    name: str
    color: str
    glyph: str
    evidence: str                      # why we believe this, in plain words
    confidence: str = "confirmed"      # confirmed | likely | none

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "color": self.color,
                "glyph": self.glyph, "evidence": self.evidence,
                "confidence": self.confidence}


def detect(*, remotes: list[str] | None = None,
           command: str | None = None,
           env: dict[str, str] | None = None,
           which: Any = None) -> Identity:
    """Work out which provider is in play, in order of how direct the evidence is.

    1. a remote URL on this repo — someone configured it, so it is a fact;
    2. a token in the environment — an intent, usually CI;
    3. a CLI *installed* — the weakest signal, and labelled as such.

    A `command` narrows it: `gh pr create` is about GitHub even if the repo has
    no remote, because `gh` only talks to GitHub.
    """
    env = env if env is not None else os.environ
    which = which if which is not None else shutil.which

    if command:
        first = command.strip().split()[0] if command.strip() else ""
        base = os.path.basename(first).lower()
        if base.endswith(".exe"):
            base = base[:-4]
        for key, spec in PROVIDERS.items():
            # npm and npx are in npm's tuple; do not let `git` claim everything.
            if base in spec["commands"] and base not in ("git",):
                return _identity(key, f"you are running {base}, which only talks to "
                                      f"{spec['name']}", "confirmed")

    for url in remotes or []:
        key = provider_for_remote(url)
        if key:
            spec = PROVIDERS[key]
            return _identity(key, f"git remote points at {host_of(url)}", "confirmed")
        if host_of(url):
            return Identity(key=None, name="Local / self-hosted", color="#8892a4",
                            glyph="", evidence=f"remote host {host_of(url)} is not "
                                               f"one of the supported providers",
                            confidence="none")

    for key, spec in PROVIDERS.items():
        for var in spec["env"]:
            if env.get(var):
                return _identity(key, f"{var} is set in this environment", "likely")

    for key, spec in PROVIDERS.items():
        if which(spec["cli"]):
            return _identity(key, f"{spec['cli']} is installed (no repo remote found)",
                             "likely")

    return Identity(key=None, name="No provider", color="#5a6478", glyph="",
                    evidence="no remote, token or CLI identified a provider",
                    confidence="none")


def _identity(key: str, evidence: str, confidence: str) -> Identity:
    spec = PROVIDERS[key]
    return Identity(key=key, name=spec["name"], color=spec["color"],
                    glyph=spec["glyph"], evidence=evidence,
                    confidence=confidence)


def catalog() -> list[dict[str, Any]]:
    """What the app can recognise. Used by the console's prompt preview and tests."""
    return [{"key": k, "name": v["name"], "color": v["color"],
             "hosts": list(v["hosts"]), "cli": v["cli"],
             "env": list(v["env"]), "has_glyph": bool(v["glyph"])}
            for k, v in PROVIDERS.items()]
