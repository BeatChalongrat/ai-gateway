@echo off
echo Stopping existing backend processes...
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul

echo Starting SRAN AI Gateway (4 workers)...
cd /d %~dp0
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 4
