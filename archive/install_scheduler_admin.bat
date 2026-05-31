@echo off
echo ============================================
echo Installing NOVA Auto-Trigger Task Scheduler
echo ============================================
echo Please run this as Administrator!
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    $scriptDir = '%CD%'; ^
    $pythonScript = [System.IO.Path]::Combine($scriptDir, 'nova_9am_auto.py'); ^
    $action = New-ScheduledTaskAction -Execute 'python' -Argument ('"' + $pythonScript + '"') -WorkingDirectory $scriptDir; ^
    $trigger = New-ScheduledTaskTrigger -Daily -At 09:00; ^
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -WakeToRun; ^
    Register-ScheduledTask -TaskName 'NOVA_9AM_AutoTrigger' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force; ^
    Write-Host 'OK - Task installed! NOVA will auto-trigger at 9:00 AM daily.' -ForegroundColor Green
echo.
echo Done.
pause
