# Исправление пустой БД на сервере

## Проблема
Миграция `e842bea3c69d` создаёт таблицы с FK на `users`, но саму таблицу `users` не создавала — в репозитории не было начальной миграции для базовых таблиц.

## Что сделано в коде
1. Добавлена **начальная миграция** `20260206_000000_initial_schema.py`: создаёт `users`, `trucks`, `loads`, `bids`, `messages`, `rating_history`, `complaints`, `forum_posts`, `forum_comments`.
2. Миграция `e842bea3c69d` переведена на зависимость от неё (`down_revision = '20260206_000000'`).
3. Цепочка: `20260206_000000` → `e842bea3c69d` → `20260204_ae` → `20260205_ae_actor`.

## Что выполнить на сервере

```bash
cd /opt/goutruckme

# 1) Подтянуть код (git pull или скопировать файлы)
git pull   # или твой способ деплоя

# 2) Убедиться, что БД пустая (если уже запускали alembic и упал — таблиц нет, транзакция откатилась)
docker compose exec -T db psql -U postgres -d goutruckme -c '\dt'

# 3) Применить миграции с начала
docker compose exec -T api sh -lc 'cd /app && alembic upgrade head'

# 4) Проверить
docker compose exec -T db psql -U postgres -d goutruckme -c '\dt'
docker compose exec -T db psql -U postgres -d goutruckme -c 'select version_num from alembic_version;'

# 5) Перезапустить API
docker compose restart api
docker compose logs api --tail 30
```

Если `alembic upgrade head` снова выдаст ошибку — пришли полный вывод.

## Пароль Postgres
Если снова будет `password authentication failed`:
```bash
docker compose exec -T db psql -U postgres -d postgres -c "ALTER USER postgres WITH PASSWORD 'postgres';"
docker compose restart db
```
