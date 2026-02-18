// Типы для системы сделок

/**
 * @typedef {"IN_PROGRESS" | "CONFIRMED" | "COMPLETED" | "CANCELLED"} DealStatus
 */

/**
 * @typedef {Object} Carrier
 * @property {string} id
 * @property {string} name
 * @property {string} [phone]
 * @property {string} [telegram]
 */

/**
 * Снимок груза на момент создания сделки (источник истины для отображения).
 * Только примитивы и строки, без Date и вложенных объектов — сериализуемо в localStorage.
 * @typedef {Object} CargoSnapshot
 * @property {number} snapshotVersion - версия формата (при добавлении полей — миграция по версии)
 * @property {string} snapshotAt - ISO дата создания снимка
 * @property {string|number} id
 * @property {string} from_city
 * @property {string} to_city
 * @property {number} price
 * @property {number} [price_per_km]
 * @property {number} [distance]
 * @property {string} [loading_date] - только строка (ISO или с API)
 * @property {string|number} [weight]
 * @property {string|number} [volume]
 * @property {string} [truck_type] - только строка (не объект)
 */

/**
 * @typedef {Object} Deal
 * @property {string} id
 * @property {string} cargoId
 * @property {string} createdAt - ISO date string
 * @property {DealStatus} status
 * @property {Carrier} carrier
 * @property {number|null} [serverId] - ID на бэкенде (для миграции)
 * @property {string|null} [syncedAt] - ISO дата последней синхронизации
 * @property {"local"|"server"} [source] - источник сделки
 * @property {CargoSnapshot|null} [cargoSnapshot] - снимок груза при создании
 */

// Экспорт для использования в других модулях
if (typeof window !== 'undefined') {
    window.DealTypes = {
        DealStatus: {
            IN_PROGRESS: 'IN_PROGRESS',
            CONFIRMED: 'CONFIRMED',
            COMPLETED: 'COMPLETED',
            CANCELLED: 'CANCELLED'
        }
    };
}


