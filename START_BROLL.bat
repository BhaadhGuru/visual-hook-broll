@echo off
title Visual Hook B-Roll Worker (BrollInput -> BrollOutput)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_worker.ps1"
echo.
pause
