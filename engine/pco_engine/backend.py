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
    def write(self, address: str, value: Any) -> None: ...
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
    def read(self, address: str) -> Any:
        if address not in self._state:
            raise BackendError(f"address does not exist: {address}")
        return copy.deepcopy(self._state[address])

    def exists(self, address: str) -> bool:
        return address in self._state

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._state if k.startswith(prefix))

    # -- write ------------------------------------------------------------
    def write(self, address: str, value: Any) -> None:
        if address in self.fail_on_write:
            raise BackendError(self.fail_on_write[address])
        if self.on_write is not None:
            self.on_write(self, address)
        self._state[address] = copy.deepcopy(value)
        self.writes.append((address, copy.deepcopy(value)))

    def settled_read(self, address: str) -> Any:
        """The value *after* policy has had its say. Tests use `pin()` to model
        a group policy / vendor agent re-imposing a value after our write."""
        if address in self._pinned:
            return copy.deepcopy(self._pinned[address])
        return self.read(address)

    def pin(self, address: str, value: Any) -> None:
        """Model an external authority that always wins."""
        self._pinned[address] = copy.deepcopy(value)

    def unpin(self, address: str) -> None:
        self._pinned.pop(address, None)

    # -- helpers for tests -------------------------------------------------
    def snapshot(self) -> str:
        return json.dumps(self._state, sort_keys=True)

    def poke(self, address: str, value: Any) -> None:
        """Simulate 'the outside world' (e.g. Windows Update) changing state."""
        self._state[address] = copy.deepcopy(value)

    def remove(self, address: str) -> None:
        self._state.pop(address, None)
