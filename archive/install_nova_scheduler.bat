@echo off
chcp 65001 >nul
set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    $a = New-ScheduledTaskAction -Execute "python" -Argument ('"'+[string]%SCRIPT_DIR%nova_9am_auto.py+'"') -WorkingDirectory ([string]%SCRIPT_DIR%); ^
    $t = New-ScheduledTaskTrigger -Daily -At 09:00; ^
    $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -WakeToRun; ^
    Register-ScheduledTask -TaskName "NOVA_9AM_AutoTrigger" -Action $a -Trigger $t -Settings $s -RunLevel Highest -Force; ^
    Write-Host "OK - Task installed!" -ForegroundColor Green
echo.
echo Task installed. NOVA will auto-trigger at 9:00 AM daily.
echo Run this to remove: Unregister-ScheduledTask -TaskName "NOVA_9AM_AutoTrigger" -Confirm:$false
pause
