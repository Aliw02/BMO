@echo off
echo ──────────────────────────────
echo تثبيت مهمة التنبيه لـ 9:00 صباحاً
echo ──────────────────────────────
powershell -Command ^
    $action = New-ScheduledTaskAction -Execute "python" -Argument """"%~dp0reminder_9am.py""""; ^
    $trigger = New-ScheduledTaskTrigger -Daily -At 09:00; ^
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries; ^
    Register-ScheduledTask -TaskName "NOVA_ExamReminder" -Action $action -Trigger $trigger -Settings $settings -Force; ^
    Write-Host "✅ تم تثبيت المهمة بنجاح!" -ForegroundColor Green
echo.
echo السكربت هيشتغل كل يوم ب 9:00 الصبح ويطلعلك تنبيه
echo عشان تلغي المهمة: اكتب بPowerShell:
echo    Unregister-ScheduledTask -TaskName "NOVA_ExamReminder" -Confirm:$false
pause
