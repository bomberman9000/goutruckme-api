// Правила оценки рисков для грузов

/**
 * Проверка: низкая цена
 * @param {Object} cargo - Груз
 * @param {Object[]} context - Массив похожих грузов для сравнения
 * @returns {boolean} - true если цена подозрительно низкая
 */
function checkLowPrice(cargo, context) {
    if (!cargo.price || !cargo.distance || cargo.distance === 0) {
        return false;
    }
    
    const cargoPricePerKm = cargo.price / cargo.distance;
    
    // Находим похожие грузы (по дистанции ±200км и весу ±5т)
    const similarCargos = context.filter(c => {
        if (!c.distance || !c.price) return false;
        
        const distanceDiff = Math.abs((c.distance || 0) - cargo.distance);
        const weightDiff = Math.abs((c.weight || 0) - (cargo.weight || 0));
        
        return distanceDiff < 200 && weightDiff < 5;
    });
    
    if (similarCargos.length === 0) {
        // Если нет похожих, используем простое правило: < 8₽/км подозрительно
        return cargoPricePerKm < 8;
    }
    
    // Вычисляем медиану ставки за км
    const pricesPerKm = similarCargos
        .map(c => c.price / c.distance)
        .filter(p => p > 0)
        .sort((a, b) => a - b);
    
    if (pricesPerKm.length === 0) return false;
    
    const median = pricesPerKm.length % 2 === 0
        ? (pricesPerKm[pricesPerKm.length / 2 - 1] + pricesPerKm[pricesPerKm.length / 2]) / 2
        : pricesPerKm[Math.floor(pricesPerKm.length / 2)];
    
    // Если цена на 30% ниже медианы
    return cargoPricePerKm < median * 0.7;
}

/**
 * Проверка: нет контакта
 * @param {Object} cargo - Груз
 * @returns {boolean} - true если нет ни телефона, ни telegram
 */
function checkNoContact(cargo) {
    const hasPhone = cargo.contact_phone && cargo.contact_phone.trim().length > 0;
    const hasTelegram = cargo.contact_telegram && cargo.contact_telegram.trim().length > 0;
    return !hasPhone && !hasTelegram;
}

/**
 * Проверка: неполные данные
 * @param {Object} cargo - Груз
 * @returns {boolean} - true если отсутствует вес или тип машины
 */
function checkIncompleteData(cargo) {
    const hasWeight = cargo.weight && cargo.weight > 0;
    const hasTruckType = cargo.truck_type && cargo.truck_type.trim().length > 0;
    return !hasWeight || !hasTruckType;
}

// Экспорт правил
if (typeof window !== 'undefined') {
    window.RiskRules = {
        checkLowPrice,
        checkNoContact,
        checkIncompleteData
    };
}

