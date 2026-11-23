import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pco_engine import (  # noqa: E402
    Catalog, Engine, FakeBackend, parse_tweak, connect, migrate,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG_DIR = os.path.join(HERE, "..", "catalog")

BASE_STATE = {
    "display:primary.mode": {"hz": 59.94, "width": 2560, "height": 1440},
    "display:primary.supported_hz": 165,
    "service:diagtrack.start_type": "auto",
    "service:diagtrack.exists": True,
    "power:active_scheme": "balanced",
    "registry:hklm\\software\\acme\\telemetry": 1,
    "registry:hklm\\software\\acme\\updater": 1,
    "startup:onedrive.defer_seconds": 0,
    "startup:steam.defer_seconds": 0,
    "task:\\microsoft\\office\\officebackgroundtaskhandler.state": "ready",
    "dns:*": "off",
    "file:c:\\temp\\junk.tmp": "x" * 10,
}


class Clock:
    """Deterministic, monotonic clock for reproducible tests."""

    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def tick(self, ms: int = 1000) -> int:
        self.now += ms
        return self.now


@pytest.fixture()
def catalog() -> Catalog:
    return Catalog.load_dir(CATALOG_DIR)


@pytest.fixture()
def backend() -> FakeBackend:
    return FakeBackend(BASE_STATE)


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def engine(tmp_path, backend, catalog, clock) -> Engine:
    db = str(tmp_path / "pco.db")
    conn = connect(db)
    migrate(conn, clock=clock)
    return Engine(conn, backend, catalog, clock=clock, confirm_window_ms=60_000)


def make_tweak(**overrides):
    base = {
        "id": "test.example",
        "module": "test",
        "title": "Test tweak",
        "plain": "Does a test thing",
        "tier": "T1",
        "risk": "low",
        "effects": [{"kind": "registry_value",
                     "target": "HKLM\\Software\\Acme\\Telemetry", "to": 0}],
    }
    base.update(overrides)
    return parse_tweak(base)
