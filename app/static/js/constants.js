// Константы для приложения
export const COLUMNS = {
    CARGO_WEIGHT: 'cargo_weight',
    ROUTE: 'route',
    PRICE: 'price',
    DATE: 'date',
    TRUCK_TYPE: 'truck_type',
    DISTANCE: 'distance',
    RISK: 'risk',
    ACTIONS: 'actions'
};

export const COLUMN_LABELS = {
    [COLUMNS.CARGO_WEIGHT]: 'Груз / вес',
    [COLUMNS.ROUTE]: 'Маршрут',
    [COLUMNS.PRICE]: 'Ставка / за км',
    [COLUMNS.DATE]: 'Дата / время',
    [COLUMNS.TRUCK_TYPE]: 'Тип машины',
    [COLUMNS.DISTANCE]: 'Дистанция',
    [COLUMNS.RISK]: 'AI-риск',
    [COLUMNS.ACTIONS]: 'Действия'
};

export const SORT_ORDERS = {
    ASC: 'asc',
    DESC: 'desc',
    NONE: 'none'
};

export const RISK_LEVELS = {
    LOW: 'low',
    MEDIUM: 'medium',
    HIGH: 'high'
};

export const RISK_COLORS = {
    [RISK_LEVELS.LOW]: 'bg-green-500/20 text-green-400',
    [RISK_LEVELS.MEDIUM]: 'bg-yellow-500/20 text-yellow-400',
    [RISK_LEVELS.HIGH]: 'bg-red-500/20 text-red-400'
};

export const TRUCK_TYPES = [
    'Тент',
    'Рефрижератор',
    'Открытый',
    'Контейнер',
    'Изотерм',
    'Автовоз',
    'Самосвал'
];

export const CITIES = [
    'Москва', 'Санкт-Петербург', 'Казань', 'Нижний Новгород',
    'Екатеринбург', 'Новосибирск', 'Краснодар', 'Ростов-на-Дону',
    'Самара', 'Уфа', 'Воронеж', 'Пермь', 'Волгоград', 'Красноярск'
];

export const ITEMS_PER_PAGE_OPTIONS = [20, 50, 100];


