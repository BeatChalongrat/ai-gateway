#!/usr/bin/env bash
# SRAN AI Gateway — Start all services (Linux/macOS)
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=============================="
echo " SRAN AI Gateway — Startup"
echo "=============================="

# 1. Check .env
if [ ! -f .env ]; then
  echo "[!] No .env file found. Creating from .env.example..."
  cp .env.example .env
  echo "[!] Edit .env with your API keys and DB credentials, then re-run."
  exit 1
fi

# 2. Python venv
if [ ! -d backend/.venv ]; then
  echo "[*] Creating Python virtual environment..."
  python3 -m venv backend/.venv
fi
source backend/.venv/bin/activate

# 3. Install backend deps
echo "[*] Installing backend dependencies..."
pip install -q -r backend/requirements.txt

# 4. Run DB migrations
echo "[*] Running database migrations..."
cd backend
alembic upgrade head
cd "$ROOT"

# 5. Seed demo data if DB is empty
echo "[*] Seeding demo data (idempotent)..."
cd backend
python -m scripts.seed_demo_data
cd "$ROOT"

# 6. Start backend
echo "[*] Starting backend on port 8080..."
mkdir -p logs
cd backend
nohup python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8080 \
  --workers 8 \
  --timeout-keep-alive 10 \
  > "$ROOT/logs/backend.log" 2>&1 &
BACKEND_PID=$!
cd "$ROOT"
echo "    Backend PID: $BACKEND_PID"

# 7. Install frontend deps
if [ ! -d admin-ui/node_modules ]; then
  echo "[*] Installing frontend dependencies..."
  cd admin-ui && npm install && cd "$ROOT"
fi

# 8. Frontend .env.local
if [ ! -f admin-ui/.env.local ]; then
  echo "[*] Creating admin-ui/.env.local from .env.example..."
  cp admin-ui/.env.example admin-ui/.env.local
fi

# 9. Start frontend
echo "[*] Starting frontend on port 3000..."
cd admin-ui
nohup npm run dev > "$ROOT/logs/frontend.log" 2>&1 &
FRONTEND_PID=$!
cd "$ROOT"
echo "    Frontend PID: $FRONTEND_PID"

# 10. Health check
echo "[*] Waiting for services..."
sleep 5

HEALTH=$(curl -sf http://localhost:8080/health 2>/dev/null || echo "FAIL")
if echo "$HEALTH" | grep -q '"ok"'; then
  echo ""
  echo "======================================"
  echo " SRAN AI Gateway running"
  echo "======================================"
  echo " Backend:  http://localhost:8080"
  echo " Frontend: http://localhost:3000"
  echo " Health:   $HEALTH"
  echo ""
  echo " Logs: logs/backend.log, logs/frontend.log"
  echo "======================================"
else
  echo "[!] Backend health check failed. Check logs/backend.log"
  echo "    Response: $HEALTH"
  exit 1
fi
