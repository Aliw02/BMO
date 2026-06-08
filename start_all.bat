@echo off
title BMO System Launcher
setlocal enabledelayedexpansion

REM Set working directory to where this script is located
cd /d "%~dp0"

echo ===================================================
echo             BMO System Launcher
echo ===================================================
echo.

REM ── Step 1: Kill any existing instances of opencode serve or BMO ──
echo [1/3] Cleaning up existing services...
taskkill /F /FI "WINDOWTITLE eq OpenCode-Serve*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq BMO-Bot*" >nul 2>&1
echo   Done.
echo.

REM ── Step 2: Start OpenCode serve in a new window ──
echo [2/3] Starting OpenCode server (port 4800)...
start "OpenCode-Serve" cmd /k "opencode serve --port 4800"

REM Wait for OpenCode server to be ready
echo   Waiting for OpenCode server to initialize...
timeout /t 5 /nobreak >nul

REM ── Step 3: Start BMO Telegram Bot + MCP Server ──
echo [3/3] Starting BMO Telegram Bot...
echo.
echo ===================================================
echo   BMO is running! Keep this window open.
echo   Press Ctrl+C in this window or close it to stop the bot.
echo ===================================================
echo.

python main.py

echo.
echo Stopping all launched services...
taskkill /F /FI "WINDOWTITLE eq OpenCode-Serve*" >nul 2>&1
echo Done.
