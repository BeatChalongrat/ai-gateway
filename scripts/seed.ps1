# SRAN AI Gateway — Seed demo data
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location "$Root\backend"

if (Test-Path ".venv\Scripts\Activate.ps1") {
    & .venv\Scripts\Activate.ps1
}

Write-Host "[*] Seeding demo data..." -ForegroundColor Cyan
python -m scripts.seed_demo_data
Write-Host "[*] Done." -ForegroundColor Green
