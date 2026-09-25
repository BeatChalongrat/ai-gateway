# dev.ps1 — PowerShell wrapper mirroring the Makefile targets.
#
# Usage:
#   .\scripts\dev.ps1 up
#   .\scripts\dev.ps1 seed
#   .\scripts\dev.ps1 logs
#   .\scripts\dev.ps1 clean
#
# Same semantics as `make <target>`; exists for Windows users without
# GNU make.

param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "logs", "seed", "clean", "test", "build", "rebuild", "ps",
                 "shell-backend", "shell-admin", "shell-portal", "help")]
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host "SRAN AI Gateway — dev stack commands" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  up            Start the full stack in dev mode"
    Write-Host "  down          Stop the stack (keeps volumes)"
    Write-Host "  logs          Tail logs from all services"
    Write-Host "  seed          Seed admin + portal test users"
    Write-Host "  clean         Stop + remove volumes (wipes DB/Redis)"
    Write-Host "  test          Run backend tests"
    Write-Host "  build         Build all images"
    Write-Host "  rebuild       Rebuild images with no cache"
    Write-Host "  ps            Show running containers"
    Write-Host "  shell-backend Exec into backend container"
    Write-Host "  shell-admin   Exec into admin-ui container"
    Write-Host "  shell-portal  Exec into portal container"
}

switch ($Command) {
    "up" {
        docker compose up -d
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host ""
        Write-Host "Backend:   https://localhost:8080/health"
        Write-Host "Admin UI:  https://localhost:3000"
        Write-Host "Portal:    https://localhost:3001"
        Write-Host "Mock prov: http://localhost:9090/health"
        Write-Host "Postgres:  localhost:5432 (postgres/postgres)"
        Write-Host "Redis:     localhost:6379"
        Write-Host ""
        Write-Host "Run '.\scripts\dev.ps1 seed' once to create admin + test portal users."
    }
    "down"          { docker compose down }
    "logs"          { docker compose logs -f }
    "seed"          { docker compose exec backend python -m scripts.seed_audit_data }
    "clean"         { docker compose down -v }
    "test"          { bash scripts/backend-test-lane.sh }   # FIX131: throwaway container, never the live one
    "build"         { docker compose build }
    "rebuild"       { docker compose build --no-cache }
    "ps"            { docker compose ps }
    "shell-backend" { docker compose exec backend /bin/bash }
    "shell-admin"   { docker compose exec admin-ui /bin/sh }
    "shell-portal"  { docker compose exec portal /bin/sh }
    "help"          { Show-Help }
    default         { Show-Help }
}
