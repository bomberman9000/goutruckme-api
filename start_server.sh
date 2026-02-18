#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
pkill -f "uvicorn.*8080" 2>/dev/null
sleep 1
echo "🚀 Запуск сервера на http://localhost:8080"
uvicorn app.api.main:app --host 0.0.0.0 --port 8080 --reload


