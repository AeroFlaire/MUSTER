@echo off
title MUSTER server
rem Double-click to start MUSTER for the phone (keeps your registered people).
rem Leave this window open while you use the app. Close it to stop.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_app.ps1" -Https
echo.
echo The server stopped. Press any key to close this window.
pause >nul
