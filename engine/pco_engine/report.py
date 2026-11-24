"""Health Report — the document you hand to a warranty tech or a buyer.

Everything in it already exists in the database; the value is the *narrative*.
A technician does not want 400 rows, they want: what this machine is, what is
wrong with it, what was changed and by whom, and the evidence behind it.

Export formats are deliberately local-only: Markdown (readable, diffable) and a
print-ready HTML document. Nothing is uploaded, and there is no share link.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SECTION_ORDER = ("machine", "verdict", "findings", "history", "bench", "storage",
                 "security", "peripherals", "appendix")


@dataclass
class Report:
    generated_at: int
    sections: dict[str, Any]
    title: str = "PC Health Report"

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "generated_at": self.generated_at,
                "sections": self.sections}


def build(service: Any, *, title: str = "PC Health Report") -> Report:
    """Assemble from live state. Read-only: generating a report changes nothing."""
    st = service.status()
    ck = st.get("checkup") or {}
    findings = []
    if ck.get("run_id"):
        findings = service.checker.findings_of(ck["run_id"])
    receipts = service.receipts(50)
    gaming = service.gaming.sessions(5)
    bench = st.get("bench") or []
    space = service.space.grew(7) if hasattr(service, "space") else {"available": False}
    periph = service.peripheral.summary() if hasattr(service, "peripheral") else {}
    vault = st.get("cleaner") or {}

    machine = {
        "os": "Windows 11 (24H2+)",
        "engine": "PC Optimizer reference engine",
        "schema_version": __import__("pco_engine.store", fromlist=["SCHEMA_VERSION"]).SCHEMA_VERSION,
        "journal_rows": st["engine"]["journal_rows"],
        "journal_verified": st["engine"]["journal_ok"],
        "managed_settings": st["engine"]["managed"],
    }

    verdict = {
        "score": ck.get("total"),
        "scores": ck.get("scores", {}),
        "headline": _verdict_line(ck),
        "findings_total": len(findings),
        "critical": [f["title"] for f in findings if f.get("severity") == "high"],
    }

    history = [{"at": r["at"], "what": r["title"], "result": r["summary"],
                "undone": r["undone"], "tx": r["tx_id"]} for r in receipts]

    storage = {
        "vault_bytes": vault.get("bytes", 0),
        "vault_items": vault.get("items", 0),
        "growth": space.get("summary") if space.get("available") else "no samples",
        "changed": space.get("changed", []) if space.get("available") else [],
    }

    security = {
        "smb1": _finding_text(findings, "security.smb1"),
        "defender": _finding_text(findings, "security.defender.off"),
        "telemetry": _finding_text(findings, "privacy.telemetry"),
        "blocker": st.get("dns", {}),
    }

    peripherals = periph or {}

    bench_section = {
        "latest": {"score": bench[0]["score"], "at": bench[0]["at"]} if bench else None,
        "baseline": ({"score": st["baseline"]["score"]} if st.get("baseline") else None),
        "sessions": [{"game": g["game"], "grade": g["grade"],
                      "fps_1pct": g["stats"].get("fps_p1")} for g in gaming],
    }

    appendix = {
        "how_this_was_made": (
            "Every change this app makes is written to an append-only, hash-chained "
            "journal *before* the machine is touched. The chain in this report was "
            "verified at generation time."),
        "privacy": "This document contains no personal data and never left the device.",
        "evidence": {"journal_ok": st["engine"]["journal_ok"],
                     "db_integrity": st["integrity"]},
    }

    sections = {"machine": machine, "verdict": verdict,
                "findings": findings, "history": history,
                "bench": bench_section, "storage": storage,
                "security": security, "peripherals": peripherals,
                "appendix": appendix}
    return Report(generated_at=service.clock(), sections=sections, title=title)


def _verdict_line(ck: dict[str, Any]) -> str:
    if not ck:
        return "No checkup has been run on this machine."
    total = ck.get("total", 0)
    if total >= 90:
        return "This PC is in good shape."
    if total >= 75:
        return "This PC is healthy but has a few fixable issues."
    if total >= 55:
        return "This PC needs attention in several areas."
    return "This PC has significant issues."


def _finding_text(findings: list[dict[str, Any]], prefix: str) -> str:
    hit = next((f for f in findings if f["check_id"] == prefix), None)
    return hit["plain"] if hit else "not detected"


# --------------------------------------------------------------------- render
def to_markdown(report: Report) -> str:
    s = report.sections
    out: list[str] = [f"# {report.title}", ""]
    out.append(f"Generated {_when(report.generated_at)} · local-only document")
    out.append("")
    out.append("## Machine")
    for k, v in s["machine"].items():
        out.append(f"- **{k.replace('_', ' ')}**: {v}")
    out += ["", "## Verdict", f"**{s['verdict']['headline']}**", ""]
    if s["verdict"]["score"] is not None:
        out.append(f"- Overall score: **{s['verdict']['score']}/100**")
        for k, v in s["verdict"]["scores"].items():
            out.append(f"  - {k}: {v}")
        out.append(f"- Findings: {s['verdict']['findings_total']}")
        if s["verdict"]["critical"]:
            out.append("- Needs attention:")
            out += [f"  - {t}" for t in s["verdict"]["critical"]]

    out += ["", "## Findings"]
    if not s["findings"]:
        out.append("_None._")
    for f in s["findings"]:
        out.append(f"- **{f['title']}** ({f['category']}, {f['severity']}) — {f['plain']}")

    out += ["", "## What changed on this PC"]
    if not s["history"]:
        out.append("_Nothing recorded._")
    for h in s["history"]:
        mark = " *(undone)*" if h["undone"] else ""
        out.append(f"- {_when(h['at'])} — {h['what']}: {h['result']}{mark}")

    out += ["", "## Benchmark"]
    b = s["bench"]
    if b["latest"]:
        out.append(f"- Latest score: **{round(b['latest']['score'])}**")
    if b["baseline"]:
        out.append(f"- Baseline: {round(b['baseline']['score'])}")
    for g in b["sessions"]:
        out.append(f"- {g['game']}: grade {g['grade']}, 1% low "
                   f"{round(g['fps_1pct'] or 0)} fps")

    out += ["", "## Storage"]
    out.append(f"- Vault: {s['storage']['vault_items']} item(s), "
               f"{s['storage']['vault_bytes']/1e9:.2f} GB restorable")
    out.append(f"- Growth: {s['storage']['growth']}")

    out += ["", "## Security & privacy"]
    for k, v in s["security"].items():
        if k != "blocker":
            out.append(f"- {k}: {v}")
    d = s["security"].get("blocker", {})
    out.append(f"- Ad/tracker blocker: {d.get('mode', 'n/a')} "
               f"({d.get('blocked_total', 0)} blocked)")

    if s["peripherals"]:
        out += ["", "## Peripherals"]
        out.append(f"- {s['peripherals'].get('verdict', 'not tested')}")

    out += ["", "## Appendix", s["appendix"]["how_this_was_made"], "",
            f"_{s['appendix']['privacy']}_"]
    return "\n".join(out) + "\n"


def to_html(report: Report) -> str:
    """A self-contained printable document: no external CSS, no scripts."""
    md = to_markdown(report).splitlines()
    body: list[str] = []
    for line in md:
        if line.startswith("# "):
            body.append(f"<h1>{_esc(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{_esc(line[3:])}</h2>")
        elif line.startswith("- "):
            body.append(f"<li>{_esc(line[2:]).replace('**', '')}</li>")
        elif line.strip():
            body.append(f"<p>{_esc(line).replace('**', '')}</p>")
    css = """
    :root{color-scheme:light}
    body{font:13px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;
         margin:32px auto;padding:0 22px;color:#12161c}
    h1{font-size:24px;margin:0 0 4px;letter-spacing:-.02em}
    h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#66707e;
       margin:26px 0 6px;border-bottom:1px solid #e6e9ee;padding-bottom:4px}
    li{margin:2px 0} p{margin:4px 0}
    @media print{body{margin:0}h2{page-break-after:avoid}}
    """
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{_esc(report.title)}</title><style>{css}</style></head>"
            f"<body>{''.join(body)}</body></html>")


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _when(ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
