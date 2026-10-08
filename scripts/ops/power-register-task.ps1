# ops — register (or replace) the host's power reading: "SRAN power watch", as the current user.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ops\power-register-task.ps1
#
# It starts at logon and runs for as long as the session does (scripts\ops\power-watch.ps1 writes one
# reading a minute); a second trigger looks every five minutes and starts it again if it has died
# (a second copy is never started: MultipleInstances IgnoreNew).
#
# The two settings that matter, and that a task does not have unless it is told: it may START on
# battery and it is NOT STOPPED when the machine goes on battery. Task Scheduler's defaults are the
# other way round, and a watch of the battery that stops when the charger is pulled watches nothing.
$ErrorActionPreference = "Stop"
$name = "SRAN power watch"
$script = Join-Path $PSScriptRoot "power-watch.ps1"
if (-not (Test-Path $script)) { throw "missing: $script" }
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $script) `
  -WorkingDirectory (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$again = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($atLogon, $again) -Settings $settings `
  -Description "ops: the host's power, once a minute, to power_last.json in the backup folder (ops-watch says when the host is on battery and pages when the charge is low)" -Force | Out-Null
Start-ScheduledTask -TaskName $name
$t = Get-ScheduledTask -TaskName $name
"registered: {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments
"starts on battery: {0}; stopped when the charger is pulled: {1}; state: {2}" -f (-not $t.Settings.DisallowStartIfOnBatteries), $t.Settings.StopIfGoingOnBatteries, $t.State
