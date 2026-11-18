@echo off
title MUSTER server (demo cards)
rem Double-click for a fresh practice run with Arjo's fake cards. This ERASES registered people.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_app.ps1" -Https -Synthetic -Reset
echo.
echo The server stopped. Press any key to close this window.
pause >nul
