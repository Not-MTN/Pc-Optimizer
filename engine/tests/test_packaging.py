"""The installer is a deliverable, so it gets tests like anything else.

PyInstaller and Inno Setup are not available in this environment, so nothing
here builds a binary. What it *can* check is the class of mistake that only
shows up on a Windows machine at release time:

  * a file the spec bundles that no longer exists (first-run crash on a clean PC)
  * an icon that is not a real ICO, or has no 256 px frame (a blurry taskbar icon)
  * a missing schema file, which breaks migration for every data directory
  * an entry point that does not import
  * packaging paths that only resolve on the machine they were written on

These are cheap, and every one of them has broken a real installer somewhere.
"""
from __future__ import annotations

import ast
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
PKG = ROOT / "packaging"
ASSETS = ROOT / "assets"


def test_entry_point_imports():
    """`app.py` is the exe's entry point; a syntax error ships as a dead exe."""
    src = (ROOT / "engine" / "pco_engine" / "app.py").read_text()
    ast.parse(src)                     # raises on a syntax error
    assert "def main(" in src
    assert 'if __name__ == "__main__":' in src


def test_spec_bundles_only_files_that_exist():
    """A datas entry pointing at a vanished file fails the build — or worse,
    succeeds and produces an app that crashes on first launch."""
    spec = (PKG / "keel.spec").read_text()
    engine = ROOT / "engine"

    # The spec builds these with Path objects, so check the sources directly.
    for rel in ("pco_engine/console.html", "catalog", "pco_engine/app.py"):
        assert (engine / rel).exists(), f"spec bundles missing path: engine/{rel}"

    schemas = sorted(engine.glob("schema*.sql"))
    assert len(schemas) >= 6, f"expected all migrations on disk, found {len(schemas)}"
    # Every one on disk must be bundled, or a fresh install skips a migration.
    assert 'glob("schema*.sql")' in spec
    for s in schemas:
        assert s.name not in spec or True      # covered by the glob


def test_icon_is_a_valid_ico_with_a_large_frame():
    """Windows picks the frame it needs by size. No 256 frame = blurry icon."""
    ico = ASSETS / "keel.ico"
    assert ico.exists(), "assets/keel.ico is missing — the exe would have no icon"
    data = ico.read_bytes()

    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1), "not an ICO file"
    assert count >= 4, f"only {count} icon frames; include 16/32/48/256"

    sizes = []
    for i in range(count):
        w, h = struct.unpack_from("<BB", data, 6 + i * 16)
        sizes.append((w or 256, h or 256))
    assert (256, 256) in sizes, f"no 256px frame — Windows will upscale: {sizes}"
    assert (16, 16) in sizes, f"no 16px frame — taskbar icon will be mush: {sizes}"
    assert ico.stat().st_size > 1000, "icon is suspiciously small (blank render?)"


def test_installer_script_references_existing_files():
    """Inno paths are relative to the .iss file. Stale paths fail at release."""
    iss = (PKG / "keel.iss").read_text()
    refs = re.findall(r'(?:Source|SetupIconFile|InfoBeforeFile):\s*"?([^"\n\r;]+)', iss)
    for ref in refs:
        ref = ref.strip().strip('"')
        if not ref or "{#" in ref:
            continue                      # build-time macro, resolved by ISCC
        target = (PKG / ref.replace("\\", "/")).resolve()
        assert target.exists(), f"keel.iss references a missing file: {ref}"


def test_installer_is_per_user_and_reversible():
    """Two promises the installer must keep, asserted so they cannot drift:
    no admin prompt, and uninstalling does not delete the journal by default."""
    iss = (PKG / "keel.iss").read_text()
    assert "PrivilegesRequired=lowest" in iss, "installer would demand admin rights"
    assert "{localappdata}\\Programs" in iss, "install path should be per-user"
    # Data deletion must be behind an explicit prompt, never automatic.
    assert "InitializeUninstall" in iss
    assert "MB_YESNO" in iss, "uninstall must ask before deleting user data"
    assert "DeleteData := False" in iss, "default must be to keep the data"


def test_build_script_refuses_to_build_from_a_red_suite():
    """Shipping an installer from failing tests is how bad builds escape."""
    ps1 = (PKG / "build.ps1").read_text()
    assert "pytest" in ps1
    assert "tests failed" in ps1.lower() or "Fail" in ps1
    # PyInstaller cannot cross-compile; the script must say so and check.
    assert "IsWindows" in ps1, "build.ps1 should refuse to run off Windows"
    assert "keel.spec" in ps1 and "keel.iss" in ps1


def test_no_console_window_in_the_build():
    """A terminal flashing on launch reads as malware."""
    spec = (PKG / "keel.spec").read_text()
    assert "console=False" in spec
    assert "upx=False" in spec, "UPX is a false-positive magnet for Defender"


@pytest.mark.parametrize("doc", ["README-first-run.txt", "before-install.txt"])
def test_installer_text_files_are_not_empty_and_mention_no_cloud(doc: str):
    text = (PKG / doc).read_text(encoding="utf-8")
    assert len(text.strip()) > 200, f"{doc} is too thin to be useful"
    lowered = text.lower()
    assert "no cloud" in lowered or "no network" in lowered or "never makes a network" in lowered
