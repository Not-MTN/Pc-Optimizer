#requires -Version 5.1
<#
.SYNOPSIS
    Build Keel.exe and the Keel installer.

.DESCRIPTION
    Produces, in ..\dist:
        Keel.exe                    one-file windowed app (~12 MB)
        installer\Keel-0.5.0-Setup.exe

    Run this on Windows. PyInstaller cannot cross-compile from Linux or macOS:
    it bundles the Python runtime it is running under, so a Linux build would
    produce a Linux binary with a .exe name.

.PARAMETER SkipTests
    Skip the test suite. Every other check still runs.

.PARAMETER SkipInstaller
    Build only Keel.exe (no Inno Setup required).

.EXAMPLE
    .\packaging\build.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root   = Split-Path -Parent $PSScriptRoot
$Engine = Join-Path $Root 'engine'
$Dist   = Join-Path $Root 'dist'

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan; $script:CurrentStep = "$n · $msg" }
function Ok($msg)       { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn($msg)     { Write-Host "    !   $msg" -ForegroundColor Yellow }
function Fail($msg) {
    Write-Host "    X   $msg" -ForegroundColor Red
    if ($script:CurrentStep) { Write-Host "    X   while in step: $script:CurrentStep" -ForegroundColor Red }
    Write-Host "    X   (CI: the last 60 lines of this log are printed above the failure)"
    exit 1
}

function Find-Python {
    foreach ($c in @('py -3', 'python')) {
        try {
            $v = & cmd /c "$c -c `"import sys;print(sys.version_info[:2])`"" 2>$null
            # $v can be an array if the probe writes anything unexpected.
            $v = ($v -join ' ')
            if ($LASTEXITCODE -eq 0 -and $v -match '\(3, (\d+)') {
                $minor = [int]$Matches[1]
                if ($minor -ge 10) { return $c }
            }
        } catch { }
    }
    return $null
}

# ------------------------------------------------------------------ preflight
Step 1 'Preflight'
if (-not $IsWindows -and $PSVersionTable.PSEdition -eq 'Core') { Fail 'This must run on Windows.' }
if ($PSVersionTable.PSVersion.Major -lt 5) { Fail 'PowerShell 5.1 or newer is required.' }

$python = Find-Python
if (-not $python) {
    Warn 'Find-Python did not find a usable interpreter. Diagnostics follow:'
    & cmd /c "py -3 -c `"import sys;print(sys.version)`"" 2>&1 | ForEach-Object { Write-Host "      py -3: $_" }
    & cmd /c "python -c `"import sys;print(sys.version)`"" 2>&1 | ForEach-Object { Write-Host "      python: $_" }
    & cmd /c "where python" 2>&1 | ForEach-Object { Write-Host "      where: $_" }
    Fail 'Python 3.10+ was not found. Install it from python.org and tick "Add to PATH".'
}
Ok "python: $python"

foreach ($f in @('engine\pco_engine\app.py', 'engine\pco_engine\console.html',
                 'assets\keel.ico', 'packaging\keel.spec', 'packaging\version_info.txt')) {
    if (-not (Test-Path (Join-Path $Root $f))) { Fail "missing required file: $f" }
}
Ok 'required files present'

$icoSize = (Get-Item (Join-Path $Root 'assets\keel.ico')).Length
if ($icoSize -lt 1000) { Fail "assets\keel.ico looks empty ($icoSize bytes)" }
Ok "icon: $icoSize bytes"

# ------------------------------------------------------------------- tooling
Step 2 'Build tooling'
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'      # a failed probe is data, not a crash
& cmd /c "$python -m PyInstaller --version" 2>&1 | Out-Null
$havePyInstaller = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prev
if (-not $havePyInstaller) {
    Warn 'PyInstaller is not installed; installing into a build venv.'
    $venvPy = Join-Path $Root '.venv-build\Scripts\python.exe'
    if (-not (Test-Path $venvPy)) {
        & cmd /c "$python -m venv `"$Root\.venv-build`""
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
            Fail "could not create a build venv at $Root\.venv-build"
        }
    }
    & $venvPy -m pip install --disable-pip-version-check --quiet pyinstaller pywebview
    if ($LASTEXITCODE -ne 0) { Fail 'could not install PyInstaller.' }
    $python = $venvPy
}
Ok 'PyInstaller available'

$iscc = $null
# $env:X returns $null when unset; ${env:X} raises under StrictMode. This script
# runs with StrictMode Latest, so the difference is a hard crash.
$isccCandidates = @()
if ($env:ProgramFiles) { $isccCandidates += "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
$pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')   # safe under StrictMode
if ($pf86) { $isccCandidates += "$pf86\Inno Setup 6\ISCC.exe" }
if ($env:LOCALAPPDATA) { $isccCandidates += "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }
$isccCandidates += @(Get-ChildItem 'C:\Program Files*\Inno Setup 6\ISCC.exe' -ErrorAction SilentlyContinue |
                     ForEach-Object { $_.FullName })
foreach ($p in $isccCandidates) {
    if (Test-Path $p) { $iscc = $p; break }
}
if (-not $SkipInstaller -and -not $iscc) {
    Warn 'Inno Setup 6 not found — Keel.exe will still be built, without an installer.'
    foreach ($c in $isccCandidates) { Warn "      looked in: $c" }
    Warn 'Install it from jrsoftware.org/isdl.php (or `choco install innosetup`) to get the installer.'
    $SkipInstaller = $true
}
if ($iscc) { Ok "Inno Setup: $iscc" }

# --------------------------------------------------------------------- tests
Step 3 'Test suite'
if ($SkipTests) {
    Warn 'skipped (-SkipTests)'
} else {
    Push-Location $Engine
    try {
        & cmd /c "$python -m pytest -q"
        if ($LASTEXITCODE -ne 0) { Fail 'tests failed — not building an installer from a red suite.' }
    } finally { Pop-Location }
    Ok 'suite green'
}

# --------------------------------------------------------------------- build
Step 4 'Building Keel.exe'
if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }
Push-Location $Root
try {
    & cmd /c "$python -m PyInstaller --clean --noconfirm --distpath `"$Dist`" --workpath `"$Root\build`" packaging\keel.spec"
    if ($LASTEXITCODE -ne 0) { Fail 'PyInstaller failed. Scroll up for the first traceback.' }
} finally { Pop-Location }

$exe = Join-Path $Dist 'Keel.exe'
if (-not (Test-Path $exe)) {
    Warn "nothing at $exe. Contents of ${Dist}:"
    if (Test-Path $Dist) { Get-ChildItem $Dist -Recurse | ForEach-Object { Write-Host "      $($_.FullName)" } }
    Fail 'PyInstaller reported success but dist\Keel.exe is missing.'
}
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Ok "dist\Keel.exe  ($mb MB)"

# ------------------------------------------------------------------- smoke
Step 5 'Smoke test (does it actually launch?)'
$before = Get-Process -Name Keel -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $exe -ArgumentList '--no-window' -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 6
$alive = -not $proc.HasExited
if ($alive) { Stop-Process -Id $proc.Id -Force; Ok 'app started and stayed up for 6 s' }
else { Fail "Keel.exe exited immediately (code $($proc.ExitCode)). Check %LOCALAPPDATA%\Keel\keel.log" }

# --------------------------------------------------------------- installer
Step 6 'Installer'
if ($SkipInstaller) {
    Warn 'skipped — run again with Inno Setup installed to get the installer'
} else {
    & cmd /c "`"$iscc`" /Qp packaging\keel.iss"
    if ($LASTEXITCODE -ne 0) {
        Warn "ISCC exited $LASTEXITCODE. Re-running without /Q to show its reasons:"
        & cmd /c "`"$iscc`" packaging\keel.iss" 2>&1 | Select-Object -Last 25 |
            ForEach-Object { Write-Host "      $_" }
        Fail 'ISCC failed.'
    }
    $setup = Get-ChildItem (Join-Path $Dist 'installer') -Filter '*.exe' | Select-Object -First 1
    if (-not $setup) { Fail 'installer was not produced' }
    $smb = [math]::Round($setup.Length / 1MB, 1)
    Ok "$($setup.FullName)  ($smb MB)"
}

# ---------------------------------------------------------------- summary
Write-Host "`nDone." -ForegroundColor Green
Write-Host "  exe        $exe"
if (-not $SkipInstaller) { Write-Host "  installer  $(Join-Path $Dist 'installer')" }
Write-Host @"

Before you ship this build:
  * Run it on a clean VM, not your dev machine. A missing DLL only shows up there.
  * Open Home, then Fix -> Recovery, and confirm the journal verifies.
  * Apply one change, undo it, and check the value actually returned.
  Nothing here is code-signed; Defender SmartScreen will warn about an
  unsigned installer until you sign it.
"@ -ForegroundColor DarkGray
