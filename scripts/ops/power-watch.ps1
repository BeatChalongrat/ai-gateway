# ops — the host's power, for the pager. The host is a laptop; the ops watcher runs in a container and
# cannot see a battery. This writes what Windows says about the power to <BACKUP_DIR>/power_last.json —
# the folder ops-watch reads at /backups — once a minute, and the watcher says when the machine is on
# battery and pages when the charge is low (backend/app/ops/watch.py classify_power).
#
# A machine with no battery writes {"battery": false}: the rule is quiet there.
#
# Register (once, as the owner; it starts at logon and is looked at every five minutes, so one that
# died is started again):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ops\power-register-task.ps1
# One reading by hand, to a file of your choosing (nothing else is touched):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ops\power-watch.ps1 -Once -Out C:\temp\power.json
# BACKUP_DIR is read the way scripts\smoke-daily.ps1 reads it (default ../ai-gateway-backups/daily).
param([switch]$Once, [string]$Out = "", [int]$EverySeconds = 60)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Out) {
  $backupDir = $null
  $settings = Join-Path $root ".env"
  if (Test-Path $settings) {
    foreach ($line in Get-Content $settings) {
      if ($line -match '^\s*BACKUP_DIR\s*=\s*(.+?)\s*$') { $backupDir = $Matches[1].Trim().Trim('"').Trim("'") }
    }
  }
  if (-not $backupDir) { $backupDir = "../ai-gateway-backups/daily" }
  if (-not [System.IO.Path]::IsPathRooted($backupDir)) { $backupDir = Join-Path $root $backupDir }
  $backupDir = [System.IO.Path]::GetFullPath($backupDir)
  New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
  $Out = Join-Path $backupDir "power_last.json"
}
Add-Type -AssemblyName System.Windows.Forms

function Read-Power {
  $p = [System.Windows.Forms.SystemInformation]::PowerStatus
  $none = [System.Windows.Forms.BatteryChargeStatus]::NoSystemBattery
  $hasBattery = (($p.BatteryChargeStatus -band $none) -ne $none) -and ($p.BatteryLifePercent -le 1.0)
  $pct = $null
  if ($hasBattery) { $pct = [int][math]::Round($p.BatteryLifePercent * 100) }
  $minutes = $null
  if ($p.BatteryLifeRemaining -ge 0) { $minutes = [int][math]::Floor($p.BatteryLifeRemaining / 60) }
  $now = [DateTimeOffset]::UtcNow
  [ordered]@{
    schema       = 1
    at           = $now.ToString("yyyy-MM-ddTHH:mm:ssZ")
    epoch        = $now.ToUnixTimeSeconds()
    battery      = $hasBattery
    on_battery   = ($hasBattery -and ($p.PowerLineStatus -eq [System.Windows.Forms.PowerLineStatus]::Offline))
    charge_pct   = $pct
    minutes_left = $minutes
    line         = "$($p.PowerLineStatus)"
  }
}

function Write-Reading($path) {
  # written beside the target and moved over it: the watcher never reads half a file
  $tmp = "$path.tmp"
  (Read-Power | ConvertTo-Json -Compress) | Out-File -FilePath $tmp -Encoding ascii
  Move-Item -Force -Path $tmp -Destination $path
}

if ($Once) { Write-Reading $Out; Get-Content $Out; exit 0 }
while ($true) {
  try { Write-Reading $Out } catch { }
  Start-Sleep -Seconds $EverySeconds
}
