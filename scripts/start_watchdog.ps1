<#
.SYNOPSIS
    Starts the always-on Watchdog supervising B-Roll, Yap, and MediaMTX.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = "$PSScriptRoot\..\config.json",
    [switch]$Once,
    [switch]$NoBootStart
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating Python virtual environment in .venv..." -ForegroundColor Cyan
    python -m venv (Join-Path $ProjectRoot ".venv")
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
}

$argsList = @("-m", "src.watchdog")
if (Test-Path $ConfigPath) {
    $argsList += @("--config", $ConfigPath)
}
if ($Once) {
    $argsList += "--once"
}
if ($NoBootStart) {
    $argsList += "--no-boot-start"
}

Push-Location $ProjectRoot
try {
    & $VenvPython @argsList
} finally {
    Pop-Location
}
