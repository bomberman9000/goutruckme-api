// Словарь лейблов для системы клиентов

const CLIENT_LABELS = {
    types: {
        OOO: {
            label: 'ООО',
            short: 'ООО',
            emoji: '🏢',
            color: 'blue'
        },
        IP: {
            label: 'ИП',
            short: 'ИП',
            emoji: '👤',
            color: 'purple'
        },
        PERSON: {
            label: 'Физ. лицо',
            short: 'ФЛ',
            emoji: '👨',
            color: 'green'
        },
        FOREIGN: {
            label: 'Иностранный',
            short: 'Foreign',
            emoji: '🌍',
            color: 'yellow'
        }
    }
};

function getClientTypeLabel(type) {
    return CLIENT_LABELS.types[type]?.label || type;
}

function getClientTypeEmoji(type) {
    return CLIENT_LABELS.types[type]?.emoji || '⚪';
}

function getClientTypeColor(type) {
    return CLIENT_LABELS.types[type]?.color || 'gray';
}

// Экспорт
if (typeof window !== 'undefined') {
    window.ClientLabels = {
        CLIENT_LABELS,
        getClientTypeLabel,
        getClientTypeEmoji,
        getClientTypeColor
    };
}


