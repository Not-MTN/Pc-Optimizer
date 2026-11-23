"""The Standing Benchmark — closes the honesty loop.

Every claim this product makes eventually answers one question: *did it
actually help?* That requires a baseline taken on the same machine, with enough
statistics to say "no measurable change" when that is the truth.

The benchmark here is small and deterministic (CPU integer/float, memory copy,
SQLite write, file IO). It is not a gaming benchmark — it is a *regression*
detector, which is what a tweak needs.
"""
from __future__ import annotations

import hashlib
import math
import os
import platform
import sqlite3
import statistics
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable

from .ids import new_id


def _cpu_int(seconds: float = 0.25) -> float:
    """Deterministic integer loop; returns iterations/second."""
    end = time.perf_counter() + seconds
    n = 0
    x = 0
    while time.perf_counter() < end:
        for _ in range(10_000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        n += 10_000
    return n / max(1e-9, (seconds)) if x >= 0 else 0.0


def _cpu_float(seconds: float = 0.25) -> float:
    end = time.perf_counter() + seconds
    total = 0.0
    n = 0
    while time.perf_counter() < end:
        for i in range(5_000):
            total += math.sqrt(i) * 1.0001
        n += 5_000
    return n / max(1e-9, seconds) if total >= 0 else 0.0


def _mem_copy(seconds: float = 0.2) -> float:
    size = 8 * 1024 * 1024
    src = bytearray(size)
    dst = bytearray(size)
    end = time.perf_counter() + seconds
    n = 0
    while time.perf_counter() < end:
        dst[:] = src
        n += 1
    return (n * size) / max(1e-9, seconds) / 1e6     # MB/s


def _sqlite_write(tmp_dir: str, seconds: float = 0.2) -> float:
    path = os.path.join(tmp_dir, "bench.db")
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS b(i INTEGER, s TEXT)")
    conn.execute("BEGIN")
    end = time.perf_counter() + seconds
    n = 0
    while time.perf_counter() < end:
        conn.execute("INSERT INTO b(i,s) VALUES(?,?)", (n, "x" * 40))
        n += 1
    conn.execute("COMMIT")
    conn.close()
    try:
        os.remove(path)
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass
    except OSError:
        pass
    return n / max(1e-9, seconds)                   # rows/s


def _file_io(tmp_dir: str, seconds: float = 0.2) -> float:
    path = os.path.join(tmp_dir, "bench.bin")
    chunk = os.urandom(1 << 20)
    end = time.perf_counter() + seconds
    n = 0
    with open(path, "wb") as fh:
        while time.perf_counter() < end:
            fh.write(chunk)
            n += 1
    with open(path, "rb") as fh:
        while fh.read(1 << 20):
            pass
    os.remove(path)
    return (n * 1) / max(1e-9, seconds)             # MB/s written


METRICS: dict[str, Callable[..., float]] = {
    "cpu.int_ops": _cpu_int,
    "cpu.float_ops": _cpu_float,
    "mem.copy": _mem_copy,
    "disk.sqlite": _sqlite_write,
    "disk.write": _file_io,
}
UNITS = {"cpu.int_ops": "ops/s", "cpu.float_ops": "ops/s", "mem.copy": "MB/s",
         "disk.sqlite": "rows/s", "disk.write": "MB/s"}


@dataclass
class BenchResult:
    id: str
    score: float
    metrics: dict[str, float]
    durations_ms: dict[str, int]
    stable: bool
    env: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "score": round(self.score, 2), "metrics":
                {k: round(v, 1) for k, v in self.metrics.items()},
                "durations_ms": self.durations_ms, "stable": self.stable,
                "env": self.env}


class Bench:
    def __init__(self, conn, clock, tmp_dir: str | None = None) -> None:
        self.conn = conn
        self.clock = clock
        self.tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="pco-bench-")

    def _env(self) -> dict[str, Any]:
        return {"platform": platform.platform(), "cpu": platform.processor() or "unknown",
                "python": platform.python_version()}

    def run(self, *, reps: int = 3, metrics: list[str] | None = None) -> BenchResult:
        keys = metrics or list(METRICS)
        samples: dict[str, list[float]] = {k: [] for k in keys}
        durations: dict[str, int] = {}

        for k in keys:
            fn = METRICS[k]
            takes_dir = k.startswith("disk.")
            for _ in range(reps):
                t0 = time.perf_counter()
                value = fn(self.tmp_dir) if takes_dir else fn()
                durations[k] = int((time.perf_counter() - t0) * 1000)
                samples[k].append(float(value))

        medians = {k: statistics.median(v) for k, v in samples.items()}
        # spread check: anything over 12% variation is not a stable environment
        stable = True
        for k, vals in samples.items():
            if len(vals) > 1:
                mean = statistics.fmean(vals) or 1.0
                spread = statistics.pstdev(vals) / mean
                if spread > 0.12:
                    stable = False

        # normalised score: geometric mean of relative-to-typical (log scale so
        # one slow metric cannot dominate)
        typical = {"cpu.int_ops": 2.0e6, "cpu.float_ops": 1.2e6, "mem.copy": 4000.0,
                   "disk.sqlite": 9000.0, "disk.write": 500.0}
        ratios = [max(1e-6, medians[k] / typical[k]) for k in keys if k in typical]
        score = 100.0 * math.exp(sum(math.log(r) for r in ratios) / len(ratios)) if ratios else 0.0

        bid = new_id()
        import json
        with self.conn:
            self.conn.execute(
                "INSERT INTO benches(id,at,duration_ms,score,detail_json,env_json) "
                "VALUES(?,?,?,?,?,?)",
                (bid, self.clock(), sum(durations.values()), score,
                 json.dumps({"medians": medians, "samples": samples}),
                 json.dumps(self._env())))
        return BenchResult(id=bid, score=score, metrics=medians,
                           durations_ms=durations, stable=stable, env=self._env())

    # -------------------------------------------------------------- comparison
    def compare(self, a_id: str, b_id: str) -> dict[str, Any]:
        import json
        rows = {r[0]: json.loads(r[1]) for r in self.conn.execute(
            "SELECT id,detail_json FROM benches WHERE id IN (?,?)", (a_id, b_id))}
        if len(rows) < 2:
            raise KeyError("unknown bench id")
        a, b = rows[a_id]["medians"], rows[b_id]["medians"]
        deltas = {}
        for k in a:
            if k not in b or a[k] == 0:
                continue
            pct = (b[k] - a[k]) / a[k] * 100.0
            verdict = "no measurable change" if abs(pct) < 3.0 else (
                "faster" if pct > 0 else "slower")
            deltas[k] = {"from": round(a[k], 1), "to": round(b[k], 1),
                         "pct": round(pct, 1), "verdict": verdict,
                         "unit": UNITS.get(k, "")}
        changed = [k for k, d in deltas.items() if d["verdict"] != "no measurable change"]
        return {"a": a_id, "b": b_id, "metrics": deltas,
                "verdict": "no measurable change" if not changed else
                           (f"{len(changed)} metric(s) changed", changed[0])}

    def baseline(self) -> dict[str, Any] | None:
        import json
        row = self.conn.execute(
            "SELECT id,score,detail_json,at FROM benches ORDER BY at ASC, rowid ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        d = json.loads(row[2])
        return {"id": row[0], "score": row[1], "metrics": d["medians"], "at": row[3]}

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,at,score,duration_ms FROM benches ORDER BY at DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"id": r[0], "at": r[1], "score": round(r[2], 2), "duration_ms": r[3]}
                for r in rows]
