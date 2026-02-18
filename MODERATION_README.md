# AI Moderation v1 (Deals + Documents)

Модерация автоматически проверяет каждую синхронизированную сделку (`deal_sync`) и загруженный документ (`document_sync`), сохраняет результат в `moderation_review` и показывает в админке, WebApp и отправляет Telegram при HIGH risk.

## Переменные окружения

```env
# Опционально: ключ для вызова API модерации (тот же X-Client-Key, что и для deals-sync)
CLIENT_SYNC_KEY=your-secret-key

# Опционально: LLM для доп. проверки (если не задано — только правила)
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# Опционально: уведомления в Telegram при HIGH risk
ADMIN_CHAT_ID=
TELEGRAM_BOT_TOKEN=
```

Если `LLM_API_KEY` не задан — используется только rules-only движок. Если задан — ответ LLM дополняет/переопределяет уровень риска и комментарий (при ошибке парсинга — fallback на правила).

Если `ADMIN_CHAT_ID` или `TELEGRAM_BOT_TOKEN` не заданы — отправка в Telegram не выполняется.

## Как проверить локально

1. **Миграция**
   ```bash
   cd goutruckme && source venv/bin/activate && alembic upgrade head
   ```
   Или таблица создаётся при старте через `init_db()` (в т.ч. `moderation_review`).

2. **Создать/обновить сделку**
   - Открыть WebApp → Грузы → «Взять» → подтвердить.
   - Сделка синхронизируется с сервером (outbox), затем в фоне запускается модерация.
   - В админке: http://localhost:8080/admin/deals — в списке должен появиться бейдж AI Risk (LOW/MEDIUM/HIGH).

3. **Загрузить документ**
   - В сделке сформировать документ и скачать PDF (или использовать POST /api/documents с файлом).
   - В фоне создаётся запись в `moderation_review` для `entity_type=document`.

4. **Перезапуск модерации**
   - В карточке сделки (админка): кнопка «Перезапустить модерацию».
   - Или: `POST /api/moderation/deal/{server_id}/run` с заголовком `X-Client-Key` (если задан).

5. **Без LLM**
   - Не задавать `LLM_API_KEY` — все результаты будут с `model_used: rules`.

## API (все с X-Client-Key при заданном CLIENT_SYNC_KEY)

- `GET /api/moderation?entity_type=&risk_level=&q=&limit=&offset=` — список отзывов.
- `GET /api/moderation/{entity_type}/{entity_id}` — отзыв по сделке или документу.
- `POST /api/moderation/{entity_type}/{entity_id}/run` — принудительный перезапуск проверки (возвращает отзыв, статус обновляется в фоне).

Админка: `/admin/deals`, `/admin/deals/{id}`, `/admin/moderation`.
