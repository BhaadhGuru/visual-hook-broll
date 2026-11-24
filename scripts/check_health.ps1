<#
.SYNOPSIS
    Preflight health check for local Ollama, ComfyUI Flux, and Google Drive BrollInput/BrollOutput folders.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = "$PSScriptRoot\..\config.json"
)

if (-not (Test-Path $ConfigPath)) {
    $ConfigPath = "$PSScriptRoot\..\config.example.json"
}

$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
Write-Host "=== Visual Hook B-Roll Health Check ===" -ForegroundColor Cyan

# 1. Folders
foreach ($label in @("input_dir", "output_dir")) {
    $p = $cfg.$label
    if (Test-Path $p) {
        Write-Host "[OK]   $label -> $p" -ForegroundColor Green
    } else {
        Write-Host "[WARN] $label does not exist yet: $p (worker.py will create it if parent Drive mount exists)" -ForegroundColor Yellow
    }
}

# 2. Ollama
$ollamaUrl = "$($cfg.ollama.base_url.TrimEnd('/'))/api/tags"
try {
    $tags = Invoke-RestMethod -Uri $ollamaUrl -TimeoutSec 5
    $models = $tags.models | ForEach-Object { $_.name }
    if ($models -contains $cfg.ollama.model) {
        Write-Host "[OK]   Ollama reachable & model '$($cfg.ollama.model)' installed." -ForegroundColor Green
    } else {
        Write-Host "[WARN] Ollama reachable, but model '$($cfg.ollama.model)' not found. Installed: $($models -join ', ')" -ForegroundColor Yellow
        Write-Host "       Run: ollama pull $($cfg.ollama.model)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[FAIL] Ollama not reachable at $ollamaUrl — start Ollama first." -ForegroundColor Red
}

# 3. ComfyUI
$comfyUrl = "$($cfg.comfyui.base_url.TrimEnd('/'))/system_stats"
try {
    $stats = Invoke-RestMethod -Uri $comfyUrl -TimeoutSec 5
    $gpu = $stats.devices[0].name
    Write-Host "[OK]   ComfyUI reachable ($gpu)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] ComfyUI not reachable at $comfyUrl — launch ComfyUI with --listen 127.0.0.1 --port 8188." -ForegroundColor Red
}
