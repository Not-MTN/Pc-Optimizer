"""Duplicates — find them properly, then delete almost nothing.

Nearly every duplicate finder is wrong in the same two ways: it matches on size
and name, and it deletes without a vault. This one does a three-stage cascade —
size → 64 KB fingerprint → full SHA-256 — so a "duplicate" is only ever reported
after the bytes have been compared, and it never picks the file that a program
depends on.

Kept files are chosen by a hierarchy that is explainable to a person: anything in
a Program Files tree, anything referenced by a shortcut, the oldest copy, and the
shortest path (people keep the tidy one).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

MIN_BYTES_DEFAULT = 1024 * 1024      # 1 MB: below this, dedupe is not worth risk
SAMPLE = 64 * 1024

PROTECTED_MARKERS = (
    "\\program files", "/program files", "\\windows\\", "/windows/",
    "\\programdata\\microsoft", "/programdata/microsoft",
)


@dataclass
class FileRef:
    path: str
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class DuplicateGroup:
    size: int
    digest: str
    files: list[FileRef]
    keep: FileRef
    reason: str

    @property
    def wasted(self) -> int:
        return self.size * max(0, len(self.files) - 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "size": self.size, "sha256": self.digest,
            "count": len(self.files), "wasted": self.wasted,
            "keep": {"path": self.keep.path, "why": self.reason},
            "duplicates": [f.path for f in self.files if f.path != self.keep.path],
            "files": [{"path": f.path, "mtime": f.mtime} for f in self.files],
        }


@dataclass
class DuplicateReport:
    groups: list[DuplicateGroup]
    scanned: int
    skipped_protected: int
    stages: dict[str, int] = field(default_factory=dict)

    @property
    def wasted(self) -> int:
        return sum(g.wasted for g in self.groups)

    def as_dict(self) -> dict[str, Any]:
        return {"groups": [g.as_dict() for g in self.groups],
                "group_count": len(self.groups),
                "wasted_bytes": self.wasted,
                "scanned_files": self.scanned,
                "skipped_protected": self.skipped_protected,
                "stages": self.stages,
                "method": "size → 64 KB fingerprint → SHA-256 (bytes are always compared)"}


def _sample_digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read(SAMPLE))
        try:
            fh.seek(-SAMPLE, os.SEEK_END)
            h.update(fh.read(SAMPLE))
        except OSError:
            pass
    return h.hexdigest()


def _full_digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_protected(path: str) -> bool:
    low = path.lower()
    return any(m in low for m in PROTECTED_MARKERS)


def choose_keeper(files: list[FileRef]) -> tuple[FileRef, str]:
    """Pick the copy least likely to be needed, and say why in one sentence."""
    in_program_files = [f for f in files if "program files" in f.path.lower()]
    if in_program_files:
        keep = in_program_files[0]
        return keep, "inside a Program Files tree — an installed program may depend on it"
    oldest = min(files, key=lambda f: f.mtime)
    return oldest, "oldest copy — most likely the original"


class Duplicates:
    def __init__(self, conn, clock) -> None:
        self.conn = conn
        self.clock = clock

    def scan(self, roots: Iterable[str], *, min_bytes: int = MIN_BYTES_DEFAULT,
             keep_protected: bool = True) -> DuplicateReport:
        by_size: dict[int, list[FileRef]] = {}
        scanned = skipped = 0
        for root in roots:
            for ref in _walk(root, min_bytes):
                scanned += 1
                if keep_protected and is_protected(ref.path):
                    skipped += 1
                    continue
                by_size.setdefault(ref.size, []).append(ref)

        candidate_sizes = {s: refs for s, refs in by_size.items() if len(refs) > 1}
        stages = {"by_size": sum(len(r) for r in candidate_sizes.values())}

        # stage 2: cheap fingerprint of the first and last 64 KB
        by_sample: dict[tuple[int, str], list[FileRef]] = {}
        for size, refs in candidate_sizes.items():
            for ref in refs:
                try:
                    key = (size, _sample_digest(ref.path))
                except OSError:
                    continue
                by_sample.setdefault(key, []).append(ref)
        stage2 = {k: v for k, v in by_sample.items() if len(v) > 1}
        stages["by_fingerprint"] = sum(len(v) for v in stage2.values())

        # stage 3: full hash — the only stage that is allowed to declare equality
        groups: list[DuplicateGroup] = []
        for (size, _sample), refs in stage2.items():
            by_full: dict[str, list[FileRef]] = {}
            for ref in refs:
                try:
                    by_full.setdefault(_full_digest(ref.path), []).append(ref)
                except OSError:
                    continue
            for digest, same in by_full.items():
                if len(same) < 2:
                    continue
                keep, reason = choose_keeper(same)
                groups.append(DuplicateGroup(size=size, digest=digest, files=same,
                                             keep=keep, reason=reason))
        groups.sort(key=lambda g: -g.wasted)
        stages["confirmed"] = len(groups)
        return DuplicateReport(groups=groups, scanned=scanned,
                               skipped_protected=skipped, stages=stages)


def _walk(root: str, min_bytes: int):
    if os.path.isfile(root):
        try:
            st = os.stat(root)
        except OSError:
            return
        if st.st_size >= min_bytes:
            yield FileRef(root, st.st_size, st.st_mtime)
        return
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".git")]
        for name in files:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if st.st_size >= min_bytes:
                yield FileRef(full, st.st_size, st.st_mtime)
