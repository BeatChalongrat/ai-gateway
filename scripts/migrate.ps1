# SRAN AI Gateway — Run database migrations
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location "$Root\backend"

if (Test-Path ".venv\Scripts\Activate.ps1") {
    & .venv\Scripts\Activate.ps1
}

Write-Host "[*] Running Alembic migrations..." -ForegroundColor Cyan
alembic upgrade head
Write-Host "[*] Migrations complete." -ForegroundColor Green
Write-Host ""
alembic current
