@echo off
setlocal
pushd "%~dp0"
if not exist "%~dp0config.json" (
    echo [INFO] config.json not found yet. Running one-time auto-detect setup first...
    echo        (Tip: Run SETUP.bat anytime to re-detect Ollama or ComfyUI Flux models.)
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_wizard.ps1" -NonInteractive -SkipSelfTest
    if errorlevel 1 (
        echo [ERROR] Initial setup failed. Please run SETUP.bat to review details.
        popd
        pause
        exit /b 1
    )
)
echo Starting Visual Hook B-Roll GPU Worker (press Ctrl+C to stop)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_worker.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Worker exited with code %EXIT_CODE%.
) else (
    echo Worker stopped.
)
popd
pause
exit /b %EXIT_CODE%
