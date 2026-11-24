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
    # SMBv1 support flag, at the address Windows really uses
    "registry:hklm\\system\\currentcontrolset\\services\\lanmanserver\\parameters": 1,
    # where Windows actually keeps the diagnostic-data level
    "registry:hklm\\software\\policies\\microsoft\\windows\\datacollection": "full",
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


@pytest.fixture()
def service(tmp_path, engine, clock):
    """The whole app, wired, on a temp database. Shared by every suite so a module
    test and a bridge test exercise the same object graph."""
    from pco_engine import FakeBackend
    from pco_engine.service import DEMO_STATE, Service
    return Service(str(tmp_path / "svc.db"), backend=FakeBackend(DEMO_STATE),
                   catalog_dir=CATALOG_DIR, vault_root=str(tmp_path / "vault"),
                   clock=clock)


# --------------------------------------------------------- scratch-space hygiene
# pytest creates a numbered directory per test under /tmp/pytest-of-<user> and
# never deletes them. Three runs of this suite left 6.9 GB behind, at which point
# unrelated tests started failing with "OSError: [Errno 28] No space left on
# device" — a red suite caused by the previous green one. Keep the last few runs
# for debugging and delete the rest.
def pytest_sessionfinish(session, exitstatus):
    import glob
    import shutil
    base = os.environ.get("PYTEST_DEBUG_TEMPROOT") or "/tmp"
    runs = sorted(glob.glob(os.path.join(base, "pytest-of-*", "pytest-*")),
                  key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    for stale in runs[:-3]:
        shutil.rmtree(stale, ignore_errors=True)
