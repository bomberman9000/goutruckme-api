// Типы для системы оценки рисков

/**
 * @typedef {"low" | "medium" | "high"} RiskLevel
 */

/**
 * @typedef {"LOW_PRICE" | "NO_CONTACT" | "PAST_DATE" | "INCOMPLETE_DATA"} RiskReason
 */

/**
 * @typedef {Object} RiskResult
 * @property {RiskLevel} level - Уровень риска
 * @property {RiskReason[]} reasons - Массив причин риска
 * @property {number} score - Числовая оценка риска (0-100)
 */

/**
 * @typedef {Object} Cargo
 * @property {number} id
 * @property {string} from_city
 * @property {string} to_city
 * @property {number} price
 * @property {number} [distance]
 * @property {number} [price_per_km]
 * @property {number} [weight]
 * @property {number} [volume]
 * @property {string} [truck_type]
 * @property {string} [loading_date]
 * @property {string} [contact_phone]
 * @property {string} [contact_telegram]
 */

// Экспорт для использования в других модулях
if (typeof window !== 'undefined') {
    window.RiskTypes = {
        RiskLevel: ['low', 'medium', 'high'],
        RiskReason: {
            LOW_PRICE: 'LOW_PRICE',
            NO_CONTACT: 'NO_CONTACT',
            PAST_DATE: 'PAST_DATE',
            INCOMPLETE_DATA: 'INCOMPLETE_DATA'
        }
    };
}


