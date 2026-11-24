"""Value backends.

A backend is the only thing that touches the real machine. Everything above it
(Catalog, Simulator, Engine, Verifier) is pure logic and therefore testable on
any OS — which is exactly how this was developed on Linux for a Windows product.

Rust port: a trait, `trait Backend { fn read(&self, addr: &str) -> Option<Value>; ... }`
implemented by `WindowsBackend` (registry/services/tasks/display/power) and
`FakeBackend` (used by the whole test suite).
"""
from __future__ import annotations

import copy
import json
from typing import Any, Iterable, Protocol


class BackendError(RuntimeError):
    pass


class Backend(Protocol):
    def read(self, address: str) -> Any: ...
    def exists(self, address: str) -> bool: ...
    def write(self, address: str, value: Any, *, value_name: str | None = None) -> None: ...
    def keys(self, prefix: str = "") -> Iterable[str]: ...


class FakeBackend:
    """In-memory machine state. Deterministic, serialisable, scriptable.

    This is not a toy: the entire test suite runs against it, including the
    crash/power-loss scenarios, because those are impossible to test reliably
    on a real machine.
    """

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._state: dict[str, Any] = dict(initial or {})
        self.writes: list[tuple[str, Any]] = []
        self.fail_on_write: dict[str, str] = {}   # address -> error message
        self.on_write = None                       # optional callback(engine, address)
        self._pinned: dict[str, Any] = {}          # values an external authority enforces

    # -- read -------------------------------------------------------------
    @staticmethod
    def _key(address: str, value_name: str | None) -> str:
        """`address` alone is a key; `address\\value` is a value inside it.

        This is how Windows actually stores Explorer preferences, so modelling it
        is what lets the demo machine prove the same behaviour the real one has.
        """
        return address if value_name is None else f"{address}\\{value_name}"

    def read(self, address: str, *, value_name: str | None = None) -> Any:
        key = self._key(address, value_name)
        if key not in self._state:
            raise BackendError(f"address does not exist: {key}")
        return copy.deepcopy(self._state[key])

    def exists(self, address: str, *, value_name: str | None = None) -> bool:
        return self._key(address, value_name) in self._state

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._state if k.startswith(prefix))

    # -- write ------------------------------------------------------------
    def write(self, address: str, value: Any, *, value_name: str | None = None) -> None:
        if address in self.fail_on_write:
            raise BackendError(self.fail_on_write[address])
        if self.on_write is not None:
            self.on_write(self, address)
        key = self._key(address, value_name)
        self._state[key] = copy.deepcopy(value)
        self.writes.append((key, copy.deepcopy(value)))

    def settled_read(self, address: str, *, value_name: str | None = None) -> Any:
        """The value *after* policy has had its say. Tests use `pin()` to model
        a group policy / vendor agent re-imposing a value after our write."""
        key = self._key(address, value_name)
        if key in self._pinned:
            return copy.deepcopy(self._pinned[key])
        return self.read(address, value_name=value_name)

    def pin(self, address: str, value: Any, *, value_name: str | None = None) -> None:
        """Model an external authority that always wins."""
        self._pinned[self._key(address, value_name)] = copy.deepcopy(value)

    def unpin(self, address: str, *, value_name: str | None = None) -> None:
        self._pinned.pop(self._key(address, value_name), None)

    # -- helpers for tests -------------------------------------------------
    def snapshot(self) -> str:
        return json.dumps(self._state, sort_keys=True)

    def poke(self, address: str, value: Any) -> None:
        """Simulate 'the outside world' (e.g. Windows Update) changing state."""
        self._state[address] = copy.deepcopy(value)

    def remove(self, address: str) -> None:
        self._state.pop(address, None)
