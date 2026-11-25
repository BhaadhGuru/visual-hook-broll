@echo off
title Visual Hook B-Roll + Watchdog Setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_wizard.ps1"
echo.
pause
