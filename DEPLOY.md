# One-command deploy

## Скрипт
- **Файл:** `deploy.sh` (в корне проекта)
- **На сервере:** один раз положить в `/opt/gruzpotok` (git pull или rsync), дальше — одна команда.

## Запуск
```bash
cd /opt/gruzpotok
bash deploy.sh
```

Или, если сделали `chmod +x deploy.sh`:
```bash
cd /opt/gruzpotok
./deploy.sh
```

## Что делает
1. `git pull` (если не репо — пропуск)
2. `docker compose up -d --build`
3. `docker compose exec api ... alembic upgrade head`
4. `docker compose restart api`
5. Smoke: `curl http://localhost:8000/health` (ожидаем 200)
6. Smoke: проверка индекса `ix_audit_events_entity` в Postgres

При ошибке на любом шаге — вывод `[FAIL]` и выход с кодом 1.
