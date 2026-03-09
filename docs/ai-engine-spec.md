# Техническое задание: AI Engine для платформы ГрузПоток

## Цель

Отдельный сервис AI Engine для анализа рынка перевозок, антифрода, прогноза ставок и подбора перевозчиков. Telegram-бот — тонкий клиент, обращается к AI Engine через HTTP API.

## Архитектура

```
Telegram Bot (thin client)
        ↓ HTTP
AI Engine API :8010
        ↓              ↓
Feature Extractors   LLM Client
   (asyncpg, SQL)    (Ollama + Redis cache + SLA)
        ↓              ↓
PostgreSQL        qwen3:30b (via WireGuard VPN)
```

**Принцип разделения:**
- Feature Extractors — детерминированные вычисления (медианы, P10/P90, скоринг, правила)
- LLM — только интерпретация и объяснение результатов

## Статус реализации

| Этап | Статус |
|------|--------|
| Сервис AI Engine (`/opt/ai-engine/`, порт 8010, systemd) | ✅ |
| Feature extractors (`extractors.py`) | ✅ |
| HTTP API — 7 эндпоинтов | ✅ |
| LLM интеграция (`llm.py`, только интерпретация) | ✅ |
| Redis кэш (TTL 5/10/30 мин) | ✅ |
| SLA защита (fallback + короткий режим + очередь) | ✅ |
| Метрики (`GET /metrics`) | ✅ |
| Интеграция с ботом (все команды через API) | ✅ |
| White-label через `AI_ENGINE_URL` env | ✅ |

## API эндпоинты

### POST /fraud_score
Анализ груза на мошенничество.

**Request:**
```json
{"load_id": 12345}
```

**Response:**
```json
{
  "score": 7.8,
  "risk": "medium",
  "verdict": "review",
  "explanation": "Цена на 28% ниже медианы рынка...",
  "features": {
    "price_vs_market_pct": -28,
    "account_age_days": 2,
    "phone_spam": true,
    "phone_loads_7d": 14,
    "is_verified": false,
    "no_inn": true,
    "risk_flags": [...]
  },
  "model": "qwen3:30b",
  "cached": false
}
```

### POST /route_price
Рекомендованная ставка по маршруту (данные 30 дней).

**Request:** `{"from_city": "Москва", "to_city": "Самара"}`

**Response:** avg, median, P10/P25/P75/P90, avg_rate_per_km, top_body_types + AI объяснение

### POST /route_forecast
Прогноз цен (данные 60 дней, тренд по дням недели).

**Response:** dow_prices (0-6), current_week_avg, weekly_trend_pct + AI прогноз

### POST /why_not_win
Почему объявление не закрывается + план действий.

**Response:** market_avg, competitors_active, age_hours, issues[], recommendations[] + AI план

### POST /suggest_carriers
Подбор перевозчиков с match score.

**Match score:** route_experience(40) + rating(20) + deals(15) + verified(10) + trust(10) + telegram(5)

### GET /market_anomaly
Аномалии рынка в реальном времени: демпинг (4ч vs 30д), всплеск аккаунтов, спам телефоны, ценовые скачки.

### GET /health
```json
{"status": "ok", "uptime_sec": 1234, "model": "qwen3:30b", "ollama": "ok", "ollama_ms": 142}
```

### GET /metrics
avg_latency_ms, p95_latency_ms, total_requests, cache_hits, fallbacks, errors, avg_tokens, queue_depth, gpu.vram_total

## SLA защита

| Ситуация | Поведение |
|----------|-----------|
| Ollama недоступна | Fallback-ответ, возвращает алгоритмический результат |
| Latency > 5000ms | Короткий режим (3 предложения, timeout 20с) |
| Очередь > 20 | "⏳ AI перегружен. Попробуй через 30 секунд." |

## Кэш TTL

| Эндпоинт | TTL |
|----------|-----|
| /route_price | 10 мин |
| /route_forecast | 30 мин |
| /market_anomaly | 5 мин |
| /fraud_score | 2 мин |
| /suggest_carriers | 3 мин |

## Деплой

```bash
# Сервис
systemctl status ai-engine   # порт 8010

# Конфиг
/opt/ai-engine/.env           # DATABASE_URL, OLLAMA_URL, OLLAMA_MODEL, REDIS_URL

# Бот
AI_ENGINE_URL=http://localhost:8010  # в /opt/admin-bot/.env
```

## White-label / Enterprise

Каждый клиент получает отдельный инстанс:
```
client_a → ai-engine-a (порт 8010) → ollama-a (модель A)
client_b → ai-engine-b (порт 8011) → ollama-b (модель B)
```

Бот переключается через `AI_ENGINE_URL` без пересборки.

## Файловая структура

```
/opt/ai-engine/
├── main.py          # FastAPI app, все эндпоинты
├── extractors.py    # Детерминированные вычисления (SQL → features)
├── llm.py           # LLM client (cache + SLA + metrics)
├── .env
└── .venv/
```
