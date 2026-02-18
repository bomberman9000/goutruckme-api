// Словарь лейблов для системы перевозчиков

const CARRIER_LABELS = {
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
        }
    },
    visibility: {
        PRIVATE: {
            label: 'Приватный',
            emoji: '🔒',
            color: 'gray'
        },
        PUBLIC: {
            label: 'Публичный',
            emoji: '🌐',
            color: 'blue'
        }
    }
};

function getCarrierTypeLabel(type) {
    return CARRIER_LABELS.types[type]?.label || type;
}

function getCarrierTypeEmoji(type) {
    return CARRIER_LABELS.types[type]?.emoji || '⚪';
}

function getCarrierTypeColor(type) {
    return CARRIER_LABELS.types[type]?.color || 'gray';
}

// Экспорт
if (typeof window !== 'undefined') {
    window.CarrierLabels = {
        CARRIER_LABELS,
        getCarrierTypeLabel,
        getCarrierTypeEmoji,
        getCarrierTypeColor
    };
}


