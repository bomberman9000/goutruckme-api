// Словарь лейблов для системы сделок (готовность к i18n)

const DEAL_LABELS = {
    // Статусы сделок
    status: {
        IN_PROGRESS: {
            label: 'В работе',
            short: 'В работе',
            emoji: '🟡',
            color: 'yellow',
            badgeClass: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30'
        },
        CONFIRMED: {
            label: 'Подтверждена',
            short: 'Подтверждена',
            emoji: '🔵',
            color: 'blue',
            badgeClass: 'bg-blue-500/20 text-blue-400 border-blue-500/30'
        },
        COMPLETED: {
            label: 'Завершена',
            short: 'Завершена',
            emoji: '🟢',
            color: 'green',
            badgeClass: 'bg-green-500/20 text-green-400 border-green-500/30'
        },
        CANCELLED: {
            label: 'Отменена',
            short: 'Отменена',
            emoji: '🔴',
            color: 'red',
            badgeClass: 'bg-red-500/20 text-red-400 border-red-500/30'
        }
    },
    
    // Действия
    actions: {
        confirm: 'Подтвердить',
        cancel: 'Отменить',
        complete: 'Завершить',
        view: 'Просмотр',
        take: 'Взять',
        inDeal: 'В сделке',
        inProgress: 'В работе'
    }
};

// Функция для получения лейбла статуса
function getStatusLabel(status) {
    return DEAL_LABELS.status[status]?.label || status;
}

// Функция для получения эмодзи статуса
function getStatusEmoji(status) {
    return DEAL_LABELS.status[status]?.emoji || '⚪';
}

// Функция для получения класса бейджа статуса
function getStatusBadgeClass(status) {
    return DEAL_LABELS.status[status]?.badgeClass || 'bg-gray-500/20 text-gray-400 border-gray-500/30';
}

// Функция для получения цвета статуса
function getStatusColor(status) {
    return DEAL_LABELS.status[status]?.color || 'gray';
}

// Экспорт
if (typeof window !== 'undefined') {
    window.DealLabels = {
        DEAL_LABELS,
        getStatusLabel,
        getStatusEmoji,
        getStatusBadgeClass,
        getStatusColor
    };
}


