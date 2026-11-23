"""PC Optimizer engine — Guardian core.

This package is the reference implementation of the safety engine specified in
SAFETY.md. It is written to be ported to Rust line-for-line; the module, class
and method names are the contract.

Nothing here talks to Windows directly: `Backend` is the only seam, which is
why the entire test suite runs on any OS.
"""
from .address import AddressError, address_of, canon_value, values_equal
from .backend import Backend, BackendError, FakeBackend
from .catalog import Catalog, CatalogError, Effect, Precondition, Tweak, parse_tweak
from .engine import Applied, Engine, EngineError, PreflightBlocked, Reverted
from .ids import new_id
from .journal import GENESIS, Entry, Journal, JournalError, compute_checksum
from .preflight import Issue, Plan, PlannedEffect, Preflight
from .snapshots import Snapshot, SnapshotStore
from .store import SCHEMA_VERSION, connect, integrity, migrate

__all__ = [
    "AddressError", "address_of", "canon_value", "values_equal",
    "Backend", "BackendError", "FakeBackend",
    "Catalog", "CatalogError", "Effect", "Precondition", "Tweak", "parse_tweak",
    "Applied", "Engine", "EngineError", "PreflightBlocked", "Reverted",
    "new_id",
    "GENESIS", "Entry", "Journal", "JournalError", "compute_checksum",
    "Issue", "Plan", "PlannedEffect", "Preflight",
    "Snapshot", "SnapshotStore",
    "SCHEMA_VERSION", "connect", "integrity", "migrate",
]

__version__ = "0.1.0"
