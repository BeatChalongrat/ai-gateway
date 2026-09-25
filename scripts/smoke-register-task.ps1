# FIX134 F — register (or replace) the daily smoke run: "SRAN smoke daily", every day at 06:30,
# as the current user, catching up at the next logon/wake when 06:30 was missed (StartWhenAvailable).
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-register-task.ps1
# Register-ScheduledTask instead of a schtasks /TR string: the quoting of a path with a space inside
# /TR "…\"…\"…" broke once (the task's argument became `-File " C:\…\smoke-daily.ps1\` and PowerShell
# refused to start it), and this form has no quoting to get wrong.
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "smoke-daily.ps1"
if (-not (Test-Path $script)) { throw "not found: $script" }
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $script) `
  -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At "06:30"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "SRAN smoke daily" -Action $action -Trigger $trigger -Settings $settings `
  -Description "FIX134: scripts/smoke.sh against live; result in ai-gateway-backups/daily/smoke_last.json (ops-watch pages when red or older than 26 h)" -Force | Out-Null
$t = Get-ScheduledTask -TaskName "SRAN smoke daily"
"registered: {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments
"start when available: {0}; next run: {1}" -f $t.Settings.StartWhenAvailable, (Get-ScheduledTaskInfo -TaskName "SRAN smoke daily").NextRunTime
