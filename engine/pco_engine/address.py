"""Canonical addresses.

Every effect resolves to one string address. That single decision makes
conflict detection, drift detection, snapshots and the managed projection all
the same operation: compare (address, value) pairs.

Rust port: same module, `fn address_of(effect: &Effect) -> String`.
"""
from __future__ import annotations

import json
from typing import Any


class AddressError(ValueError):
    pass


def norm(value: str) -> str:
    """Registry/service paths are case-insensitive on Windows; compare lowercased."""
    return value.strip().lower()


def address_of(kind: str, target: str) -> str:
    if not isinstance(target, str) or not target:
        raise AddressError(f"effect target must be a non-empty string, got {target!r}")
    t = norm(target)

    if kind == "registry_value":
        return f"registry:{t}"
    if kind == "registry_tree":
        return f"registry:{t}"
    if kind == "service_start_type":
        return f"service:{t}.start_type"
    if kind == "scheduled_task_state":
        return f"task:{t}.state"
    if kind in ("display_mode", "display_setting"):
        # target is the monitor role, e.g. "primary"
        return f"display:{t}.mode"
    if kind == "power_setting":
        return f"power:{t}"
    if kind == "dns_policy":
        return f"dns:{t}.mode"
    if kind == "file_write" or kind == "file_delete":
        return f"file:{target.strip()}"
    if kind == "driver_action":
        return f"driver:{t}.state"
    if kind == "startup_defer":
        return f"startup:{t}.defer_seconds"
    if kind == "app_setting":
        return f"app:{t}"
    raise AddressError(f"unknown effect kind: {kind}")


def canon_value(value: Any) -> str:
    """Stable JSON encoding so identical values always compare equal."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def values_equal(a: Any, b: Any) -> bool:
    return canon_value(a) == canon_value(b)
