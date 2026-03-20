// Хранилище клиентов (localStorage)

const CLIENTS_STORAGE_KEY = 'gotruckme_clients_v1';

// Режим приложения (PRIVATE_TEST или ORG)
const APP_MODE = 'PRIVATE_TEST';
const CURRENT_OWNER_ID = 'me';
const CURRENT_VISIBILITY = 'PRIVATE';

/**
 * Получить всех клиентов
 * @returns {Array<Client>}
 */
function getClients() {
    try {
        const stored = localStorage.getItem(CLIENTS_STORAGE_KEY);
        if (!stored) return [];
        const parsed = JSON.parse(stored);
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        console.error('Failed to load clients:', e);
        return [];
    }
}

/**
 * Сохранить клиентов
 * @param {Array<Client>} clients
 */
function saveClients(clients) {
    try {
        localStorage.setItem(CLIENTS_STORAGE_KEY, JSON.stringify(clients));
    } catch (e) {
        console.error('Failed to save clients:', e);
    }
}

/**
 * Добавить клиента
 * @param {Client} clientData
 * @returns {Client}
 */
function addClient(clientData) {
    const clients = getClients();
    
    // Проверка на дубликаты по ИНН (если есть)
    if (clientData.inn) {
        const existing = clients.find(c => c.inn && c.inn === clientData.inn);
        if (existing) {
            throw new Error(`Клиент с ИНН ${clientData.inn} уже существует`);
        }
    }
    
    const newClient = {
        id: `client_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
        ...clientData,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
    };
    
    clients.push(newClient);
    saveClients(clients);
    
    return newClient;
}

/**
 * Обновить клиента
 * @param {string} clientId
 * @param {Partial<Client>} updates
 * @returns {Client|null}
 */
function updateClient(clientId, updates) {
    const clients = getClients();
    const client = clients.find(c => c.id === clientId);
    
    if (!client) {
        throw new Error('Клиент не найден');
    }
    
    Object.assign(client, updates, {
        updatedAt: new Date().toISOString()
    });
    
    saveClients(clients);
    
    return client;
}

/**
 * Удалить клиента
 * @param {string} clientId
 */
function deleteClient(clientId) {
    const clients = getClients();
    const filtered = clients.filter(c => c.id !== clientId);
    saveClients(filtered);
}

/**
 * Получить клиента по ID
 * @param {string} clientId
 * @returns {Client|null}
 */
function getClientById(clientId) {
    const clients = getClients();
    return clients.find(c => c.id === clientId) || null;
}

/**
 * Поиск клиентов
 * @param {string} query - Поисковый запрос (название, ИНН, телефон)
 * @param {ClientType} [type] - Фильтр по типу
 * @returns {Array<Client>}
 */
function searchClients(query, type = null) {
    const clients = getClients();
    let filtered = clients;
    
    // Фильтр по типу
    if (type) {
        filtered = filtered.filter(c => c.type === type);
    }
    
    // Поиск
    if (query && query.trim()) {
        const q = query.toLowerCase().trim();
        filtered = filtered.filter(c => {
            return (
                (c.name && c.name.toLowerCase().includes(q)) ||
                (c.inn && c.inn.includes(q)) ||
                (c.phone && c.phone.includes(q))
            );
        });
    }
    
    return filtered;
}

/**
 * Импорт клиентов из текста
 * @param {string} text - Текст для парсинга
 * @returns {Object} - { clients: Client[], errors: string[], stats: { imported, skipped } }
 */
function importClients(text) {
    const lines = text.split('\n').filter(line => line.trim());
    const clients = [];
    const errors = [];
    let imported = 0;
    let skipped = 0;
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        
        try {
            const client = parseClientLine(line, i + 1);
            if (client) {
                clients.push(client);
                imported++;
            } else {
                skipped++;
            }
        } catch (e) {
            errors.push(`Строка ${i + 1}: ${e.message}`);
            skipped++;
        }
    }
    
    return {
        clients,
        errors,
        stats: {
            imported,
            skipped,
            total: lines.length
        }
    };
}

/**
 * Парсинг строки клиента
 * @param {string} line
 * @param {number} lineNumber
 * @returns {Client|null}
 */
function parseClientLine(line, lineNumber) {
    // Определяем разделитель (табуляция или множественные пробелы)
    let parts = [];
    if (line.includes('\t')) {
        parts = line.split('\t').map(p => p.trim());
    } else {
        // Разделяем по 2+ пробелам
        parts = line.split(/\s{2,}/).map(p => p.trim());
    }
    
    if (parts.length < 1 || !parts[0]) {
        return null;
    }
    
    const name = parts[0].trim();
    if (!name || name.toLowerCase() === 'инн' || name.toLowerCase() === 'наименование') {
        return null;
    }
    
    // Определяем тип организации
    const type = determineClientType(name);
    
    // Извлекаем ИНН из всех полей
    let inn = null;
    for (const part of parts) {
        const innMatch = part.match(/\b\d{10,12}\b/);
        if (innMatch) {
            const candidate = innMatch[0];
            if (candidate.length === 10 || candidate.length === 12) {
                inn = candidate;
                break;
            }
        }
    }
    
    // Если ИНН не найден, пытаемся из колонки ИНН (обычно 3-я или 4-я)
    if (!inn && parts.length > 3) {
        const innCandidate = parts[3] || parts[2];
        if (innCandidate && /^\d{10,12}$/.test(innCandidate.replace(/\s/g, ''))) {
            inn = innCandidate.replace(/\s/g, '');
        }
    }
    
    // Парсим остальные поля
    const paymentDetails = parts[1] || '';
    const address = parts[2] || '';
    const kpp = parts[4] ? parts[4].replace(/\s/g, '') : null;
    const ogrn = parts[5] ? parts[5].replace(/\s/g, '') : null;
    const director = parts[6] || null;
    const directorGenitive = parts[7] || null;
    const actingBasis = parts[8] || null;
    const email = parts[9] ? parts[9].replace(/[""]/g, '') : null;
    const phone = parts[10] || null;
    const postalAddress = parts[11] || null;
    
    // Если нет ИНН, но есть название - создаём клиента без ИНН
    if (!inn && !name) {
        return null;
    }
    
    return {
        type,
        name: name.substring(0, 200),
        inn: inn || null,
        kpp: kpp && kpp.length <= 20 ? kpp : null,
        ogrn: ogrn && ogrn.length <= 20 ? ogrn : null,
        director: director ? director.substring(0, 200) : null,
        directorGenitive: directorGenitive ? directorGenitive.substring(0, 200) : null,
        actingBasis: actingBasis || 'Устава',
        email: email && email.includes('@') ? email.substring(0, 100) : null,
        phone: phone ? normalizePhone(phone) : null,
        paymentDetails: paymentDetails ? paymentDetails.substring(0, 500) : null,
        legalAddress: address ? address.substring(0, 500) : null,
        actualAddress: address ? address.substring(0, 500) : null,
        postalAddress: postalAddress ? postalAddress.substring(0, 500) : null,
        notes: null
    };
}

/**
 * Определить тип клиента по названию
 * @param {string} name
 * @returns {ClientType}
 */
function determineClientType(name) {
    if (!name) return 'OOO';
    const nameLower = name.toLowerCase();
    
    if (nameLower.includes('ип') || nameLower.includes('индивидуальный предприниматель')) {
        return 'IP';
    }
    if (nameLower.includes('физ') || nameLower.includes('физ лицо') || nameLower.includes('частное лицо') || nameLower.includes('паспорт')) {
        return 'PERSON';
    }
    if (nameLower.includes('каз') || nameLower.includes('kz') || nameLower.includes('бик') || nameLower.includes('bin')) {
        return 'FOREIGN';
    }
    return 'OOO';
}

/**
 * Нормализация телефона
 * @param {string} phone
 * @returns {string}
 */
function normalizePhone(phone) {
    if (!phone) return null;
    const digits = phone.replace(/\D/g, '');
    if (digits.length >= 10) {
        let normalized = digits;
        if (normalized.startsWith('8')) {
            normalized = '7' + normalized.substring(1);
        } else if (!normalized.startsWith('7')) {
            normalized = '7' + normalized;
        }
        if (normalized.length === 11) {
            return `+7 ${normalized[1]}${normalized[2]}${normalized[3]} ${normalized[4]}${normalized[5]}${normalized[6]}-${normalized[7]}${normalized[8]}-${normalized[9]}${normalized[10]}`;
        }
    }
    return phone.substring(0, 30);
}

// Экспорт
if (typeof window !== 'undefined') {
    window.clientsStore = {
        getClients,
        addClient,
        updateClient,
        deleteClient,
        getClientById,
        searchClients,
        importClients
    };
}

