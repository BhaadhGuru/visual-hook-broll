<#
.SYNOPSIS
    Starts the Visual Hook B-Roll folder watcher using the local Python virtualenv.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = "$PSScriptRoot\..\config.json",
    [switch]$Once,
    [switch]$MockComfy
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

$argsList = @("-m", "src.worker")
if (Test-Path $ConfigPath) {
    $argsList += @("--config", $ConfigPath)
}
if ($Once) {
    $argsList += "--once"
}
if ($MockComfy) {
    $argsList += "--mock-comfy"
}

Push-Location $ProjectRoot
try {
    & $VenvPython @argsList
} finally {
    Pop-Location
}
