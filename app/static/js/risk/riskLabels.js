// Словарь лейблов для системы рисков (готовность к i18n)

const RISK_LABELS = {
    // Уровни риска
    levels: {
        low: {
            label: 'Низкий',
            short: 'Low',
            emoji: '🟢',
            color: 'green'
        },
        medium: {
            label: 'Средний',
            short: 'Medium',
            emoji: '🟡',
            color: 'yellow'
        },
        high: {
            label: 'Высокий',
            short: 'High',
            emoji: '🔴',
            color: 'red'
        }
    },
    
    // Причины риска
    reasons: {
        LOW_PRICE: {
            label: 'Низкая цена',
            description: 'Ставка значительно ниже среднерыночной',
            warning: true // Это предупреждение, не ошибка
        },
        NO_CONTACT: {
            label: 'Нет контакта',
            description: 'Не указан телефон или Telegram',
            warning: true
        },
        PAST_DATE: {
            label: 'Дата в прошлом',
            description: 'Дата погрузки уже прошла',
            warning: true
        },
        INCOMPLETE_DATA: {
            label: 'Неполные данные',
            description: 'Отсутствует вес или тип машины',
            warning: true
        }
    }
};

// Функция для получения лейбла причины
function getReasonLabel(reason) {
    return RISK_LABELS.reasons[reason]?.label || reason;
}

// Функция для получения описания причины
function getReasonDescription(reason) {
    return RISK_LABELS.reasons[reason]?.description || '';
}

// Функция для получения лейбла уровня
function getLevelLabel(level) {
    return RISK_LABELS.levels[level]?.label || level;
}

// Функция для получения эмодзи уровня
function getLevelEmoji(level) {
    return RISK_LABELS.levels[level]?.emoji || '⚪';
}

// Функция для получения цвета уровня
function getLevelColor(level) {
    return RISK_LABELS.levels[level]?.color || 'gray';
}

// Функция для форматирования tooltip
function formatRiskTooltip(reasons) {
    if (!reasons || reasons.length === 0) {
        return 'Риски не обнаружены';
    }
    return reasons.map(reason => {
        const label = getReasonLabel(reason);
        const desc = getReasonDescription(reason);
        return desc ? `${label}: ${desc}` : label;
    }).join('\n');
}

// Экспорт
if (typeof window !== 'undefined') {
    window.RiskLabels = {
        RISK_LABELS,
        getReasonLabel,
        getReasonDescription,
        getLevelLabel,
        getLevelEmoji,
        getLevelColor,
        formatRiskTooltip
    };
}


