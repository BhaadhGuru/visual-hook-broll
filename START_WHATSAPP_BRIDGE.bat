@echo off
setlocal
echo Starting Local Baileys WhatsApp QR Bridge (Reels + Collage + Visual Hook B-Roll + News)...
pushd "C:\Projects\whatsapp-reel-workflow"
node bridge.mjs
set "EXIT_CODE=%ERRORLEVEL%"
popd
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Bridge exited with code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
