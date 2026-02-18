// Утилиты для работы с данными
import { SORT_ORDERS, RISK_LEVELS } from './constants.js';

export function formatPrice(price) {
    return new Intl.NumberFormat('ru-RU', {
        style: 'currency',
        currency: 'RUB',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(price);
}

export function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric'
    });
}

export function formatTime(dateString) {
    const date = new Date(dateString);
    return date.toLocaleTimeString('ru-RU', {
        hour: '2-digit',
        minute: '2-digit'
    });
}

export function formatDistance(distance) {
    if (!distance) return '—';
    return `${distance.toLocaleString('ru-RU')} км`;
}

export function calculateRiskLevel(load, allLoads) {
    // Простой расчет риска без AI
    let riskScore = 0;
    let reasons = [];
    
    // Проверка цены
    const similarLoads = allLoads.filter(l => 
        Math.abs(l.distance - load.distance) < 200 && 
        Math.abs(l.weight - load.weight) < 5
    );
    
    if (similarLoads.length > 0) {
        const avgPrice = similarLoads.reduce((sum, l) => sum + l.price, 0) / similarLoads.length;
        if (load.price < avgPrice * 0.7) {
            riskScore += 40;
            reasons.push('Низкая цена');
        }
    }
    
    // Проверка контакта
    if (!load.contact_phone && !load.contact_telegram) {
        riskScore += 30;
        reasons.push('Нет контакта');
    }
    
    // Проверка даты
    if (load.loading_date && new Date(load.loading_date) < new Date()) {
        riskScore += 25;
        reasons.push('Ошибка даты');
    }
    
    // Определение уровня
    let riskLevel = RISK_LEVELS.LOW;
    if (riskScore >= 60) {
        riskLevel = RISK_LEVELS.HIGH;
    } else if (riskScore >= 30) {
        riskLevel = RISK_LEVELS.MEDIUM;
    }
    
    return {
        level: riskLevel,
        score: Math.min(riskScore, 100),
        reasons: reasons
    };
}

export function sortLoads(loads, sortColumn, sortOrder) {
    if (sortOrder === SORT_ORDERS.NONE) return loads;
    
    const sorted = [...loads].sort((a, b) => {
        let aVal, bVal;
        
        switch (sortColumn) {
            case 'price':
                aVal = a.price;
                bVal = b.price;
                break;
            case 'date':
                aVal = new Date(a.created_at);
                bVal = new Date(b.created_at);
                break;
            case 'distance':
                aVal = a.distance || 0;
                bVal = b.distance || 0;
                break;
            case 'weight':
                aVal = a.weight || 0;
                bVal = b.weight || 0;
                break;
            default:
                return 0;
        }
        
        if (aVal < bVal) return sortOrder === SORT_ORDERS.ASC ? -1 : 1;
        if (aVal > bVal) return sortOrder === SORT_ORDERS.ASC ? 1 : -1;
        return 0;
    });
    
    return sorted;
}

export function filterLoads(loads, filters) {
    return loads.filter(load => {
        if (filters.from && !load.from_city.toLowerCase().includes(filters.from.toLowerCase())) {
            return false;
        }
        if (filters.to && !load.to_city.toLowerCase().includes(filters.to.toLowerCase())) {
            return false;
        }
        if (filters.weightFrom && load.weight < filters.weightFrom) {
            return false;
        }
        if (filters.weightTo && load.weight > filters.weightTo) {
            return false;
        }
        if (filters.volumeFrom && load.volume < filters.volumeFrom) {
            return false;
        }
        if (filters.volumeTo && load.volume > filters.volumeTo) {
            return false;
        }
        if (filters.truckType && load.truck_type !== filters.truckType) {
            return false;
        }
        if (filters.date && load.loading_date) {
            const loadDate = new Date(load.loading_date).toDateString();
            const filterDate = new Date(filters.date).toDateString();
            if (loadDate !== filterDate) {
                return false;
            }
        }
        return true;
    });
}

export function validateFilters(filters) {
    const errors = {};
    
    if (filters.weightFrom && filters.weightTo && filters.weightFrom > filters.weightTo) {
        errors.weight = 'Минимальный вес не может быть больше максимального';
    }
    
    if (filters.volumeFrom && filters.volumeTo && filters.volumeFrom > filters.volumeTo) {
        errors.volume = 'Минимальный объём не может быть больше максимального';
    }
    
    if (filters.date && new Date(filters.date) < new Date().setHours(0, 0, 0, 0)) {
        errors.date = 'Дата не может быть в прошлом';
    }
    
    return errors;
}

export function getActiveFilters(filters) {
    const active = [];
    
    if (filters.from) active.push({ key: 'from', label: `Откуда: ${filters.from}`, value: filters.from });
    if (filters.to) active.push({ key: 'to', label: `Куда: ${filters.to}`, value: filters.to });
    if (filters.weightFrom || filters.weightTo) {
        const weightLabel = filters.weightFrom && filters.weightTo 
            ? `Вес: ${filters.weightFrom}–${filters.weightTo} т`
            : filters.weightFrom 
                ? `Вес: от ${filters.weightFrom} т`
                : `Вес: до ${filters.weightTo} т`;
        active.push({ key: 'weight', label: weightLabel, value: { from: filters.weightFrom, to: filters.weightTo } });
    }
    if (filters.volumeFrom || filters.volumeTo) {
        const volumeLabel = filters.volumeFrom && filters.volumeTo 
            ? `Объём: ${filters.volumeFrom}–${filters.volumeTo} м³`
            : filters.volumeFrom 
                ? `Объём: от ${filters.volumeFrom} м³`
                : `Объём: до ${filters.volumeTo} м³`;
        active.push({ key: 'volume', label: volumeLabel, value: { from: filters.volumeFrom, to: filters.volumeTo } });
    }
    if (filters.truckType) active.push({ key: 'truckType', label: `Тип: ${filters.truckType}`, value: filters.truckType });
    if (filters.date) active.push({ key: 'date', label: `Дата: ${formatDate(filters.date)}`, value: filters.date });
    
    return active;
}

export function saveToLocalStorage(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
        console.error('Failed to save to localStorage:', e);
    }
}

export function loadFromLocalStorage(key, defaultValue = null) {
    try {
        const item = localStorage.getItem(key);
        return item ? JSON.parse(item) : defaultValue;
    } catch (e) {
        console.error('Failed to load from localStorage:', e);
        return defaultValue;
    }
}


