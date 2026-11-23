# Runs PC Optimizer from a fresh clone on Windows 11.
#
#   Set-ExecutionPolicy -Scope Process Bypass -Force   # once, this window only
#   .\run.ps1
#
# It creates a venv, installs pytest (the only dependency, test-only), starts the
# local console, and opens it. Nothing is installed system-wide, nothing is sent
# anywhere, and the default backend is the simulated machine.
#
# To point it at the real PC:
#   .\run.ps1 -Real
# ...which runs `pco --backend windows doctor` FIRST and refuses to start the
# console against real hardware until you have read what it can touch.

param(
    [switch]$Real,
    [switch]$Test,
    [int]$Port = 7788
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$engine = Join-Path $root "engine"
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"

function Say($msg) { Write-Host $msg }

# ---- Windows 11 only, and we check the build rather than trusting the label ----
if ($PSVersionTable.PSVersion.Major -lt 5) { throw "PowerShell 5+ required." }
$build = [System.Environment]::OSVersion.Version.Build
if ($build -lt 22000) {
    Write-Host "This build targets Windows 11 (build 22000+). This PC reports $build." -ForegroundColor Yellow
    $answer = Read-Host "Continue anyway? (y/N)"
    if ($answer -ne "y") { exit 1 }
}

# ---- venv ----
if (-not (Test-Path $py)) {
    Say "Creating a local virtual environment (.venv) - nothing system-wide."
    python -m venv $venv
}
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet pytest

if ($Test) {
    Say "Running the test suite..."
    Push-Location $engine
    try { & $py -m pytest -q } finally { Pop-Location }
    exit $LASTEXITCODE
}

if ($Real) {
    Say "Reading this machine (no changes)..."
    Push-Location $engine
    try {
        & $py -m pco_engine.cli --backend windows doctor
    } finally { Pop-Location }
    Write-Host ""
    Write-Host "Launching the console against the REAL machine." -ForegroundColor Yellow
    Write-Host "Every fix is journaled, previewed and one-click reversible." -ForegroundColor Yellow
    $env:PCO_BACKEND = "windows"
}

Say "Console: http://127.0.0.1:$Port  (Ctrl+C to stop)"
Start-Process "http://127.0.0.1:$Port"
Push-Location $engine
try {
    & $py -m pco_engine.server --port $Port
} finally {
    Pop-Location
}
