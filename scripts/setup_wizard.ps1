<#
.SYNOPSIS
    Zero-config Windows setup wizard for Visual Hook B-Roll.
    Creates .venv, installs Pillow, auto-detects Google Drive / Ollama / ComfyUI Flux,
    writes config.json, runs offline self-tests + live health check, and optionally
    registers auto-start at Windows logon.
#>
[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$ConfigPath = Join-Path $ProjectRoot "config.json"

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Visual Hook B-Roll - Windows Zero-Config Setup" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Locate a real Python 3 interpreter
$PythonExe = $null
$PythonArgsPrefix = @()
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    foreach ($candidate in @(
        @{ Cmd = "python"; Prefix = @() },
        @{ Cmd = "py"; Prefix = @("-3") },
        @{ Cmd = "python3"; Prefix = @() }
    )) {
        $found = Get-Command $candidate.Cmd -ErrorAction SilentlyContinue
        if ($found) {
            try {
                $ver = & $found.Source @($candidate.Prefix) -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
                if ($LASTEXITCODE -eq 0 -and $ver -match "^3\.") {
                    $PythonExe = $found.Source
                    $PythonArgsPrefix = $candidate.Prefix
                    Write-Host "[OK]   Python $ver found -> $PythonExe" -ForegroundColor Green
                    break
                }
            } catch {
                continue
            }
        }
    }
} finally {
    $ErrorActionPreference = $prevEAP
}

if (-not $PythonExe) {
    Write-Host "[FAIL] Python 3.10+ was not found in PATH." -ForegroundColor Red
    Write-Host "       Install Python from https://www.python.org/downloads/ and check 'Add python.exe to PATH'." -ForegroundColor Yellow
    exit 1
}

# 2. Create .venv & install Pillow (only external runtime dependency)
if (-not (Test-Path $VenvPython)) {
    Write-Host "[INFO] Creating virtual environment in .venv..." -ForegroundColor Cyan
    & $PythonExe @PythonArgsPrefix -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Failed to create .venv." -ForegroundColor Red
        exit 1
    }
}

$hasPil = & $VenvPython -c "import importlib.util; print('1' if importlib.util.find_spec('PIL') else '0')"
if ($LASTEXITCODE -ne 0 -or $hasPil.Trim() -ne "1") {
    Write-Host "[INFO] Installing Python dependencies from requirements.txt..." -ForegroundColor Cyan
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPython -m pip install --upgrade pip
        & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
        $pipExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if ($pipExit -ne 0) {
        Write-Host "[FAIL] Failed to install dependencies from requirements.txt." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[OK]   .venv and Pillow are ready." -ForegroundColor Green
}

# 3. Auto-detect Google Drive folders, Ollama model, and ComfyUI Flux layout -> config.json
Write-Host ""
Write-Host "[INFO] Running auto-detection (Drive, Ollama, ComfyUI Flux)..." -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    & $VenvPython -m src.setup_detect --config $ConfigPath --create-dirs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Auto-detection failed with exit code $LASTEXITCODE." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    # 4. Safe offline self-test suite (never writes into real Google Drive folders)
    if (-not $SkipSelfTest) {
        Write-Host ""
        Write-Host "[INFO] Running offline unit & pipeline self-tests..." -ForegroundColor Cyan
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $VenvPython -m unittest discover -s tests
            $testExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEAP
        }
        if ($testExit -ne 0) {
            Write-Host "[FAIL] Offline self-test suite failed." -ForegroundColor Red
            exit $testExit
        }
        Write-Host "[OK]   All offline self-tests passed." -ForegroundColor Green
    }
} finally {
    Pop-Location
}

# 5. Live preflight health summary
Write-Host ""
& "$PSScriptRoot\check_health.ps1" -ConfigPath $ConfigPath

# 6. Optional logon auto-start registration
if (-not $NonInteractive) {
    Write-Host ""
    $reply = Read-Host "Register Windows logon auto-start task for START_BROLL worker? (y/N)"
    if ($reply -match "^(y|yes)$") {
        & "$PSScriptRoot\install_scheduled_task.ps1"
    } else {
        Write-Host "[INFO] Skipped Scheduled Task registration. You can run .\scripts\install_scheduled_task.ps1 anytime." -ForegroundColor Cyan
    }
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  Setup complete! Config saved to: config.json" -ForegroundColor Green
Write-Host "  Next step: Double-click START_BROLL.bat to launch." -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
