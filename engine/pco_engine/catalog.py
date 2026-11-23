"""Tweak catalog: loading, validation, and the in-memory index.

A tweak declares what it does; it never *does* it. Keeping declarations pure
data is what makes preflight simulation, conflict detection and the knowledge
base possible at all.

Document format (`tweak.v1`) is specified in SCHEMA.md §12.1.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from .address import AddressError, address_of

VALID_TIERS = {"T0", "T1", "T2", "T3"}
VALID_RISK = {"none", "low", "medium", "high", "cosmetic"}
VALID_EFFECTS = {
    "registry_value", "registry_tree", "service_start_type", "scheduled_task_state",
    "display_mode", "display_setting", "power_setting", "dns_policy",
    "file_write", "file_delete", "driver_action", "startup_defer", "app_setting",
}
VALID_PRECONDITIONS = {"exists", "absent", "equals", "gte", "lte", "true"}


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Effect:
    kind: str
    target: str
    to: Any
    pre: Any = None

    @property
    def address(self) -> str:
        return address_of(self.kind, self.target)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "to": self.to, "pre": self.pre}


@dataclass(frozen=True)
class Precondition:
    kind: str
    what: str
    value: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "what": self.what, "value": self.value}


@dataclass(frozen=True)
class Tweak:
    id: str
    module: str
    title: str
    plain: str
    tier: str
    risk: str
    effects: tuple[Effect, ...]
    preconditions: tuple[Precondition, ...] = ()
    revert: tuple[Effect, ...] = ()
    needs_reboot: bool = False
    measure: dict[str, Any] | None = None
    blast_radius: str | None = None
    knowledge_key: str | None = None
    source_file: str | None = None
    catalog_version: int = 1

    @property
    def addresses(self) -> tuple[str, ...]:
        return tuple(e.address for e in self.effects)

    @property
    def requires_confirmation(self) -> bool:
        return self.risk in {"high"} or self.tier in {"T3"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "module": self.module, "title": self.title,
            "plain": self.plain, "tier": self.tier, "risk": self.risk,
            "effects": [e.as_dict() for e in self.effects],
            "preconditions": [p.as_dict() for p in self.preconditions],
            "revert": [e.as_dict() for e in self.revert],
            "needs_reboot": self.needs_reboot, "measure": self.measure,
            "blast_radius": self.blast_radius, "knowledge_key": self.knowledge_key,
            "catalog_version": self.catalog_version,
        }


def _parse_effect(raw: dict[str, Any], tweak_id: str) -> Effect:
    if "kind" not in raw or "target" not in raw or "to" not in raw:
        raise CatalogError(f"{tweak_id}: effect needs kind/target/to: {raw!r}")
    kind = raw["kind"]
    if kind not in VALID_EFFECTS:
        raise CatalogError(f"{tweak_id}: unknown effect kind {kind!r}")
    try:
        return Effect(kind=kind, target=raw["target"], to=raw["to"], pre=raw.get("pre"))
    except AddressError as exc:
        raise CatalogError(f"{tweak_id}: {exc}") from exc


def _parse_precondition(raw: Any, tweak_id: str) -> Precondition:
    if isinstance(raw, str):
        # shorthand: "!address" means absent, "address" means exists
        if raw.startswith("!"):
            return Precondition("absent", raw[1:])
        return Precondition("exists", raw)
    if not isinstance(raw, dict) or "kind" not in raw:
        raise CatalogError(f"{tweak_id}: bad precondition {raw!r}")
    if raw["kind"] not in VALID_PRECONDITIONS:
        raise CatalogError(f"{tweak_id}: unknown precondition kind {raw['kind']!r}")
    return Precondition(kind=raw["kind"], what=raw.get("what", ""), value=raw.get("value"))


def parse_tweak(raw: dict[str, Any], source_file: str | None = None) -> Tweak:
    tid = raw.get("id")
    if not tid or not isinstance(tid, str):
        raise CatalogError(f"tweak without id in {source_file}")
    for required in ("module", "title", "plain"):
        if not raw.get(required):
            raise CatalogError(f"{tid}: missing {required}")
    tier = raw.get("tier", "T1")
    if tier not in VALID_TIERS:
        raise CatalogError(f"{tid}: bad tier {tier!r}")
    risk = raw.get("risk", "low")
    if risk not in VALID_RISK:
        raise CatalogError(f"{tid}: bad risk {risk!r}")

    effects = tuple(_parse_effect(e, tid) for e in raw.get("effects") or [])
    if not effects and not raw.get("script"):
        raise CatalogError(f"{tid}: has no effects")

    # duplicate addresses inside one tweak is always a bug
    addrs = [e.address for e in effects]
    if len(set(addrs)) != len(addrs):
        raise CatalogError(f"{tid}: duplicate effect addresses {addrs}")

    return Tweak(
        id=tid,
        module=raw["module"],
        title=raw["title"],
        plain=raw["plain"],
        tier=tier,
        risk=risk,
        effects=effects,
        preconditions=tuple(_parse_precondition(p, tid) for p in raw.get("preconditions") or []),
        revert=tuple(_parse_effect(e, tid) for e in raw.get("revert") or []),
        needs_reboot=bool(raw.get("needs_reboot", False)),
        measure=raw.get("measure"),
        blast_radius=raw.get("blast_radius"),
        knowledge_key=raw.get("knowledge_key"),
        source_file=source_file,
        catalog_version=int(raw.get("catalog_version", 1)),
    )


class Catalog:
    """Immutable index of tweaks. Loaded once at startup."""

    def __init__(self, tweaks: Iterable[Tweak]) -> None:
        self._by_id: dict[str, Tweak] = {}
        self._by_address: dict[str, list[str]] = {}
        for t in tweaks:
            if t.id in self._by_id:
                raise CatalogError(f"duplicate tweak id: {t.id}")
            self._by_id[t.id] = t
            for addr in t.addresses:
                self._by_address.setdefault(addr, []).append(t.id)

    # -- queries -----------------------------------------------------------
    def get(self, tweak_id: str) -> Tweak:
        try:
            return self._by_id[tweak_id]
        except KeyError as exc:
            raise CatalogError(f"unknown tweak: {tweak_id}") from exc

    def has(self, tweak_id: str) -> bool:
        return tweak_id in self._by_id

    def all(self) -> list[Tweak]:
        return sorted(self._by_id.values(), key=lambda t: t.id)

    def writers_of(self, address: str) -> list[str]:
        return list(self._by_address.get(address, []))

    def addresses(self) -> set[str]:
        return set(self._by_address)

    def __len__(self) -> int:
        return len(self._by_id)

    # -- loading -----------------------------------------------------------
    @classmethod
    def load_dir(cls, path: str) -> "Catalog":
        tweaks: list[Tweak] = []
        for root, _dirs, files in os.walk(path):
            for name in sorted(files):
                if not name.endswith(".json"):
                    continue
                full = os.path.join(root, name)
                with open(full, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                items = raw if isinstance(raw, list) else raw.get("tweaks", [raw])
                for item in items:
                    tweaks.append(parse_tweak(item, source_file=full))
        return cls(tweaks)
