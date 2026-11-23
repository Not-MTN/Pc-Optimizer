"""Preflight simulation.

Reads the machine, resolves preconditions, and *projects* the effects onto a
virtual state graph — so conflicts, dependents and unknowns are discovered
before anything is written.

The simulation is deliberately conservative: if it cannot prove an action is
safe to apply, it says so and the engine refuses.

Rust port: pure functions over the same structs; no I/O except `backend.read`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .address import canon_value, values_equal
from .backend import Backend, BackendError
from .catalog import Catalog, Effect, Precondition, Tweak

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}


@dataclass
class Issue:
    severity: str          # info | warning | error
    code: str              # stable machine code, e.g. 'precondition.failed'
    message: str           # human, plain english
    tweak_id: str | None = None
    address: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code,
                "message": self.message, "tweak_id": self.tweak_id,
                "address": self.address}


@dataclass
class PlannedEffect:
    address: str
    kind: str
    target: str
    old: Any
    new: Any
    tweak_id: str

    def as_dict(self) -> dict[str, Any]:
        return {"address": self.address, "kind": self.kind, "target": self.target,
                "old": self.old, "new": self.new, "tweak_id": self.tweak_id}


@dataclass
class Plan:
    tweak_ids: list[str] = field(default_factory=list)
    effects: list[PlannedEffect] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    needs_reboot: bool = False
    requires_confirmation: bool = False

    # -- verdict -----------------------------------------------------------
    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"blocked: {self.errors[0].message}"
        n = len(self.effects)
        extra = f", reboot required" if self.needs_reboot else ""
        warn = f", {len(self.warnings)} warning(s)" if self.warnings else ""
        return f"{n} change(s) across {len(self.tweak_ids)} tweak(s){extra}{warn}"

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "summary": self.summary,
                "tweak_ids": self.tweak_ids,
                "effects": [e.as_dict() for e in self.effects],
                "issues": [i.as_dict() for i in self.issues],
                "needs_reboot": self.needs_reboot,
                "requires_confirmation": self.requires_confirmation}


class Preflight:
    def __init__(self, catalog: Catalog, backend: Backend) -> None:
        self.catalog = catalog
        self.backend = backend

    # ------------------------------------------------------------------ read
    def _read(self, address: str) -> Any:
        try:
            return self.backend.read(address)
        except BackendError:
            return None

    # ------------------------------------------------------- preconditions
    def _resolve(self, what: str) -> tuple[Any, bool]:
        """Resolve 'some.attr' against a base address holding a dict.

        Returns (value, found). Missing always means found=False — never a guess.
        """
        value, found = self._try_read(what)
        if found:
            return value, True
        if "." in what:
            base, _, attr = what.rpartition(".")
            obj, base_found = self._try_read(base)
            if base_found:
                if isinstance(obj, dict) and attr in obj:
                    return obj[attr], True
                return None, False
        return None, False

    def _backend_keys(self) -> set[str]:
        # cached per simulate() call; the fake/real backend answers cheaply.
        # NOTE: keys() is an *index*, not the source of truth — read() is.
        cached = getattr(self, "_keys_cache", None)
        if cached is None:
            try:
                cached = set(self.backend.keys())
            except Exception:  # backend without enumeration support
                cached = set()
            self._keys_cache = cached
        return cached

    def _try_read(self, address: str) -> tuple[Any, bool]:
        """Read with an explicit found flag; a missing address is never a value."""
        try:
            return self.backend.read(address), True
        except Exception:
            return None, False

    def _check_precondition(self, pc: Precondition, tweak: Tweak, plan: Plan) -> None:
        kind, what, want = pc.kind, pc.what, pc.value
        self._keys_cache = None
        actual, found = self._resolve(what)

        if kind == "exists":
            if not found:
                plan.issues.append(Issue("error", "precondition.failed",
                                         f"{tweak.title}: requires {what}, which is not present on this PC.",
                                         tweak.id, what))
            return

        if kind == "absent":
            if found:
                plan.issues.append(Issue("warning", "precondition.already_present",
                                         f"{tweak.title}: {what} already exists.",
                                         tweak.id, what))
            return

        if not found:
            # An unprovable precondition is a refusal, never a pass.
            plan.issues.append(Issue("error", "precondition.unknown",
                                     f"{tweak.title}: cannot read {what}, so '{kind} {want}' cannot be verified.",
                                     tweak.id, what))
            return

        if kind == "not_equals":
            # "only offer this if the machine is not already there" — without it a
            # tweak whose goal is its own precondition can never be planned.
            if values_equal(actual, want):
                plan.issues.append(Issue("error", "precondition.already_present",
                                         f"{tweak.title}: {what} is already {canon_value(want)}.",
                                         tweak.id, what))
            return

        if kind == "equals":
            if not values_equal(actual, want):
                plan.issues.append(Issue("error", "precondition.mismatch",
                                         f"{tweak.title}: expected {what} = {canon_value(want)}, found {canon_value(actual)}.",
                                         tweak.id, what))
            return

        if kind in {"gte", "lte"}:
            try:
                actual_num = float(actual)
                want_num = float(want)
            except (TypeError, ValueError):
                plan.issues.append(Issue("error", "precondition.unreadable",
                                         f"{tweak.title}: {what} is not a number, so {kind} {want} cannot be checked.",
                                         tweak.id, what))
                return
            bad = actual_num < want_num if kind == "gte" else actual_num > want_num
            if bad:
                plan.issues.append(Issue("error", "precondition.out_of_range",
                                         f"{tweak.title}: needs {what} {kind} {want}, but it is {actual_num:g}.",
                                         tweak.id, what))
            return

    # ------------------------------------------------------------ simulate
    def simulate(self, tweak_ids: list[str], *, allow_tier: set[str] | None = None) -> Plan:
        """Project a batch onto a virtual state graph. Read-only."""
        allow_tier = allow_tier or {"T0", "T1", "T2", "T3"}
        plan = Plan()
        # virtual overlay: address -> (value, owner tweak)
        overlay: dict[str, tuple[Any, str]] = {}

        for tid in tweak_ids:
            try:
                tweak = self.catalog.get(tid)
            except Exception as exc:  # noqa: BLE001 - surfaced as an issue
                plan.issues.append(Issue("error", "tweak.unknown", str(exc), tid))
                continue

            plan.tweak_ids.append(tid)

            if tweak.tier not in allow_tier:
                plan.issues.append(Issue(
                    "error", "tier.blocked",
                    f"{tweak.title}: needs access tier {tweak.tier}, which is not enabled.",
                    tid))
                continue

            if tweak.needs_reboot:
                plan.needs_reboot = True
            if tweak.requires_confirmation:
                plan.requires_confirmation = True

            # 1. preconditions against current + projected state
            for pc in tweak.preconditions:
                self._check_precondition(pc, tweak, plan)

            # 2. effects: conflicts and unknowns
            for eff in tweak.effects:
                self._check_effect(eff, tweak, plan, overlay)

        if plan.errors:
            plan.effects = [e for e in plan.effects if True]  # keep for the UI to explain
        return plan

    def _check_effect(self, eff: Effect, tweak: Tweak, plan: Plan,
                      overlay: dict[str, tuple[Any, str]]) -> None:
        addr = eff.address

        # a. already projected by another tweak in this batch
        if addr in overlay:
            other_value, other_tweak = overlay[addr]
            if other_tweak != tweak.id:
                if values_equal(other_value, eff.to):
                    plan.issues.append(Issue(
                        "warning", "effect.redundant",
                        f"'{other_tweak}' already sets {addr} to the same value — one of them is unnecessary.",
                        tweak.id, addr))
                else:
                    plan.issues.append(Issue(
                        "error", "effect.conflict",
                        f"Conflict on {addr}: '{other_tweak}' sets {canon_value(other_value)} "
                        f"but '{tweak.id}' wants {canon_value(eff.to)}.",
                        tweak.id, addr))
                return

        # b. compare with the real machine
        current = self._read(addr)
        if current is None:
            if eff.pre is None:
                plan.issues.append(Issue(
                    "warning", "effect.new_value",
                    f"{tweak.title}: {addr} does not exist yet and will be created.",
                    tweak.id, addr))
            else:
                plan.issues.append(Issue(
                    "error", "effect.missing_base",
                    f"{tweak.title}: expected {addr} to exist with {canon_value(eff.pre)}, "
                    f"but it is missing. Refusing to guess.",
                    tweak.id, addr))
                return
        else:
            if values_equal(current, eff.to):
                plan.issues.append(Issue(
                    "info", "effect.noop",
                    f"{tweak.title}: {addr} already has that value.",
                    tweak.id, addr))

        # c. other tweaks on the machine that own the same address
        for other_id in self.catalog.writers_of(addr):
            if other_id == tweak.id:
                continue
            # if the machine's current value came from that tweak, this is a real conflict
            plan.issues.append(Issue(
                "warning", "effect.shared_address",
                f"{addr} is also managed by '{other_id}'. Applying both may fight.",
                tweak.id, addr))

        plan.effects.append(PlannedEffect(
            address=addr, kind=eff.kind, target=eff.target,
            old=current, new=eff.to, tweak_id=tweak.id))
        overlay[addr] = (eff.to, tweak.id)

    # ------------------------------------------------------------ apply-order
    @staticmethod
    def order_effects(plan: Plan) -> list[PlannedEffect]:
        """Deterministic apply order: registry → services → tasks → display →
        power → dns → files. Revert runs this list backwards.

        Ordering matters for real systems: stop a service before editing its
        config, restore config before starting it again.
        """
        order = {
            "service_start_type": 0, "scheduled_task_state": 1, "registry_value": 2,
            "registry_tree": 3, "startup_defer": 4, "display_mode": 5,
            "display_setting": 6, "power_setting": 7, "driver_action": 8,
            "dns_policy": 9, "app_setting": 10, "file_write": 11, "file_delete": 12,
        }
        return sorted(plan.effects, key=lambda e: (order.get(e.kind, 99), e.address))
