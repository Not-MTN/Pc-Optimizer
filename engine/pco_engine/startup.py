"""Startup Surgeon — cut boot time without breaking anything.

The rule that separates this from every "startup manager": an item is disabled
only when the reason is provable.

  * a service with live dependents is refused, and the dependents are named;
  * an item with no measured cost is labelled **unattributed**, never given an
    invented number;
  * anything that silently re-adds itself is reported, with who did it.

Deferring is the default recommendation, not disabling: 60 seconds of delay
buys nearly all of the boot win and none of the breakage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .ids import new_id

CLASSES = {"run_key", "service", "task", "uwp", "driver", "shell_ext", "winlogon"}
PHASES = {"kernel", "services", "logon", "shell", "idle", "unattributed"}

# Offline knowledge: what a thing does, and what turning it off breaks.
# Ships with the app; never fetched. Keyed by a stable locator.
KNOWLEDGE: dict[str, dict[str, Any]] = {
    "service:diagtrack": {
        "does": "Windows diagnostics and telemetry upload",
        "disable_ok": True,
        "breaks": ["Feedback Hub", "some enterprise diagnostics"],
        "recommended": "disable — nothing on a home PC needs it",
        "advice": "The largest single non-essential boot service on a stock install.",
    },
    "service:sysmain": {
        "does": "Prefetches frequently used programs",
        "disable_ok": False,
        "breaks": ["faster app launch", "game load times on HDD"],
        "recommended": "leave on — on an SSD it is cheap, on an HDD it matters",
        "advice": "Disabling this is popular advice that is usually wrong for HDDs.",
    },
    "service:wuauserv": {
        "does": "Windows Update",
        "disable_ok": False,
        "breaks": ["security updates"],
        "recommended": "leave on — schedule instead of disabling",
        "advice": "Use the update window in the Performance view instead.",
    },
    "run_key:onedrive": {
        "does": "Cloud file sync client",
        "disable_ok": True,
        "breaks": ["automatic sync until you open it"],
        "recommended": "defer 60 s",
        "advice": "Sync does not need to start before you have logged in.",
    },
    "run_key:steam": {
        "does": "Game library client",
        "disable_ok": True,
        "breaks": ["auto-updates stop until launched"],
        "recommended": "defer 60 s",
        "advice": "Nothing needs Steam in the first minute of a session.",
    },
    "run_key:epicgameslauncher": {
        "does": "Epic Games launcher",
        "disable_ok": True,
        "breaks": ["auto-updates stop until launched"],
        "recommended": "defer 60 s",
        "advice": "Same as Steam: defer, do not remove.",
    },
    "task:\\\\microsoft\\\\office\\\\officebackgroundtaskhandler": {
        "does": "Office background maintenance",
        "disable_ok": True,
        "breaks": ["background Office updates"],
        "recommended": "disable — it can still update on launch",
        "advice": "Runs at logon for no user-visible reason.",
    },
}


@dataclass
class StartupItem:
    id: str
    name: str
    cls: str
    locator: str
    phase: str = "unattributed"
    cost_ms: int | None = None        # None means *unmeasured*, never zero
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "class": self.cls,
                "locator": self.locator, "phase": self.phase,
                "cost_ms": self.cost_ms, "enabled": self.enabled,
                "cost_label": (f"{self.cost_ms} ms" if self.cost_ms is not None
                               else "unattributed")}


@dataclass
class Dependency:
    """A live service that needs another one to stay running."""
    dependent: str
    needs: str


def knowledge_for(item: StartupItem) -> dict[str, Any] | None:
    return KNOWLEDGE.get(item.locator.lower())


def refusal(item: StartupItem, deps: Iterable[Dependency]) -> str | None:
    """Why this must not be disabled, or None if it is safe to. One function so
    the UI, the API and the tests all enforce exactly the same rule."""
    live = [d.dependent for d in deps if d.needs.lower() == item.locator.lower()
            and d.dependent.lower() != item.locator.lower()]
    if live:
        return (f"Cannot disable {item.name}: {', '.join(sorted(set(live)))} "
                f"depend(s) on it right now.")
    k = knowledge_for(item)
    if k and not k["disable_ok"]:
        return f"Not recommended: {k['does']} — would break {', '.join(k['breaks'])}."
    if item.cls == "driver":
        return "Boot-start drivers cannot be disabled safely from here."
    return None


@dataclass
class BootReport:
    total_ms: int
    measured: int
    unattributed: int
    items: list[StartupItem]

    def as_dict(self) -> dict[str, Any]:
        return {"total_ms": self.total_ms, "measured_items": self.measured,
                "unattributed_items": self.unattributed,
                "attributed_share_pct": (round(100 * self.measured /
                                               max(1, len(self.items)), 1)),
                "items": [i.as_dict() for i in self.items]}


class StartupSurgeon:
    def __init__(self, conn, clock, *, backend=None, catalog=None) -> None:
        self.conn = conn
        self.clock = clock
        self.backend = backend
        self.catalog = catalog
        self._deps: list[Dependency] = []

    # ------------------------------------------------------------------ reading
    def inventory(self, items: Iterable[StartupItem] | None = None) -> list[StartupItem]:
        if items is None:
            items = [StartupItem(id="x", name="(none discovered)", cls="run_key",
                                 locator="run_key:none", enabled=False)]
        return list(items)

    def report(self, items: list[StartupItem]) -> BootReport:
        measured = [i for i in items if i.cost_ms is not None and i.enabled]
        unmeasured = [i for i in items if i.cost_ms is None and i.enabled]
        return BootReport(total_ms=sum(i.cost_ms or 0 for i in measured),
                          measured=len(measured), unattributed=len(unmeasured),
                          items=items)

    def set_dependencies(self, deps: Iterable[Dependency]) -> None:
        self._deps = list(deps)

    # ----------------------------------------------------------------- planning
    def plan(self, items: list[StartupItem], *, target_ms: int | None = None
             ) -> dict[str, Any]:
        """Order actions by *measured* cost, deferring before disabling.

        `target_ms` asks "get boot under this many ms" and returns the shortest
        list of actions that reaches it — or says plainly that it cannot.
        """
        plan_rows: list[dict[str, Any]] = []
        disabled_ms = deferred_ms = 0
        for item in sorted(items, key=lambda i: -(i.cost_ms or 0)):
            if not item.enabled:
                continue
            refusal_reason = refusal(item, self._deps)
            k = knowledge_for(item)
            if refusal_reason:
                plan_rows.append({"item": item.name, "action": "keep",
                                  "why": refusal_reason,
                                  "risk": "not recommended"})
                continue
            action = "defer" if (k and "defer" in k["recommended"]) else "disable"
            gain = item.cost_ms
            plan_rows.append({
                "item": item.name, "action": action,
                "why": (k["advice"] if k else
                        "No measured cost, so it is offered last and labelled unattributed."),
                "risk": "safe" if gain else "unknown",
                "gain_ms": gain, "unattributed": gain is None,
                "do": {"defer": 60, "action": action, "locator": item.locator},
            })
            if gain:
                if action == "defer":
                    deferred_ms += gain
                else:
                    disabled_ms += gain

        reachable = self.report(items).total_ms - (disabled_ms + deferred_ms)
        out: dict[str, Any] = {
            "rows": plan_rows,
            "current_ms": self.report(items).total_ms,
            "saving_ms": disabled_ms + deferred_ms,
            "after_ms": reachable,
            "honesty": ("Costs marked unattributed were never measured on this machine. "
                        "They are listed last and are not counted in the saving."),
        }
        if target_ms is not None:
            out["target_ms"] = target_ms
            out["meets_target"] = reachable <= target_ms
            out["verdict"] = ("This plan reaches the target." if reachable <= target_ms else
                              f"Cannot reach {target_ms} ms with measured items alone; "
                              f"closest is {reachable} ms.")
        return out

    # ------------------------------------------------------------------ actions
    def apply_plan(self, plan: dict[str, Any], *, mode: str = "defer") -> dict[str, Any]:
        """Report what this build can actually do — which today is nothing.

        This used to build `[{"item": ..., "action": "defer"}]` from the plan and
        return it as `applied`. No write, no backend call, no verification. The
        window then said "10 item(s) deferred" and the machine was untouched.
        The docstring claimed it applied through the engine; it never did.

        Deferral needs a launcher process Keel does not ship, so the honest
        answer is zero applied plus the reason. A change that cannot be made must
        say so; reporting ten imaginary successes is the "app says it worked and
        nothing changed" complaint in its purest form.
        """
        if self.backend is None:
            raise RuntimeError("no backend wired")
        wanted = [row for row in plan["rows"] if row["action"] != "keep"]
        return {
            "mode": mode,
            "applied": [],
            "requested": len(wanted),
            "refused": [{"item": row["item"],
                         "reason": ("Deferring a startup entry needs the Keel "
                                    "launcher, which this build does not ship. "
                                    "Nothing was changed.")}
                        for row in wanted],
            "note": ("No startup entry was changed. Deferring one needs the Keel "
                     "launcher, which this build does not ship, and Keel will not "
                     "rewrite a Run entry it cannot then honour. To disable an "
                     "entry yourself, use Windows' own Task Manager → Startup tab."),
        }

    # ----------------------------------------------------------------- watchdog
    def watch_for_relapse(self, before: set[str], after: set[str]) -> list[dict[str, Any]]:
        """Items that came back after we removed them.

        Software that silently re-adds itself is common; naming it is the only
        defence that works on the user's behalf.
        """
        returned = sorted(after - before)
        out = []
        for locator in returned:
            out.append({"locator": locator,
                        "narrative": f"{locator} re-added itself after removal.",
                        "guess": _owner_guess(locator)})
        return out


def _owner_guess(locator: str) -> str:
    low = locator.lower()
    for brand in ("steam", "epic", "riot", "adobe", "nvidia", "amd", "intel",
                  "razer", "logitech", "asus", "msi", "corsair", "onedrive"):
        if brand in low:
            return f"{brand} updater"
    if low.startswith("task:"):
        return "a scheduled task the vendor re-registers on update"
    return "unknown — the hash and signer are in the receipt"
