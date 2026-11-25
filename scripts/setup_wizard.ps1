<#
.SYNOPSIS
    Zero-config interactive setup wizard for Visual Hook B-Roll + PC Studio Watchdog.
    Double-click SETUP.bat to run this.
#>
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$ConfigPath = Join-Path $ProjectRoot "config.json"

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Visual Hook B-Roll + PC Studio Watchdog Setup" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python & set up .venv
Write-Host "[1/5] Checking Python environment..." -ForegroundColor Yellow
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    Write-Host "ERROR: Python was not found in PATH. Install Python 3.10+ from python.org and check 'Add Python to PATH'." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "      Creating virtual environment (.venv)..." -ForegroundColor Gray
    python -m venv $VenvDir
}
Write-Host "      Installing required package (Pillow)..." -ForegroundColor Gray
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r (Join-Path $ProjectRoot "requirements.txt")
Write-Host "      Python environment ready." -ForegroundColor Green
Write-Host ""

# 2. Auto-detect Google Drive, Ollama, ComfyUI Flux, Yap & MediaMTX
Write-Host "[2/5] Auto-detecting Google Drive, Ollama, ComfyUI Flux, Yap & MediaMTX..." -ForegroundColor Yellow
Push-Location $ProjectRoot
try {
    & $VenvPython -m src.setup_detect --write
} finally {
    Pop-Location
}
Write-Host ""

# 3. Offer drag-and-drop path entry if Yap or MediaMTX wasn't auto-found
$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$updatedExtra = $false

if (-not $cfg.watchdog.yap_bat) {
    Write-Host "[3/5] Yap .bat was not found automatically." -ForegroundColor Yellow
    $yapInput = Read-Host "      Drag & drop your Yap .bat file here (or press Enter to skip for now)"
    $yapClean = $yapInput.Trim().Trim('"')
    if ($yapClean) {
        $cfg.watchdog.yap_bat = $yapClean
        $updatedExtra = $true
    }
}
if (-not $cfg.watchdog.mediamtx_bat) {
    Write-Host "[3/5] MediaMTX .bat / .exe was not found automatically." -ForegroundColor Yellow
    $mtxInput = Read-Host "      Drag & drop your MediaMTX .bat or mediamtx.exe here (or press Enter to skip for now)"
    $mtxClean = $mtxInput.Trim().Trim('"')
    if ($mtxClean) {
        $cfg.watchdog.mediamtx_bat = $mtxClean
        $updatedExtra = $true
    }
}
if ($updatedExtra) {
    $cfg | ConvertTo-Json -Depth 10 | Set-Content -Path $ConfigPath -Encoding UTF8
    Push-Location $ProjectRoot
    try {
        & $VenvPython -m src.setup_detect --write
    } finally {
        Pop-Location
    }
}
Write-Host ""

# 4. Health check & optional self-test
Write-Host "[4/5] Running health check..." -ForegroundColor Yellow
& (Join-Path $PSScriptRoot "check_health.ps1") -ConfigPath $ConfigPath
Write-Host ""

$runTest = Read-Host "Run a quick self-test now using the AMA TechTel example script? (Y/n)"
if ($runTest -eq "" -or $runTest -match "^[Yy]") {
    $testInput = Join-Path $cfg.input_dir "setup_self_test_ama.txt"
    $exampleFile = Join-Path $ProjectRoot "examples\ama_techtel_card_reader.txt"
    if (Test-Path $exampleFile) {
        Copy-Item $exampleFile $testInput -Force
        Write-Host "      Dropped test script -> $testInput" -ForegroundColor Cyan
        Write-Host "      Processing one job (this may take 30-90 seconds on GPU)..." -ForegroundColor Cyan
        Push-Location $ProjectRoot
        try {
            & $VenvPython -m src.worker --config $ConfigPath --once
        } finally {
            Pop-Location
        }
        Write-Host "      Check output folder: $($cfg.output_dir)" -ForegroundColor Green
    }
}
Write-Host ""

# 5. Install Windows auto-start Scheduled Task (Watchdog)
Write-Host "[5/5] Auto-start at Windows login + WhatsApp remote control" -ForegroundColor Yellow
$installTask = Read-Host "Install the always-on Watchdog so B-Roll, Yap & MediaMTX start at login and respond to WhatsApp commands? (Y/n)"
if ($installTask -eq "" -or $installTask -match "^[Yy]") {
    & (Join-Path $PSScriptRoot "install_scheduled_task.ps1")
    Write-Host "      Starting Watchdog in the background now..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName "VisualHookBrollWatchdog" -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  ALL DONE!" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  - Double-click START_BROLL.bat anytime to watch B-Roll live." -ForegroundColor White
Write-Host "  - Double-click START_WATCHDOG.bat anytime to watch the supervisor live." -ForegroundColor White
Write-Host "  - WhatsApp commands: #status, #start all, #restart stream, #restart yap, #restart broll" -ForegroundColor White
Write-Host ""
