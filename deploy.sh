#!/usr/bin/env bash
# One-command deploy: pull → build → up → migrate → restart → smoke
# Запуск на сервере: cd /opt/gruzpotok && bash deploy.sh

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

LOG() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
OK()  { LOG "[OK] $*"; }
FAIL() { LOG "[FAIL] $*"; exit 1; }

# --- 1. Pull (git) ---
LOG "1. Git pull..."
if git pull 2>/dev/null; then
  OK "git pull"
else
  LOG "[SKIP] git pull (не репо или ошибка — продолжаем)"
fi

# --- 2. Build & up ---
export BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export BUILD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
LOG "2. Docker compose up -d --build (BUILD_SHA=$BUILD_SHA)..."
docker compose up -d --build || FAIL "docker compose up"

# --- 3. Migrate ---
LOG "3. Alembic upgrade head..."
docker compose exec -T api sh -lc 'cd /app && alembic upgrade head' || FAIL "alembic upgrade head"
OK "migrations applied"

# --- 4. Restart API ---
LOG "4. Restart API..."
docker compose restart api || FAIL "restart api"
sleep 3

# --- 5. Smoke: /health ---
LOG "5. Smoke: GET /health..."
HEALTH="$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8000/health 2>/dev/null || echo 000)"
if [ "$HEALTH" = "200" ]; then
  OK "health returned 200"
  curl -sS http://localhost:8000/health | head -c 200
  echo
else
  FAIL "health returned $HEALTH (expected 200)"
fi

# --- 6. Smoke: ix_audit_events_entity ---
LOG "6. Smoke: index ix_audit_events_entity..."
IDX="$(docker compose exec -T db sh -lc 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d gruzpotok -t -A -c "SELECT 1 FROM pg_indexes WHERE tablename='"'"'audit_events'"'"' AND indexname='"'"'ix_audit_events_entity'"'"';" 2>/dev/null' | tr -d '\r' || true)"
if [ "$IDX" = "1" ]; then
  OK "ix_audit_events_entity exists"
else
  LOG "[WARN] ix_audit_events_entity not found (миграции могли не дойти до audit_events)"
fi

LOG "=== Deploy finished ==="
OK "done"
