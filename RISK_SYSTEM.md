# 🛡️ Система AI-RISK (v1, Rule-Based)

## ✅ Реализовано

### 1. Модель риска

**Типы:**
- `RiskLevel`: `"low" | "medium" | "high"`
- `RiskReason`: `"LOW_PRICE" | "NO_CONTACT" | "PAST_DATE" | "INCOMPLETE_DATA"`
- `RiskResult`: `{ level: RiskLevel, reasons: RiskReason[], score: number }`

### 2. Правила риска

Функция `calculateCargoRisk(cargo, context)` реализована в `/app/static/js/risk/calculateRisk.js`

**Правила:**

1. **LOW_PRICE** — низкая цена
   - Если ставка/км на 30% ниже медианы по похожим грузам (дистанция ±200км, вес ±5т)
   - Или если ставка/км < 8₽ (если нет похожих грузов)

2. **NO_CONTACT** — нет контакта
   - Отсутствует и телефон, и Telegram

3. **PAST_DATE** — дата в прошлом
   - Дата погрузки < сегодня

4. **INCOMPLETE_DATA** — неполные данные
   - Отсутствует вес ИЛИ тип машины

**Логика уровня:**
- `high` → 2+ причины
- `medium` → 1 причина
- `low` → причин нет

### 3. UI: колонка AI-RISK

**Бейджи:**
- 🟢 Low (зелёный)
- 🟡 Medium (жёлтый)
- 🔴 High (красный)

**Tooltip:**
- При наведении на бейдж показывается список причин
- Форматирование: "Причина: Описание"
- Никаких слов "ошибка" — только предупреждения

**Подсветка:**
- Строки с High-риском имеют мягкую подсветку (`bg-red-500/5`)

### 4. UX-фишки

✅ **Фильтр по риску:**
- Выпадающий список: Все / 🟢 Low / 🟡 Medium / 🔴 High
- Расположен в панели фильтров после "Тип кузова"

✅ **Сортировка по риску:**
- Клик по заголовку "AI-риск"
- Сортировка: High → Medium → Low (по убыванию)
- При одинаковом уровне сортируется по score

✅ **Подсветка High:**
- Мягкая подсветка строки (`bg-red-500/5`)
- Не мешает чтению, но привлекает внимание

### 5. Архитектура

**Структура файлов:**
```
app/static/js/risk/
├── types.js          # Типы данных
├── riskLabels.js     # Словарь лейблов (готовность к i18n)
├── riskRules.js      # Правила проверки
└── calculateRisk.js  # Основная функция расчёта
```

**Все тексты через словарь:**
- `riskLabels.js` содержит все лейблы и описания
- Готовность к i18n (интернационализации)

### 6. Мок-данные

Обновлена функция `generateMockLoads()` для создания тестовых грузов:

- **~20%** грузов с подозрительно низкой ценой (для LOW_PRICE)
- **~15%** грузов без контакта (для NO_CONTACT)
- **~10%** грузов с прошедшей датой (для PAST_DATE)
- **~5%** грузов с неполными данными (для INCOMPLETE_DATA)

Риск рассчитывается автоматически при генерации.

## 📁 Где находится

### Модули риска
- `/app/static/js/risk/types.js` — типы
- `/app/static/js/risk/riskLabels.js` — лейблы
- `/app/static/js/risk/riskRules.js` — правила
- `/app/static/js/risk/calculateRisk.js` — расчёт

### Интеграция
- `/app/static/index.html` — основной файл
  - Подключение модулей (строки ~9-12)
  - Генерация мок-данных (строки ~824-912)
  - Фильтрация (строки ~1017-1033)
  - Сортировка (строки ~1035-1065)
  - Рендеринг таблицы (строки ~1304-1384)
  - Фильтр по риску в UI (строки ~188-198)

## 🧪 Как протестировать

1. **Запустите сервер:**
   ```bash
   cd /Users/mac/Projects/atinew/gruzpotok
   source venv/bin/activate
   uvicorn app.api.main:app --host 0.0.0.0 --port 8080 --reload
   ```

2. **Откройте:** `http://localhost:8080`

3. **Проверьте бейджи:**
   - В таблице должны быть бейджи 🟢🟡🔴
   - Наведите на бейдж → должен появиться tooltip с причинами

4. **Проверьте фильтр:**
   - Выберите "🔴 Высокий" в фильтре "AI-риск"
   - Должны остаться только грузы с High-риском

5. **Проверьте сортировку:**
   - Кликните на заголовок "AI-риск"
   - Грузы должны отсортироваться: High → Medium → Low

6. **Проверьте подсветку:**
   - Грузы с High-риском должны иметь мягкую красную подсветку

## 🔧 Как изменить правила

### Добавить новое правило

1. **Добавьте проверку в `riskRules.js`:**
   ```javascript
   function checkNewRule(cargo, context) {
       // Ваша логика
       return condition;
   }
   ```

2. **Добавьте в `calculateRisk.js`:**
   ```javascript
   if (window.RiskRules.checkNewRule(cargo, context)) {
       reasons.push('NEW_RULE');
   }
   ```

3. **Добавьте лейбл в `riskLabels.js`:**
   ```javascript
   NEW_RULE: {
       label: 'Новое правило',
       description: 'Описание',
       warning: true
   }
   ```

### Изменить пороги

**В `riskRules.js`:**
- `checkLowPrice`: измените `0.7` (30%) или `8` (₽/км)
- `checkPastDate`: логика в функции
- `checkIncompleteData`: логика в функции

**В `calculateRisk.js`:**
- Измените логику определения уровня (строки с `if (reasons.length >= 2)`)

## 🚀 TODO: Подключение AI

Для замены rule-based на AI:

1. **Создайте `riskAI.js`:**
   ```javascript
   async function calculateCargoRiskAI(cargo, context) {
       // Вызов AI API (OpenAI, YandexGPT, etc.)
       // Анализ груза через LLM
       // Возврат RiskResult
   }
   ```

2. **Замените в `calculateRisk.js`:**
   ```javascript
   // TODO: Заменить rule-based на AI
   // if (USE_AI) {
   //     return await calculateCargoRiskAI(cargo, context);
   // }
   return calculateCargoRiskRules(cargo, context);
   ```

3. **Добавьте промпт для AI:**
   - Описание правил
   - Примеры
   - Формат ответа

## 📊 Статистика правил

**Текущие правила:**
- ✅ LOW_PRICE — проверка цены
- ✅ NO_CONTACT — проверка контактов
- ✅ PAST_DATE — проверка даты
- ✅ INCOMPLETE_DATA — проверка полноты данных

**Где менять:**
- Правила: `/app/static/js/risk/riskRules.js`
- Логика уровня: `/app/static/js/risk/calculateRisk.js`
- Лейблы: `/app/static/js/risk/riskLabels.js`

## 💡 Почему это больно для ATI

✅ **У них нет объяснимого риска** — мы показываем причины  
✅ **У нас видно "почему опасно"** — tooltip с описанием  
✅ **Даже без AI — ценность выше** — rule-based уже работает  
✅ **Готовность к AI** — легко заменить на LLM

## 🔮 Следующий шаг

После Risk → Сделка → Документы:
- Risk → показываем риск
- Сделка → кнопка "В сделку" (если риск приемлем)
- Документы → автогенерация документов


