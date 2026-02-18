// Типы для системы документов

/**
 * @typedef {"CONTRACT" | "TTN" | "UPD"} DocumentType
 */

/**
 * @typedef {Object} GeneratedDocument
 * @property {string} id
 * @property {string} dealId
 * @property {DocumentType} type
 * @property {string} createdAt - ISO date string
 * @property {string} fileName
 */

// Экспорт для использования в других модулях
if (typeof window !== 'undefined') {
    window.DocumentTypes = {
        CONTRACT: 'CONTRACT',
        TTN: 'TTN',
        UPD: 'UPD'
    };
}


