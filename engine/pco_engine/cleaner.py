"""The Cleaner — classification by regeneration cost, not by size.

Everything deleted goes to a vault on the same volume and is restorable for the
retention window. "Freed 42 GB" is only honest if the user can take it back.

Rules are data (`clean_rules`), so the engine stays generic and rules ship like
definitions — exactly as specified.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Iterable

from .ids import new_id

CLASSES = {"free", "auto_regen", "policy"}     # "policy" is never auto-selected
DEFAULT_RETENTION_DAYS = 30


@dataclass
class Rule:
    id: str
    title: str
    cls: str
    risk: str
    patterns: tuple[str, ...]
    note: str = ""
    exempt: tuple[str, ...] = ()
    enabled: bool = True


@dataclass
class Candidate:
    rule_id: str
    path: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "path": self.path, "size": self.size}


@dataclass
class ScanResult:
    candidates: list[Candidate]
    per_rule: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(c.size for c in self.candidates)

    @property
    def total_files(self) -> int:
        return len(self.candidates)

    def as_dict(self) -> dict[str, Any]:
        return {"total_bytes": self.total_bytes, "total_files": self.total_files,
                "per_rule": self.per_rule,
                "candidates": [c.as_dict() for c in self.candidates[:500]]}


@dataclass
class CleanResult:
    run_id: str
    tx_id: str
    moved: int
    bytes_moved: int
    failed: list[dict[str, Any]]
    vault_dir: str

    @property
    def summary(self) -> str:
        gb = self.bytes_moved / 1e9
        if self.failed:
            return f"{self.moved} item(s), {gb:.2f} GB in the vault · {len(self.failed)} skipped"
        return f"{self.moved} item(s), {gb:.2f} GB in the vault"

    def as_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "tx_id": self.tx_id, "moved": self.moved,
                "bytes_moved": self.bytes_moved, "failed": self.failed,
                "summary": self.summary, "restorable_until_days": DEFAULT_RETENTION_DAYS}


def _expand(pattern: str, roots: dict[str, str]) -> str:
    out = pattern
    for key, value in roots.items():
        out = out.replace("{" + key + "}", value)
    return os.path.expanduser(os.path.expandvars(out))


class Cleaner:
    def __init__(self, conn, roots: dict[str, str], vault_root: str, clock,
                 engine=None) -> None:
        self.conn = conn
        self.roots = roots                 # {"temp": "C:/…/Temp", "cache": "…"}
        self.vault_root = vault_root
        self.clock = clock
        self.engine = engine               # for the journal; optional in scans

    # -------------------------------------------------------------------- seed
    def seed_rules(self, rules: Iterable[Rule]) -> None:
        with self.conn:
            for r in rules:
                self.conn.execute(
                    "INSERT OR REPLACE INTO clean_rules(id,title,class,risk,patterns,"
                    "exempt,note,enabled) VALUES(?,?,?,?,?,?,?,?)",
                    (r.id, r.title, r.cls, r.risk, ";".join(r.patterns),
                     ";".join(r.exempt), r.note, int(r.enabled)))

    def rules(self, classes: set[str] | None = None) -> list[Rule]:
        classes = classes or {"free", "auto_regen", "policy"}
        rows = self.conn.execute(
            "SELECT id,title,class,risk,patterns,exempt,note,enabled FROM clean_rules "
            "ORDER BY class, id").fetchall()
        out = []
        for r in rows:
            rule = Rule(id=r[0], title=r[1], cls=r[2], risk=r[3],
                        patterns=tuple(p for p in (r[4] or "").split(";") if p),
                        exempt=tuple(p for p in (r[5] or "").split(";") if p),
                        note=r[6] or "", enabled=bool(r[7]))
            if rule.enabled and rule.cls in classes:
                out.append(rule)
        return out

    # -------------------------------------------------------------------- scan
    def scan(self, classes: set[str] | None = None) -> ScanResult:
        """Never deletes. Reports what *would* move, per rule."""
        classes = classes or {"free", "auto_regen"}
        result = ScanResult(candidates=[])
        seen: set[str] = set()          # a file belongs to exactly one rule
        for rule in self.rules(classes):
            rtotal, rcount = 0, 0
            for pattern in rule.patterns:
                path = _expand(pattern, self.roots)
                if any(_matches(path, _expand(x, self.roots)) for x in rule.exempt):
                    continue
                for file_path, size in _glob_walk(path):
                    if any(_matches(file_path, _expand(x, self.roots)) for x in rule.exempt):
                        continue
                    if file_path in seen:
                        continue
                    seen.add(file_path)
                    result.candidates.append(Candidate(rule.id, file_path, size))
                    rtotal += size
                    rcount += 1
            result.per_rule[rule.id] = {"bytes": rtotal, "files": rcount}
        return result

    # ------------------------------------------------------------------- clean
    def clean(self, candidates: list[Candidate], *, source: str = "ui") -> CleanResult:
        """Move candidates into the vault. Journaled, restorable, verified."""
        run_id = new_id()
        started = self.clock()
        vault_dir = os.path.join(self.vault_root, run_id)
        os.makedirs(vault_dir, exist_ok=True)

        moved = 0
        bytes_moved = 0
        failed: list[dict[str, Any]] = []
        tx_id = ""

        # Every clean run is a Guardian transaction, so it appears in history and
        # can be reverted with the same machinery as any other change.
        if self.engine is not None:
            tx_id = new_id()
            self.engine.journal.ensure_transaction(
                tx_id, kind="clean", source=source,
                note=f"clean {len(candidates)} item(s)")

        with self.conn:
            self.conn.execute(
                "INSERT INTO clean_runs(id,tx_id,started_at,bytes_found,rule_ids,receipt_json) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, tx_id, started, sum(c.size for c in candidates),
                 ",".join(sorted({c.rule_id for c in candidates})), "{}"))

        for c in candidates:
            try:
                if not os.path.exists(c.path):
                    continue
                size = os.path.getsize(c.path)
                digest = _sha256(c.path)
                rel = f"{c.rule_id}/{hashlib.sha1(c.path.encode()).hexdigest()[:16]}"
                dest = os.path.join(vault_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(c.path, dest)
                if _sha256(dest) != digest:
                    shutil.move(dest, c.path)
                    raise OSError("verification failed after move; file restored")
                moved += 1
                bytes_moved += size
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO vault_items(id,run_id,rule_id,original_path,vault_path,"
                        "size,sha256,moved_at) VALUES(?,?,?,?,?,?,?,?)",
                        (new_id(), run_id, c.rule_id, c.path, dest, size, digest,
                         self.clock()))
            except (OSError, shutil.Error) as exc:
                failed.append({"path": c.path, "error": str(exc)})

        receipt = {
            "run_id": run_id, "tx_id": tx_id, "moved": moved,
            "bytes_moved": bytes_moved, "failed": failed,
            "vault_dir": vault_dir,
            "retention_days": DEFAULT_RETENTION_DAYS,
        }
        with self.conn:
            self.conn.execute(
                "UPDATE clean_runs SET finished_at=?,bytes_moved=?,files=?,receipt_json=? "
                "WHERE id=?", (self.clock(), bytes_moved, moved, _json(receipt), run_id))
            self.conn.execute(
                "INSERT INTO receipts(id,at,kind,title,summary,detail_json,tx_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (new_id(), self.clock(), "clean", "Cleaner",
                 f"{moved} item(s), {bytes_moved/1e9:.2f} GB restorable for "
                 f"{DEFAULT_RETENTION_DAYS} days", _json(receipt), tx_id))

        # Close the transaction, or recovery would see an open one at next boot
        # and try to "repair" a clean run that in fact finished perfectly.
        if self.engine is not None and tx_id:
            self.engine._close_tx(tx_id, "committed")
            self.engine.journal.append(tx_id=tx_id, tweak_id="cleaner", phase="applied",
                                       prestate={}, poststate={"files": moved})

        return CleanResult(run_id=run_id, tx_id=tx_id, moved=moved,
                           bytes_moved=bytes_moved, failed=failed, vault_dir=vault_dir)

    # ----------------------------------------------------------------- restore
    def restore(self, run_id: str) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT id,original_path,vault_path,size,sha256 FROM vault_items "
            "WHERE run_id=? AND restored_at IS NULL", (run_id,)).fetchall()
        restored, failed = 0, []
        for vid, original, vaulted, size, digest in rows:
            try:
                os.makedirs(os.path.dirname(original), exist_ok=True)
                shutil.move(vaulted, original)
                if _sha256(original) != digest:
                    failed.append({"path": original, "error": "hash mismatch after restore"})
                    continue
                restored += 1
                with self.conn:
                    self.conn.execute(
                        "UPDATE vault_items SET restored_at=? WHERE id=?", (self.clock(), vid))
            except (OSError, shutil.Error) as exc:
                failed.append({"path": original, "error": str(exc)})
        with self.conn:
            self.conn.execute("UPDATE receipts SET undone_at=? WHERE detail_json LIKE ?",
                              (self.clock(), f'%"run_id":"{run_id}"%'))
        return {"run_id": run_id, "restored": restored, "failed": failed}

    def vault_summary(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size),0) FROM vault_items WHERE restored_at IS NULL"
        ).fetchone()
        runs = self.conn.execute(
            "SELECT id,started_at,bytes_moved,files FROM clean_runs ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
        return {"items": row[0], "bytes": row[1],
                "runs": [{"run_id": r[0], "at": r[1], "bytes": r[2], "files": r[3]}
                         for r in runs]}

    def purge_expired(self, days: int = DEFAULT_RETENTION_DAYS) -> int:
        cutoff = self.clock() - days * 86_400_000
        rows = self.conn.execute(
            "SELECT id,vault_path FROM vault_items WHERE restored_at IS NULL AND moved_at < ?",
            (cutoff,)).fetchall()
        n = 0
        for vid, vaulted in rows:
            try:
                if os.path.exists(vaulted):
                    os.remove(vaulted)
                with self.conn:
                    self.conn.execute("DELETE FROM vault_items WHERE id=?", (vid,))
                n += 1
            except OSError:
                pass
        return n


# ------------------------------------------------------------------- helpers

def _glob_root(pattern: str) -> str:
    """The longest literal directory prefix of a glob: '/t/temp/*.tmp' -> '/t/temp'."""
    parts = pattern.split(os.sep)
    literal = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        literal.append(part)
    root = os.sep.join(literal)
    return root or os.sep


def _glob_walk(pattern: str):
    """Yield (path, size) for real files matching a glob pattern.

    `os.path.exists()` cannot answer a glob question — testing it on the pattern
    itself is always False, which silently turned every rule into a no-op.
    """
    import glob
    root = _glob_root(pattern)
    if any(ch in root for ch in "*?["):
        candidates = glob.glob(pattern, recursive=True)
    elif os.path.isdir(root):
        candidates = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    else:
        candidates = []
    for full in candidates:
        if os.path.isfile(full):
            try:
                yield full, os.path.getsize(full)
            except OSError:
                continue


def _walk(root: str):
    if os.path.isfile(root):
        yield root, os.path.getsize(root)
        return
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            try:
                yield full, os.path.getsize(full)
            except OSError:
                continue


def _matches(path: str, pattern: str) -> bool:
    import fnmatch
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
