// Типы для системы перевозчиков

/**
 * @typedef {"OOO" | "IP" | "PERSON" | "FOREIGN"} CarrierType
 */

/**
 * @typedef {"PRIVATE" | "PUBLIC"} CarrierVisibility
 */

/**
 * @typedef {Object} Carrier
 * @property {string} id
 * @property {CarrierType} type
 * @property {string} name
 * @property {string} [inn]
 * @property {string} [phone]
 * @property {string} [email]
 * @property {string} [passport] - Паспортные данные (одной строкой)
 * @property {string} [vehicle] - Транспортное средство
 * @property {string} [paymentDetails] - Платежные реквизиты (одной строкой)
 * @property {string} [address] - Адрес (одной строкой)
 * @property {string} [postalAddress] - Почтовый адрес
 * @property {string} [notes]
 * @property {string} ownerId - "me" для текущего пользователя
 * @property {CarrierVisibility} visibility - "PRIVATE" для тестовой базы
 * @property {string} createdAt
 * @property {string} updatedAt
 */

// Экспорт
if (typeof window !== 'undefined') {
    window.CarrierTypes = {
        OOO: 'OOO',
        IP: 'IP',
        PERSON: 'PERSON',
        FOREIGN: 'FOREIGN'
    };
    
    window.CarrierVisibility = {
        PRIVATE: 'PRIVATE',
        PUBLIC: 'PUBLIC'
    };
}


