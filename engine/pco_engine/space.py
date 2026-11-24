"""Space — time travel for your disk.

A single "C: is 87% full" number tells you nothing actionable. What a person
needs is *direction*: what grew, by how much, and when you will run out.

This module keeps cheap daily folder-size samples and answers three questions:

    grew(7)         what changed this week, ranked
    forecast()      at the current rate, when do I hit 90%?
    explain(path)   which rule or cache owns the growth

Samples are cheap by construction: it stores folder totals, not file lists, and
skips folders it has no business walking (WinSxS, System Volume Information).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

from .ids import new_id

DAY_MS = 86_400_000

# the standard Windows trees, and which of them are safe to measure cheaply
DEFAULT_ROOTS = {
    "Downloads": "{user}/Downloads",
    "Documents": "{user}/Documents",
    "Games": "{user}/Games",
    "AppData": "{user}/AppData/Local",
    "Program Files": "{programs}",
    "Temp": "{temp}",
}

SKIP = ("winsxs", "system volume information", "$recycle.bin", "node_modules",
        ".git", "windows\\installer")


def _expand(path: str, roots: dict[str, str]) -> str:
    out = path
    for k, v in roots.items():
        out = out.replace("{" + k + "}", v)
    return os.path.expanduser(os.path.expandvars(out))


def folder_size(path: str, *, budget_files: int = 200_000) -> tuple[int, int]:
    """(bytes, files). Bounded so one huge tree cannot hang a background sample."""
    if not os.path.isdir(path):
        return 0, 0
    total = files = 0
    for dirpath, dirs, names in os.walk(path):
        low = dirpath.lower()
        if any(s in low for s in SKIP):
            dirs[:] = []
            continue
        for name in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
                files += 1
            except OSError:
                continue
            if files >= budget_files:
                return total, files
    return total, files


@dataclass
class Sample:
    id: str
    at: int
    folders: dict[str, dict[str, int]]     # folder -> {bytes, files}
    free_bytes: int
    total_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "at": self.at, "folders": self.folders,
                "free_bytes": self.free_bytes, "total_bytes": self.total_bytes}


class Space:
    def __init__(self, conn, clock, *, roots: dict[str, str] | None = None) -> None:
        self.conn = conn
        self.clock = clock
        self.roots = roots or DEFAULT_ROOTS

    # ------------------------------------------------------------------ sampling
    def sample(self, *, paths: dict[str, str] | None = None) -> Sample:
        paths = paths or self.roots
        folders: dict[str, dict[str, int]] = {}
        total = 0
        for label, pattern in paths.items():
            b, n = folder_size(_expand(pattern, self.roots))
            folders[label] = {"bytes": b, "files": n}
            total += b
        free, size = _disk_free(paths)
        s = Sample(id=new_id(), at=self.clock(), folders=folders,
                   free_bytes=free, total_bytes=size)
        with self.conn:
            self.conn.execute(
                "INSERT INTO space_samples(id,at,folders_json,free_bytes,total_bytes) "
                "VALUES(?,?,?,?,?)",
                (s.id, s.at, _json(s.folders), s.free_bytes, s.total_bytes))
        return s

    def samples(self, limit: int = 60) -> list[Sample]:
        rows = self.conn.execute(
            "SELECT id,at,folders_json,free_bytes,total_bytes FROM space_samples "
            "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [Sample(r[0], r[1], _load(r[2]), r[3], r[4]) for r in rows]

    # ------------------------------------------------------------------- answers
    def grew(self, days: int = 7) -> dict[str, Any]:
        """What changed, ranked. The question people actually ask."""
        rows = self.conn.execute(
            "SELECT id,at,folders_json,free_bytes,total_bytes FROM space_samples "
            "ORDER BY at DESC LIMIT 200").fetchall()
        if not rows:
            return {"available": False, "reason": "no samples yet — run one"}
        now = Sample(rows[0][0], rows[0][1], _load(rows[0][2]), rows[0][3], rows[0][4])
        cutoff = now.at - days * DAY_MS
        older = [r for r in rows[1:] if r[1] <= cutoff]
        before = (Sample(older[0][0], older[0][1], _load(older[0][2]),
                         older[0][3], older[0][4]) if older
                  else Sample(rows[-1][0], rows[-1][1], _load(rows[-1][2]),
                              rows[-1][3], rows[-1][4]))
        deltas = []
        for label, cur in now.folders.items():
            prev = before.folders.get(label, {"bytes": 0, "files": 0})
            delta = cur["bytes"] - prev["bytes"]
            if delta:
                deltas.append({"folder": label, "delta_bytes": delta,
                               "now_bytes": cur["bytes"],
                               "pct": round(100 * delta / max(1, prev["bytes"]), 1)})
        deltas.sort(key=lambda d: -abs(d["delta_bytes"]))
        span = max(1, (now.at - before.at) // DAY_MS)
        return {"available": True, "days": span, "changed": deltas,
                "summary": _summary(deltas, span),
                "free_now": now.free_bytes, "total": now.total_bytes}

    def forecast(self, *, threshold: float = 0.90, days: int = 30) -> dict[str, Any]:
        grew = self.grew(days)
        if not grew.get("available"):
            return {"available": False, "reason": grew.get("reason")}
        used_now = grew["total"] - grew["free_now"]
        slope = sum(d["delta_bytes"] for d in grew["changed"]) / max(1, grew["days"])
        limit = grew["total"] * threshold
        if slope <= 0:
            return {"available": True, "slope_per_day": round(slope),
                    "verdict": "Disk usage is flat or shrinking — no projection needed.",
                    "days_left": None}
        remaining = limit - used_now
        days_left = max(0, int(remaining / slope))
        return {"available": True, "slope_per_day": round(slope),
                "threshold_pct": int(threshold * 100),
                "days_left": days_left,
                "verdict": (f"At the current rate you hit {int(threshold * 100)}% in "
                            f"{days_left} day(s)."),
                "free_now": grew["free_now"]}

    def explain(self, folder: str) -> dict[str, Any]:
        grew = self.grew(30)
        row = next((d for d in grew.get("changed", []) if d["folder"].lower()
                    == folder.lower()), None)
        if row is None:
            return {"folder": folder, "growth": 0,
                    "verdict": f"{folder} has not grown in the sampled window."}
        per_day = row["delta_bytes"] / max(1, grew["days"])
        return {"folder": folder, "growth_bytes": row["delta_bytes"],
                "per_day": round(per_day),
                "verdict": (f"{folder} grew {_gb(row['delta_bytes'])} "
                            f"({_gb(per_day)}/day)."),
                "next": ("Run the Cleaner with this folder in scope, or set a recipe: "
                         "'when this folder grows, tell me'.")}


def _summary(deltas: list[dict[str, Any]], span: int) -> str:
    if not deltas:
        return "Nothing grew measurably."
    top = deltas[0]
    verb = "grew" if top["delta_bytes"] > 0 else "shrank"
    return (f"{top['folder']} {verb} {_gb(abs(top['delta_bytes']))} in {span} day(s).")


def _gb(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if abs(n) >= 1e9 else f"{n / 1e6:.0f} MB"


def _disk_free(paths: dict[str, str]) -> tuple[int, int]:
    first = next(iter(paths.values()), "/")
    root = next((_expand(v, paths) for v in paths.values() if os.path.isdir(_expand(v, paths))),
                "/")
    try:
        st = os.statvfs(os.path.dirname(root.rstrip("/")) or "/")
        return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize
    except (OSError, AttributeError):
        import shutil
        total, used, free = shutil.disk_usage("/")
        return free, total


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load(text: str) -> Any:
    import json
    return json.loads(text)
