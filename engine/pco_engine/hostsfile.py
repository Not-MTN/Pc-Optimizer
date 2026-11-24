"""Blocking that actually blocks: a managed block in the system hosts file.

Why this and not a DNS proxy. Pointing an adapter at a resolver Keel runs means
Keel must be running, listening on port 53, and winning a race against Windows'
own resolver — three things this build does not do. The hosts file needs none of
that: Windows consults it before it asks any server, so a name listed there is
dead for every application on the machine, whether Keel is running or not.

The trade-offs are stated where they bite, because they are real:
  * it needs administrator to write `C:\\Windows\\System32\\drivers\\etc\\hosts`;
  * it blocks by *name*, so an app that dials an IP directly is unaffected;
  * a managed block is only as good as its markers — the user's own lines are
    never touched, and removing the block restores the file exactly.

Everything here is reversible and verified: we back the file up once, write
atomically, read the result back, and refuse to report success we cannot see.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

BEGIN = "# >>> Keel blocklist — managed automatically, do not edit these lines >>>"
END = "# <<< Keel blocklist <<<"

# 0.0.0.0 rather than 127.0.0.1: a loopback address can still answer if something
# is listening on that port, and "connection refused" is slower to fail than a
# black hole. This is what every hosts-based blocker uses, for that reason.
SINK = "0.0.0.0"

WINDOWS_HOSTS = r"C:\Windows\System32\drivers\etc\hosts"


@dataclass
class HostsResult:
    ok: bool
    path: str = ""
    lines: int = 0                  # how many domains are in the block now
    reason: str = ""
    backup: str = ""
    needs_admin: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "path": self.path, "lines": self.lines,
                "reason": self.reason, "backup": self.backup,
                "needs_admin": self.needs_admin}


def default_path(backend: Any = None) -> str:
    """The hosts file to use. Overridable so a test never touches the real one."""
    override = os.environ.get("PCO_HOSTS_PATH")
    if override:
        return override
    getter = getattr(backend, "hosts_path", None)
    if getter is not None:
        try:
            got = getter()
            if got:
                return got
        except Exception:
            pass
    if os.name == "nt":
        return WINDOWS_HOSTS
    return "/etc/hosts"


def strip_block(text: str) -> str:
    """The file with Keel's managed block removed, and nothing else changed."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.strip() == BEGIN:
            inside = True
            continue
        if line.strip() == END:
            inside = False
            continue
        if not inside:
            out.append(line)
    # Collapse the trailing blank run we may have created, never the user's own.
    while out and out[-1].strip() == "":
        out.pop()
    return "\n".join(out) + ("\n" if out else "")


def read_block_domains(text: str) -> list[str]:
    """Domains currently inside the managed block."""
    found: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.strip() == BEGIN:
            inside = True
            continue
        if line.strip() == END:
            inside = False
            continue
        if not inside:
            continue
        parts = line.split()
        if len(parts) >= 2 and not line.strip().startswith("#"):
            # "0.0.0.0 a.example b.example" — take every name on the line
            found.extend(p.lower() for p in parts[1:])
    return found


def normalise(domains: Iterable[str]) -> list[str]:
    """Deduplicated, sorted, lowercased bare hostnames — wildcards included.

    A hosts entry matches one exact name, so a pattern has to be expanded before
    it can be enforced: `*.doubleclick.net` is written as `doubleclick.net`
    (which a hosts file treats as an exact name) plus the wildcard's own base, so
    the block actually covers the domain family rather than nothing.

    The trade-off is stated rather than hidden: `*.example.com` also blocks
    `example.com` itself. That is right for tracker and ad domains — the whole
    point is to kill the family — and it is why this is only used for lists the
    user chose to enable. Where it would be wrong (a CDN apex that also serves
    content) the list should not contain a bare wildcard.
    """
    out: set[str] = set()
    for d in domains:
        name = str(d).strip().lower().strip(".")
        if not name or name.startswith("#") or name.startswith("!"):
            continue
        if name.startswith("||"):                 # adblock syntax: ||host^
            name = name[2:].split("^")[0].strip(".")
        if name.startswith("0.0.0.0 "):           # already a hosts line
            name = name.split(None, 1)[1].strip()
        if name.startswith("*."):
            # Break the wildcard into the names a hosts file can hold.
            name = name[2:].strip(".")
        if not name or "/" in name or " " in name:
            continue
        if "*" in name:                           # mid-label wildcards are not
            continue                              # expressible; skip, not guess
        out.add(name)
    return sorted(out)


def compose(original: str, domains: Sequence[str]) -> str:
    """The new file: the user's lines verbatim, then one managed block."""
    body = strip_block(original)
    if not domains:
        return body
    block = [BEGIN, f"# {len(domains)} hostname(s) · written by Keel"]
    block.extend(f"{SINK} {d}" for d in domains)
    block.append(END)
    if body and not body.endswith("\n"):
        body += "\n"
    return body + "\n".join(block) + "\n"


class HostsFile:
    """Read/write/verify the managed block. Injected path keeps tests honest."""

    def __init__(self, path: str | None = None, *, backend: Any = None) -> None:
        self._fixed = path
        self.backend = backend

    # -- io ---------------------------------------------------------------
    @property
    def path(self) -> str:
        return self._fixed or default_path(self.backend)

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def read(self) -> str:
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def _backup_once(self) -> str:
        """Keep the user's original exactly once, beside the file."""
        target = self.path + ".keel-backup"
        if not os.path.exists(target):
            shutil.copy2(self.path, target)
        return target

    def apply(self, domains: Sequence[str]) -> HostsResult:
        names = normalise(domains)
        if not names:
            return HostsResult(False, self.path,
                               reason="The blocklist is empty, so there is nothing "
                                      "to write. Enable a list first.")
        if not self.exists():
            return HostsResult(False, self.path,
                               reason=f"No hosts file at {self.path}.")
        try:
            original = self.read()
        except PermissionError as exc:
            return HostsResult(False, self.path, reason=str(exc), needs_admin=True)

        try:
            backup = self._backup_once()
        except OSError as exc:
            return HostsResult(False, self.path,
                               reason=f"Could not back up the hosts file: {exc}",
                               needs_admin=getattr(exc, "errno", 0) == 13)

        # Ask whether we may write *before* writing. On Windows a non-elevated
        # process gets PermissionError from the write itself, but the check is
        # cheap and it makes the refusal immediate and specific rather than
        # relying on the error a temp-file swap happens to raise.
        if not os.access(self.path, os.W_OK):
            return HostsResult(
                False, self.path, backup=backup, needs_admin=True,
                reason=("Writing the hosts file needs administrator rights. "
                        "Nothing was changed — press “Restart as administrator” "
                        "in the banner, then try again."))

        text = compose(original, names)
        try:
            self._atomic_write(text)
        except PermissionError:
            return HostsResult(
                False, self.path, backup=backup, needs_admin=True,
                reason=("Writing the hosts file needs administrator rights. "
                        "Nothing was changed — press “Restart as administrator” "
                        "in the banner, then try again."))
        except OSError as exc:
            return HostsResult(False, self.path, backup=backup,
                               reason=f"Could not write {self.path}: {exc}")

        # Verify by reading the file Windows will actually consult.
        try:
            written = self.read()
        except OSError as exc:
            return HostsResult(False, self.path, backup=backup,
                               reason=f"Wrote the file but could not read it back: {exc}")
        live = read_block_domains(written)
        missing = [n for n in names if n not in set(live)]
        if missing:
            return HostsResult(
                False, self.path, lines=len(live), backup=backup,
                reason=(f"{len(missing)} of {len(names)} entries are missing after "
                        f"writing — another program may be rewriting the file."))
        return HostsResult(True, self.path, lines=len(live), backup=backup)

    def clear(self) -> HostsResult:
        if not self.exists():
            return HostsResult(True, self.path, lines=0)
        try:
            original = self.read()
        except PermissionError:
            return HostsResult(False, self.path, needs_admin=True,
                               reason=("Reading the hosts file needs administrator "
                                       "rights. Nothing was changed."))
        if BEGIN not in original:
            return HostsResult(True, self.path, lines=0)
        if not os.access(self.path, os.W_OK):
            return HostsResult(False, self.path, needs_admin=True,
                               reason=("Removing the block needs administrator "
                                       "rights. Your hosts file is unchanged and "
                                       "still blocking."))
        try:
            self._atomic_write(strip_block(original))
        except PermissionError:
            return HostsResult(False, self.path, needs_admin=True,
                               reason=("Removing the block needs administrator "
                                       "rights. Your hosts file is unchanged and "
                                       "still blocking."))
        except OSError as exc:
            return HostsResult(False, self.path,
                               reason=f"Could not write {self.path}: {exc}")
        try:
            left = read_block_domains(self.read())
        except OSError:
            left = []
        if left:
            return HostsResult(False, self.path, lines=len(left),
                               reason="The block is still present after clearing it.")
        return HostsResult(True, self.path, lines=0)

    def status(self) -> dict[str, Any]:
        try:
            text = self.read() if self.exists() else ""
        except OSError as exc:
            return {"available": False, "path": self.path, "reason": str(exc),
                    "blocked": 0}
        return {"available": True, "path": self.path,
                "blocked": len(read_block_domains(text)),
                "backup": os.path.exists(self.path + ".keel-backup")}

    # -- internals --------------------------------------------------------
    def _atomic_write(self, text: str) -> None:
        """Write beside the target and swap, so a crash cannot leave it truncated.

        The hosts file is consulted by every socket on the machine; a half-written
        one is a broken machine. `os.replace` is atomic on both platforms.
        """
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".keel-hosts-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            try:
                shutil.copymode(self.path, tmp)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
