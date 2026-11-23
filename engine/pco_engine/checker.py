"""The Checker — PC Checkup.

Design rule that makes this different from every other optimizer: findings are
split into three categories that never borrow each other's credibility.

    performance  — measurably affects speed / responsiveness
    storage      — costs disk, not speed
    security     — posture and risk, not speed
    privacy / reliability — same idea

A finding in `storage` can never push the Speed score down, and the UI says so.
That single constraint is why "freeing 42 GB will not make your games faster"
appears in the product instead of the opposite.

Every check is a pure function of a probe: deterministic, explainable, testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .ids import new_id

CATEGORIES = {"performance", "storage", "security", "privacy", "reliability"}
SEVERITY_WEIGHT = {"high": 3.0, "medium": 1.6, "low": 0.6, "info": 0.0}


@dataclass
class Finding:
    check_id: str
    category: str
    severity: str
    title: str
    plain: str
    evidence: dict[str, Any]
    auto_safe: bool
    fix_tweaks: list[str] = field(default_factory=list)
    impact: str = ""
    id: str = field(default_factory=lambda: new_id())

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "check_id": self.check_id, "category": self.category,
            "severity": self.severity, "title": self.title, "plain": self.plain,
            "evidence": self.evidence, "impact": self.impact,
            "auto_safe": self.auto_safe, "fix_tweaks": self.fix_tweaks,
        }


@dataclass
class CheckResult:
    run_id: str
    findings: list[Finding]
    scores: dict[str, int]
    total: int
    checks_run: int

    @property
    def fixable_safe(self) -> list[Finding]:
        return [f for f in self.findings if f.auto_safe and f.fix_tweaks]

    @property
    def needs_review(self) -> list[Finding]:
        return [f for f in self.findings if not f.auto_safe]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "scores": self.scores, "total": self.total,
            "checks_run": self.checks_run,
            "findings": [f.as_dict() for f in self.findings],
            "counts": {
                "total": len(self.findings),
                "safe": len(self.fixable_safe),
                "review": len(self.needs_review),
            },
        }


# --------------------------------------------------------------------- probing

class Probe:
    """Read-only view of the machine used by checks.

    Reads go through the same Backend as everything else, plus a few shell
    measurements a real build would take with ETW/PDH. On the Windows backend
    those are real; in the demo they are modelled.
    """

    def __init__(self, backend, metrics: dict[str, Any] | None = None) -> None:
        self.backend = backend
        self.metrics = metrics or {}

    def get(self, address: str, default: Any = None) -> Any:
        try:
            return self.backend.read(address)
        except Exception:
            return default

    def metric(self, name: str, default: Any = None) -> Any:
        return self.metrics.get(name, default)


# ---------------------------------------------------------------------- checks

@dataclass(frozen=True)
class Check:
    id: str
    module: str
    category: str
    severity: str
    title: str
    fn: Callable[[Probe], tuple[bool, str, dict[str, Any]] | None]
    fix_tweaks: tuple[str, ...] = ()
    auto_safe: bool = False
    impact: str = ""


def _refresh_check(p: Probe):
    mode = p.get("display:primary.mode")
    if not isinstance(mode, dict):
        return None
    hz = float(mode.get("hz", 60))
    supported = float(mode.get("supported_hz") or p.get("display:primary.supported_hz") or hz)
    if supported <= hz + 1:
        return None
    return True, (
        f"Your monitor is running at {hz:.0f} Hz but supports {supported:.0f} Hz.",
        {"current_hz": hz, "max_hz": supported},
    )


CHECKS: list[Check] = [
    Check("display.refresh.below_max", "display", "performance", "high",
          "Monitor is below its maximum refresh rate", _refresh_check,
          fix_tweaks=("display.set_refresh",), auto_safe=True,
          impact="Up to 2.7x smoother motion — the largest free win on most PCs"),

    Check("startup.count", "startup", "performance", "high",
          "Many programs start with Windows",
          lambda p: _startup(p),
          fix_tweaks=("startup.defer.onedrive", "startup.defer.steam"), auto_safe=True,
          impact="Faster login and a quieter first minute"),

    Check("storage.junk", "cleaner", "storage", "medium",
          "Reclaimable junk files",
          lambda p: _junk(p), auto_safe=False,
          impact="Frees disk space. Does not make the PC faster — stated plainly."),

    Check("storage.duplicates", "cleaner", "storage", "medium",
          "Duplicate files",
          lambda p: _dups(p), auto_safe=False,
          impact="Frees disk space only, never speed"),

    Check("power.scheme", "power", "performance", "low",
          "Power plan is holding the CPU back",
          lambda p: _power(p), fix_tweaks=("power.scheme.high",), auto_safe=True,
          impact="Small but real: the CPU stops parking cores under load"),

    Check("security.defender.off", "security", "security", "high",
          "Real-time protection is disabled",
          lambda p: _defender(p), auto_safe=False,
          impact="Risk, not speed. This app will never turn security off for a benchmark."),

    Check("security.smb1", "security", "security", "medium",
          "SMBv1 is enabled",
          lambda p: _smb1(p), fix_tweaks=("security.smb1.disable",), auto_safe=True,
          impact="Closes a legacy attack surface almost nothing needs"),

    Check("privacy.telemetry", "privacy", "privacy", "low",
          "Windows telemetry at the full level",
          lambda p: _telemetry(p), fix_tweaks=("privacy.telemetry.minimum",),
          auto_safe=False,
          impact="Privacy only. The UI separates this from performance on purpose."),

    Check("reliability.crashes", "reliability", "reliability", "medium",
          "Repeated application crashes",
          lambda p: _crashes(p), auto_safe=False,
          impact="Stability signal, correlated with the tweak journal"),
]


def _startup(p: Probe):
    items = p.get("startup:items")
    if not isinstance(items, list):
        return None
    heavy = [i for i in items if i.get("enabled", True)]
    if len(heavy) < 5:
        return None
    cost = sum(float(i.get("cost_ms", 0)) for i in heavy)
    return True, (
        f"{len(heavy)} programs start with Windows, costing about {cost/1000:.1f} s of login time.",
        {"count": len(heavy), "cost_ms": cost,
         "items": [i.get("name") for i in heavy][:12]},
    )


def _junk(p: Probe):
    bytes_ = p.metric("storage.junk_bytes")
    if not bytes_ or bytes_ < 1_000_000_000:
        return None
    return True, (f"{bytes_/1e9:.1f} GB of junk can be reclaimed.", {"bytes": bytes_})


def _dups(p: Probe):
    d = p.metric("storage.dup_bytes")
    if not d or d < 500_000_000:
        return None
    return True, (f"{d/1e9:.1f} GB is duplicated files.", {"bytes": d})


def _power(p: Probe):
    scheme = p.get("power:active_scheme")
    if scheme != "balanced":
        return None
    return True, ("The Balanced power plan parks CPU cores under load.",
                  {"scheme": scheme})


def _defender(p: Probe):
    rt = p.get("security:defender.realtime")
    if rt is not False:
        return None
    return True, ("Real-time protection is off — this is a security risk, not a speed win.",
                  {"realtime": False})


def _smb1(p: Probe):
    v = p.get("security:smb1.enabled")
    if v is not True:
        return None
    return True, ("The 1990s SMBv1 protocol is enabled.", {"smb1": True})


TELEMETRY_ADDRESS = "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection"


def _telemetry(p: Probe):
    level = p.get(TELEMETRY_ADDRESS)
    if level not in ("full", 3):
        return None
    return True, ("Diagnostics are set to the full level.", {"level": level})


def _crashes(p: Probe):
    n = p.metric("reliability.crash_count_7d", 0)
    if not n or n < 3:
        return None
    return True, (f"{n} application crashes in the last 7 days.", {"crashes": n})


# ---------------------------------------------------------------------- runner

class Checker:
    def __init__(self, conn, probe: Probe, clock) -> None:
        self.conn = conn
        self.probe = probe
        self.clock = clock
        self._seed()

    def _seed(self) -> None:
        with self.conn:
            for c in CHECKS:
                self.conn.execute(
                    "INSERT OR REPLACE INTO checks(id,module,category,title,plain,severity,"
                    "fix_tweaks,auto_safe,weight) VALUES(?,?,?,?,?,?,?,?,?)",
                    (c.id, c.module, c.category, c.title, c.impact or c.title,
                     c.severity, ",".join(c.fix_tweaks), int(c.auto_safe),
                     SEVERITY_WEIGHT.get(c.severity, 1.0)))

    # ------------------------------------------------------------------- run
    def run(self, *, trigger: str = "user") -> CheckResult:
        run_id = new_id()
        started = self.clock()
        findings: list[Finding] = []
        checks_run = 0

        for c in CHECKS:
            checks_run += 1
            try:
                hit = c.fn(self.probe)
            except Exception:  # a broken check must never break the whole scan
                continue
            if not hit:
                continue
            plain, evidence = _normalize(hit)
            findings.append(Finding(
                check_id=c.id, category=c.category, severity=c.severity,
                title=c.title, plain=plain, evidence=evidence,
                auto_safe=c.auto_safe, fix_tweaks=list(c.fix_tweaks),
                impact=c.impact))

        scores = {"performance": 100, "security": 100, "privacy": 100, "reliability": 100}
        for f in findings:
            if f.category in scores:
                scores[f.category] -= int(round(SEVERITY_WEIGHT.get(f.severity, 1.0) * 4))
        scores = {k: max(0, v) for k, v in scores.items()}
        total = max(0, min(100, int(round(sum(scores.values()) / len(scores)))))

        with self.conn:
            self.conn.execute(
                "INSERT INTO check_runs(id,started_at,finished_at,trigger,score_speed,"
                "score_security,score_privacy,score_reliability,score_total,checks_run) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, started, self.clock(), trigger, scores["performance"],
                 scores["security"], scores["privacy"], scores["reliability"],
                 total, checks_run))
            for f in findings:
                self.conn.execute(
                    "INSERT INTO findings(id,run_id,check_id,category,severity,title,plain,"
                    "evidence_json,impact,auto_safe,fix_tweaks,state,first_seen,last_seen) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,'new',?,?)",
                    (f.id, run_id, f.check_id, f.category, f.severity, f.title, f.plain,
                     _json(f.evidence), f.impact, int(f.auto_safe),
                     ",".join(f.fix_tweaks), started, started))

        return CheckResult(run_id=run_id, findings=findings, scores=scores,
                           total=total, checks_run=checks_run)

    # ---------------------------------------------------------------- queries
    def latest_run(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id,started_at,score_speed,score_security,score_privacy,"
            "score_reliability,score_total,checks_run FROM check_runs "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {"run_id": row[0], "at": row[1],
                "scores": {"performance": row[2], "security": row[3],
                           "privacy": row[4], "reliability": row[5]},
                "total": row[6], "checks_run": row[7]}

    def findings_of(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,check_id,category,severity,title,plain,evidence_json,impact,"
            "auto_safe,fix_tweaks,state FROM findings WHERE run_id=? ORDER BY severity, category",
            (run_id,)).fetchall()
        import json
        return [{
            "id": r[0], "check_id": r[1], "category": r[2], "severity": r[3],
            "title": r[4], "plain": r[5], "evidence": json.loads(r[6]),
            "impact": r[7], "auto_safe": bool(r[8]),
            "fix_tweaks": [t for t in (r[9] or "").split(",") if t], "state": r[10],
        } for r in rows]

    def mark_fixed(self, finding_ids: list[str], tx_id: str) -> int:
        n = 0
        with self.conn:
            for fid in finding_ids:
                cur = self.conn.execute(
                    "UPDATE findings SET state='fixed', tx_id=? WHERE id=?", (tx_id, fid))
                n += cur.rowcount
        return n


def _normalize(hit: Any) -> tuple[str, dict[str, Any]]:
    """Checks may return (plain, evidence), (ok, plain, evidence) or a bare str.

    Accepting all three shapes is deliberate: a check author should not have to
    remember a tuple arity, and a wrong-shape return must fail loudly here rather
    than silently dropping a finding.
    """
    if isinstance(hit, str):
        return hit, {}
    if isinstance(hit, tuple):
        if len(hit) == 3:                       # legacy (ok, plain, evidence)
            _ok, hit = hit[0], hit[1:]
        if len(hit) == 2:
            plain, evidence = hit
            if isinstance(evidence, dict) or evidence is None:
                return str(plain), dict(evidence or {})
            # (ok, (plain, evidence)) — the double-wrapped shape
            if isinstance(plain, bool):
                inner_plain, inner_ev = evidence
                return str(inner_plain), dict(inner_ev or {})
    raise TypeError(f"check returned an unusable shape: {hit!r}")


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
