@echo off
title PC Studio Watchdog (B-Roll + Yap + MediaMTX)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_watchdog.ps1"
echo.
pause
