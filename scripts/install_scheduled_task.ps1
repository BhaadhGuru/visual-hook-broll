<#
.SYNOPSIS
    Registers a Windows Scheduled Task so the BrollInput -> BrollOutput GPU worker starts automatically at logon.
#>
[CmdletBinding()]
param(
    [string]$TaskName = "VisualHookBrollWorker"
)

$ErrorActionPreference = "Stop"
$StartScript = (Resolve-Path "$PSScriptRoot\start_worker.ps1").Path

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

Write-Host "Registered Scheduled Task '$TaskName' -> $StartScript" -ForegroundColor Green
Write-Host "Start it now with: Start-ScheduledTask -TaskName $TaskName" -ForegroundColor Cyan
