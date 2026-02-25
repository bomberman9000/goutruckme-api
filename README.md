# tg-bot — логистический Telegram-бот

Бот для поиска грузов, размещения заявок, рейтингов и откликов. Роли: заказчик, перевозчик, экспедитор.

## Требования

- Python 3.12+
- PostgreSQL
- Redis

## Переменные окружения

Скопируй `.env.example` в `.env` и заполни:

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `BOT_USERNAME` | Username бота (опционально) |
| `DATABASE_URL` | PostgreSQL, формат: `postgresql+asyncpg://user:pass@host:port/db` |
| `REDIS_URL` | Redis, например `redis://localhost:6379` |
| `ADMIN_ID` | Telegram user_id администратора (алерты Watchdog) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Логин и пароль админ-панели |
| `SECRET_KEY` | Секрет для сессий админки (смени в продакшене) |
| `WEBAPP_URL` | Публичный URL для WebApp (если используешь) |
| `GROQ_API_KEY` | Ключ [Groq](https://console.groq.com) для умного поиска `/find` и AI (опционально) |
| `DEBUG` | `true` / `false` |

## Запуск

### Локально (uv)

```bash
uv sync
# PostgreSQL и Redis должны быть запущены
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

### Docker Compose

```bash
cp .env.example .env
# Отредактируй .env (BOT_TOKEN, ADMIN_ID и т.д.)
docker compose up -d
```

- Бот: порт 8001 (внутри контейнера 8000)
- Health: http://localhost:8001/health
- Админка: http://localhost:8001/admin

### Parser-bot v2 (Redis Streams: producer + worker)

`parser-bot` (producer) только принимает Telegram-сообщения и кладёт raw payload в Redis Stream `logistics_stream`.  
`parser-worker` (consumer) читает stream через consumer group, прогоняет `extractor.py`, делает антидубль, опциональный scoring по ИНН и отправляет событие в `gruzpotok-api`.

```bash
# В .env заполни минимум:
# PARSER_ENABLED=true
# PARSER_TG_API_ID, PARSER_TG_API_HASH
# PARSER_TG_STRING_SESSION
# PARSER_CHAT_IDS=@chat1,-1001234567890
docker compose --profile parser-v2 up -d parser-bot parser-worker
```

Антидубль работает по ключу `телефон + маршрут` с TTL `PARSER_DEDUPE_TTL_SEC` (по умолчанию 2 часа).  
Статус обработки пишется в таблицу `parser_ingest_events` (миграция `migrations/010_parser_ingest_events.sql`).

Масштабирование воркеров:

```bash
docker compose --profile parser-v2 up -d --scale parser-worker=3 parser-worker
```

Проверка очереди:

```bash
docker compose exec -T redis redis-cli xlen logistics_stream
```

Для шардирования producer по аккаунтам запускай отдельные инстансы с разными `PARSER_TG_STRING_SESSION` и разными `PARSER_CHAT_IDS`.

## Команды бота

- `/start` — меню / онбординг
- `/help` — справка
- `/find` — умный поиск грузов (нужен `GROQ_API_KEY` для парсинга запросов)
- `/webapp` — открыть TWA-кабинет внутри Telegram
- `/buy_premium` — купить Premium через Telegram Stars
- `/referral` — получить реферальную ссылку (+дни premium за приглашенного)
- `/me` — профиль
- `/remind 30m Текст` — напоминание
- `/reminders` — список напоминаний

## Feed API (для TWA)

- `GET /api/v1/feed`
  - Параметры: `limit`, `cursor`, `verdict=green&verdict=yellow`, `min_score`, `max_score`, `from_city`, `to_city`
  - Заголовок `Authorization: tma <initData>` (опционально): если нет активного premium, поле `phone` маскируется
  - Источник: таблица `parser_ingest_events`
  - По умолчанию возвращает только `is_spam=false` и `status=synced`
- `POST /api/v1/feed/{id}/click`
  - Логирует клик «Позвонить» в `call_logs`
  - Требует `Authorization: tma <initData>`

## Структура

- `main.py` — FastAPI + lifespan, запуск бота и планировщика
- `src/bot/` — хендлеры, клавиатуры, FSM
- `src/core/` — БД, Redis, AI, сервисы (watchdog, рейтинг, заявки)
- `src/admin/` — веб-админка
- `src/webapp/` — WebApp
- `frontend/twa/` — frontend-каркас Telegram WebApp (React + Vite)
- `migrations/` — SQL-миграции схемы
