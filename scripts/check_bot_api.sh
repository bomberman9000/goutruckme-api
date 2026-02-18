#!/bin/bash
# Проверки Bot API на сервере. Запуск: cd /opt/goutruckme && bash scripts/check_bot_api.sh

set -e
cd "$(dirname "$0")/.."

echo "========== 1) Роуты /api/bot =========="
docker compose exec -T api sh -lc '
python3 - <<PY
from app.api.main import app
paths = sorted({getattr(r, "path", "") for r in app.routes if getattr(r, "path", None)})
for p in paths:
    if p.startswith("/api/bot"):
        print(p)
PY
'
echo "Ожидаем: /api/bot/link, /api/bot/loads, /api/bot/loads/{load_id}, /api/bot/loads/{load_id}/take"
echo ""

echo "========== 2) DATABASE_URL и миграции =========="
docker compose exec -T api sh -lc 'echo "DATABASE_URL=$DATABASE_URL"'
echo "alembic current:"
docker compose exec -T api sh -lc 'cd /app && alembic current' || true
echo "alembic upgrade head:"
docker compose exec -T api sh -lc 'cd /app && alembic upgrade head'
docker compose exec -T api sh -lc 'cd /app && alembic current'
echo ""

echo "========== 3) Колонки telegram_* в users =========="
docker compose exec -T db sh -lc '
PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d goutruckme -c "\d users" | sed -n "1,160p"
' || echo "Если ошибка — проверь, что контейнер db запущен и пароль верный."
echo ""

echo "========== 4) E2E: link → loads → take =========="
echo "4.1 POST /api/bot/link (подставь свой phone/password):"
echo 'curl -sS -X POST http://localhost:8000/api/bot/link -H "Content-Type: application/json" -d '"'"'{"phone":"+79990000000","password":"PASSWORD","telegram_id":123456789,"telegram_username":"testuser"}'"'"' | jq'
echo ""
echo "4.2 GET /api/bot/loads (после получения TOKEN):"
echo 'TOKEN="..."; curl -sS http://localhost:8000/api/bot/loads -H "Authorization: Bearer $TOKEN" | jq'
echo ""
echo "4.3 POST take (LOAD_ID из списка):"
echo 'LOAD_ID=1; curl -sS -X POST "http://localhost:8000/api/bot/loads/$LOAD_ID/take" -H "Authorization: Bearer $TOKEN" | jq'
echo ""
echo "Готово. Выполни curl-команды вручную с реальными phone/password и TOKEN."
