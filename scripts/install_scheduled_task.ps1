<#
.SYNOPSIS
    Registers a Windows Scheduled Task so the Watchdog (B-Roll, Yap, MediaMTX)
    starts automatically at logon and auto-restarts on failure.
#>
[CmdletBinding()]
param(
    [string]$TaskName = "VisualHookBrollWatchdog"
)

$ErrorActionPreference = "Stop"
$StartScript = (Resolve-Path "$PSScriptRoot\start_watchdog.ps1").Path

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""

$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Force | Out-Null

Write-Host "Installed Scheduled Task '$TaskName' -> $StartScript" -ForegroundColor Green
Write-Host "It will start automatically at logon (supervising B-Roll, Yap, and MediaMTX)." -ForegroundColor Cyan
Write-Host "To run it right now: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Yellow
