@echo off
setlocal
pushd "%~dp0"
echo Starting Visual Hook B-Roll Setup Wizard...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_wizard.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Setup finished with exit code %EXIT_CODE%.
) else (
    echo Setup finished cleanly.
)
popd
pause
exit /b %EXIT_CODE%
