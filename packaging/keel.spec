# Keel — PyInstaller build spec
#
#   pyinstaller --clean --noconfirm packaging/keel.spec
#
# One file, one window, no console. The result is dist/Keel.exe: ~12 MB, because
# the stdlib is most of it and there is no Pillow, no Qt, no Electron, no numpy.
#
# Deliberate choices:
#   * `console=False` — a PC optimizer that flashes a black terminal on launch
#     looks like malware. Errors go to %LOCALAPPDATA%\Keel\keel.log instead.
#   * pywebview is a *hidden* import: present in a normal build (it gives a real
#     window via the WebView2 runtime already on Windows 11), absent otherwise,
#     and app.py degrades to the browser rather than crashing.
#   * The SQL schema files and the catalog are data, not code — they must be
#     bundled or migrations fail on first run on a clean machine, which is the
#     single easiest way to ship an app that only works on the dev's PC.
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ENGINE = ROOT / "engine"

datas = [
    (str(ENGINE / "pco_engine" / "console.html"), "pco_engine"),
    (str(ENGINE / "catalog"), "catalog"),
]
datas += [(str(p), ".") for p in sorted(ENGINE.glob("schema*.sql"))]

hiddenimports = [
    "pco_engine.windows_backend",
    "pco_engine.providers",
    "pco_engine.shell_lab",
]

a = Analysis(
    [str(ENGINE / "pco_engine" / "app.py")],
    pathex=[str(ENGINE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Nothing here is imported, and each of these adds tens of MB.
        #
        # `distutils` and `setuptools` are deliberately NOT excluded. On Python
        # 3.12+ PyInstaller's own pre-safe-import hook aliases `distutils` to
        # `setuptools._distutils`, and excluding the alias target raises
        #   ValueError: Target module "distutils" already imported as
        #   ExcludedModule('distutils',)
        # which is an opaque failure inside step 4 of build.ps1. Neither is
        # imported by Keel, so they cost nothing when they stay in.
        "tkinter", "unittest", "pydoc_data", "test",
        "pip", "numpy", "pandas", "PIL", "PyQt5", "PySide6",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Keel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX trips Defender heuristics; the gain is ~2 MB
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "keel.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
