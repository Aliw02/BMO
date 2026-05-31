@echo off
title BMO Telegram Bot

REM Set working directory to where this script is located
cd /d "%~dp0"

echo NOTE: Use start_all.bat to launch opencode serve + bot together.
echo This script only starts the bot (assumes opencode serve is already running).
echo.

:loop
echo ========================================
echo   Starting BMO Telegram Bot...
echo ========================================
python main.py

if %errorlevel% neq 0 (
    echo.
    echo ========================================
    echo   ERROR: Bot crashed!
    echo ========================================
    echo.
    echo Check the error above. Press any key to restart or Ctrl+C to quit.
    pause
)

echo.
echo Bot process ended. Restarting in 2 seconds (Ctrl+C to stop)...
timeout /t 2 > nul
goto loop
