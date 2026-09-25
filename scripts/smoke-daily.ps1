# FIX134 F — the daily smoke run (Task Scheduler, 06:30): the live target with the desktop
# check on, its JSON result copied to <BACKUP_DIR>/smoke_last.json — the folder ops-watch reads
# at /backups — so the ops watcher pages when the suite is red or has not run for 26 h
# (backend/app/ops/watch.py classify_smoke). The console output goes to smoke_last.log beside it.
#
# Register (once, from an elevated or the owner's own PowerShell; the task runs as the owner so
# it can drive the installed desktop app):
#   schtasks /Create /TN "SRAN smoke daily" /SC DAILY /ST 06:30 /F ^
#     /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:\Users\Beat Beat\Desktop\ai-gateway\scripts\smoke-daily.ps1\""
# Run by hand:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-daily.ps1
# BACKUP_DIR is read from .env the way compose reads it (relative to the repo root; default
# ../ai-gateway-backups/daily — docker-compose.webchat.yml).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
$backupDir = $null
if (Test-Path $envFile) {
  foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*BACKUP_DIR\s*=\s*(.+?)\s*$') { $backupDir = $Matches[1].Trim().Trim('"').Trim("'") }
  }
}
if (-not $backupDir) { $backupDir = "../ai-gateway-backups/daily" }
if (-not [System.IO.Path]::IsPathRooted($backupDir)) { $backupDir = Join-Path $root $backupDir }
$backupDir = [System.IO.Path]::GetFullPath($backupDir)
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$out = Join-Path $backupDir "smoke_last.json"
$log = Join-Path $backupDir "smoke_last.log"

$bash = $null
foreach ($candidate in @("C:\Program Files\Git\bin\bash.exe", "C:\Program Files\Git\usr\bin\bash.exe")) {
  if (Test-Path $candidate) { $bash = $candidate; break }
}
if (-not $bash) { $cmd = Get-Command bash -ErrorAction SilentlyContinue; if ($cmd) { $bash = $cmd.Source } }

$started = Get-Date
$env:SMOKE_TARGET = "live"
$env:SMOKE_DESKTOP = "1"
$env:SMOKE_OUT = $out
$rc = 2
if (-not $bash) {
  "smoke-daily: Git Bash not found (scripts/smoke.sh needs it)" | Out-File -FilePath $log -Encoding utf8
} else {
  Set-Location $root
  & $bash -c "bash scripts/smoke.sh" *> $log
  $rc = $LASTEXITCODE
}

# The watcher must see a record even when the suite could not write one (node missing, a crash
# before the result): a red record with the reason, so "has not run" and "is red" both surface.
$fresh = (Test-Path $out) -and ((Get-Item $out).LastWriteTime -ge $started.AddSeconds(-5))
if (-not $fresh) {
  $now = [DateTime]::UtcNow
  $record = @{
    schema = 1; target = "live"; at = $now.ToString("yyyy-MM-ddTHH:mm:ssZ")
    epoch = [int][double]::Parse(($now - [DateTime]'1970-01-01').TotalSeconds.ToString([cultureinfo]::InvariantCulture))
    ok = $false; error = "smoke.sh did not write a result (exit $rc); see smoke_last.log"; checks = @()
    counts = @{ pass = 0; fail = 1; skip = 0 }
  }
  $record | ConvertTo-Json -Depth 4 | Out-File -FilePath $out -Encoding utf8
}
exit $rc
