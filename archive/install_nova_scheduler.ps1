$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "nova_9am_auto.py"

$action = New-ScheduledTaskAction -Execute "python" -Argument "`"$pythonScript`"" -WorkingDirectory $scriptDir
$trigger = New-ScheduledTaskTrigger -Daily -At 09:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -WakeToRun

Register-ScheduledTask -TaskName "NOVA_9AM_AutoTrigger" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force

Write-Host "OK - Task installed! NOVA will auto-trigger at 9:00 AM daily." -ForegroundColor Green
