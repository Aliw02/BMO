@echo off
REM run_detached.bat — Windows detached process wrapper
REM Usage: run_detached.bat <command> <log_file>
REM Starts a command detached with stdout+stderr redirected to log file
REM Example: run_detached.bat "cloudflared tunnel --url http://localhost:3456" "logs\tunnel_3456.log"

setlocal enabledelayedexpansion

set "CMD=%~1"
set "LOG=%~2"

if "%CMD%"=="" (
    echo ERROR: No command provided.
    echo Usage: run_detached.bat ^<command^> ^<log_file^>
    exit /b 1
)

if "%LOG%"=="" (
    echo ERROR: No log file provided.
    echo Usage: run_detached.bat ^<command^> ^<log_file^>
    exit /b 1
)

REM Create log directory if it doesn't exist
for %%F in ("%LOG%") do set "LOG_DIR=%%~dpF"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM Start the command in a hidden window, redirecting both stdout and stderr
start /B cmd /c "%CMD% > "%LOG%" 2>&1"

REM Return the exit code (0 = success starting)
echo %ERRORLEVEL%
