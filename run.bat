@echo off
REM Double-click launcher. The pause at the end keeps the window open so any
REM error message stays readable instead of the console vanishing instantly.
cd /d "%~dp0"

python main.py %*

echo.
echo ---------------------------------------------
echo App closed. Press any key to close this window.
pause >nul
