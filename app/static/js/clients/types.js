// Типы для системы клиентов (контрагентов)

/**
 * @typedef {"OOO" | "IP" | "PERSON" | "FOREIGN"} ClientType
 */

/**
 * @typedef {"PRIVATE" | "ORG"} ClientVisibility
 */

/**
 * @typedef {Object} Client
 * @property {string} id
 * @property {ClientType} type
 * @property {string} name
 * @property {string} [inn]
 * @property {string} [kpp]
 * @property {string} [ogrn]
 * @property {string} [director]
 * @property {string} [directorGenitive]
 * @property {string} [actingBasis]
 * @property {string} [email]
 * @property {string} [phone]
 * @property {string} [paymentDetails]
 * @property {string} [legalAddress]
 * @property {string} [actualAddress]
 * @property {string} [postalAddress]
 * @property {string} [notes]
 * @property {string} ownerId - ID владельца (для PRIVATE_TEST режима всегда "me")
 * @property {ClientVisibility} visibility - Видимость клиента (PRIVATE/ORG)
 * @property {string} createdAt
 * @property {string} updatedAt
 */

// Экспорт
if (typeof window !== 'undefined') {
    window.ClientTypes = {
        OOO: 'OOO',
        IP: 'IP',
        PERSON: 'PERSON',
        FOREIGN: 'FOREIGN'
    };
}


