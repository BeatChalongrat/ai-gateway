# SRAN AI Gateway — Start all services (Windows PowerShell)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==============================" -ForegroundColor Cyan
Write-Host " SRAN AI Gateway — Startup"    -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan

# 1. Check .env
if (-not (Test-Path ".env")) {
    Write-Host "[!] No .env file found. Creating from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "[!] Edit .env with your API keys and DB credentials, then re-run." -ForegroundColor Yellow
    exit 1
}

# 2. Python venv
if (-not (Test-Path "backend\.venv")) {
    Write-Host "[*] Creating Python virtual environment..."
    python -m venv backend\.venv
}
& backend\.venv\Scripts\Activate.ps1

# 3. Install backend deps
Write-Host "[*] Installing backend dependencies..."
pip install -q -r backend\requirements.txt

# 4. Run DB migrations
Write-Host "[*] Running database migrations..."
Set-Location backend
alembic upgrade head
Set-Location $Root

# 5. Seed demo data
Write-Host "[*] Seeding demo data (idempotent)..."
Set-Location backend
python -m scripts.seed_demo_data
Set-Location $Root

# 6. Start backend
Write-Host "[*] Starting backend on port 8080..."
New-Item -ItemType Directory -Path logs -Force | Out-Null
Set-Location backend
$backend = Start-Process python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "8", "--timeout-keep-alive", "10" -PassThru -NoNewWindow -RedirectStandardOutput "$Root\logs\backend.log" -RedirectStandardError "$Root\logs\backend-err.log"
Set-Location $Root
Write-Host "    Backend PID: $($backend.Id)"

# 7. Install frontend deps
if (-not (Test-Path "admin-ui\node_modules")) {
    Write-Host "[*] Installing frontend dependencies..."
    Set-Location admin-ui
    npm install
    Set-Location $Root
}

# 8. Frontend .env.local
if (-not (Test-Path "admin-ui\.env.local")) {
    Write-Host "[*] Creating admin-ui\.env.local from .env.example..."
    Copy-Item "admin-ui\.env.example" "admin-ui\.env.local"
}

# 9. Start frontend
Write-Host "[*] Starting frontend on port 3000..."
Set-Location admin-ui
$frontend = Start-Process npm -ArgumentList "run", "dev" -PassThru -NoNewWindow -RedirectStandardOutput "$Root\logs\frontend.log" -RedirectStandardError "$Root\logs\frontend-err.log"
Set-Location $Root
Write-Host "    Frontend PID: $($frontend.Id)"

# 10. Health check
Write-Host "[*] Waiting for services..."
Start-Sleep -Seconds 5

try {
    $health = Invoke-RestMethod -Uri "http://localhost:8080/health" -TimeoutSec 5
    Write-Host ""
    Write-Host "======================================" -ForegroundColor Green
    Write-Host " SRAN AI Gateway running"              -ForegroundColor Green
    Write-Host "======================================" -ForegroundColor Green
    Write-Host " Backend:  http://localhost:8080"
    Write-Host " Frontend: http://localhost:3000"
    Write-Host " Health:   $($health.status)"
    Write-Host ""
    Write-Host " Logs: logs\backend.log, logs\frontend.log"
    Write-Host "======================================" -ForegroundColor Green
} catch {
    Write-Host "[!] Backend health check failed. Check logs\backend.log" -ForegroundColor Red
    exit 1
}
