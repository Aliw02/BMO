@echo off
title BMO Webchat + Tunnel Launcher
setlocal enabledelayedexpansion

REM Set working directory to where this script is located
cd /d "%~dp0"

echo ========================================
echo   BMO Webchat + Tunnel Launcher
echo ========================================
echo.

REM ── Step 1: Check if opencode serve is running ──
echo [1/4] Checking opencode serve...
curl -s http://127.0.0.1:4096/session >nul 2>&1
if %errorlevel% neq 0 (
    echo   ERROR: opencode serve is not running on port 4096!
    echo   Run start_all.bat first.
    echo.
    pause
    exit /b 1
)
echo   opencode serve is running.
echo.

REM ── Step 2: Kill existing webchat on port 3456 ──
echo [2/4] Cleaning up existing webchat...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":3456"') do (
    echo   Killing existing webchat (PID: %%a)
    taskkill /F /PID %%a >nul 2>&1
)
echo   Done.
echo.

REM ── Step 3: Start webchat server ──
echo [3/4] Starting webchat server on port 3456...
start "BMO-Webchat" cmd /k "cd webchat && node server.js"

REM Wait for webchat to be ready
echo   Waiting for webchat to start...
timeout /t 3 /nobreak >nul
curl -s http://localhost:3456 >nul 2>&1
if %errorlevel% equ 0 (
    echo   Webchat is running at http://localhost:3456
) else (
    echo   WARNING: Webchat may not be ready yet. Check the Webchat window.
)
echo.

REM ── Step 4: Ask about tunnel ──
echo [4/4] Start cloudflared tunnel for public access?
set /p tunnel_choice="Type 'yes' to start tunnel, or press Enter to skip: "

if /i "!tunnel_choice!"=="yes" (
    echo.
    echo Starting cloudflared tunnel...
    echo The tunnel URL will appear in a new window.
    echo.
    start "BMO-Tunnel" cmd /k "cloudflared tunnel --url http://localhost:3456"
    echo.
    echo ========================================
    echo   Webchat + Tunnel started!
    echo   - Local:  http://localhost:3456
    echo   - Public: Check the BMO-Tunnel window
    echo ========================================
) else (
    echo.
    echo ========================================
    echo   Webchat started!
    echo   - Local: http://localhost:3456
    echo   - Public: Not started (skip tunnel)
    echo ========================================
)

echo.
echo Press any key to stop all services...
pause >nul

echo Stopping services...
taskkill /F /FI "WINDOWTITLE eq BMO-Webchat*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq BMO-Tunnel*" >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1
echo Done.
