// Основная функция расчёта риска для груза

/**
 * Рассчитывает уровень риска для груза на основе правил
 * @param {Object} cargo - Груз для оценки
 * @param {Object[]} context - Массив всех грузов для контекста
 * @returns {Object} - { level: "low"|"medium"|"high", reasons: string[], score: number }
 */
function calculateCargoRisk(cargo, context = []) {
    const reasons = [];
    
    // Применяем правила
    if (window.RiskRules.checkLowPrice(cargo, context)) {
        reasons.push('LOW_PRICE');
    }
    
    if (window.RiskRules.checkNoContact(cargo)) {
        reasons.push('NO_CONTACT');
    }
    
    if (window.RiskRules.checkIncompleteData(cargo)) {
        reasons.push('INCOMPLETE_DATA');
    }
    
    // Определяем уровень риска
    let level = 'low';
    let score = 0;
    
    // Если слишком дёшево (LOW_PRICE) → medium/high даже при одном факторе
    const hasLowPrice = reasons.includes('LOW_PRICE');
    
    if (reasons.length >= 2) {
        level = 'high';
        score = Math.min(60 + (reasons.length - 2) * 15, 100);
    } else if (reasons.length === 1) {
        // Если единственная причина - низкая цена, делаем medium
        if (hasLowPrice) {
            level = 'medium';
            score = 40 + Math.floor(Math.random() * 15); // 40-55
        } else {
            level = 'medium';
            score = 30 + Math.floor(Math.random() * 20); // 30-50
        }
    } else {
        level = 'low';
        score = Math.floor(Math.random() * 20); // 0-20
    }
    
    return {
        level,
        reasons,
        score
    };
}

// Экспорт
if (typeof window !== 'undefined') {
    window.calculateCargoRisk = calculateCargoRisk;
}
