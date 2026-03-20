// Хранилище перевозчиков (localStorage)

const CARRIERS_STORAGE_KEY = 'gotruckme_carriers_v1';

/**
 * Получить всех перевозчиков
 * @param {string} [ownerId] - Фильтр по владельцу (по умолчанию "me")
 * @param {string} [visibility] - Фильтр по видимости (по умолчанию "PRIVATE")
 * @returns {Array<Carrier>}
 */
function getCarriers(ownerId = 'me', visibility = 'PRIVATE') {
    try {
        const stored = localStorage.getItem(CARRIERS_STORAGE_KEY);
        if (!stored) return [];
        const parsed = JSON.parse(stored);
        const all = Array.isArray(parsed) ? parsed : [];
        
        // Фильтруем по ownerId и visibility
        return all.filter(c => 
            (!ownerId || c.ownerId === ownerId) &&
            (!visibility || c.visibility === visibility)
        );
    } catch (e) {
        console.error('Failed to load carriers:', e);
        return [];
    }
}

/**
 * Сохранить перевозчиков
 * @param {Array<Carrier>} carriers
 */
function saveCarriers(carriers) {
    try {
        localStorage.setItem(CARRIERS_STORAGE_KEY, JSON.stringify(carriers));
    } catch (e) {
        console.error('Failed to save carriers:', e);
    }
}

/**
 * Добавить перевозчика
 * @param {Carrier} carrierData
 * @returns {Carrier}
 */
function addCarrier(carrierData) {
    const allCarriers = getAllCarriers();
    
    // Проверка на дубликаты по ИНН (если есть)
    if (carrierData.inn) {
        const existing = allCarriers.find(c => c.inn && c.inn === carrierData.inn);
        if (existing) {
            throw new Error(`Перевозчик с ИНН ${carrierData.inn} уже существует`);
        }
    }
    
    const newCarrier = {
        id: `carrier_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
        ownerId: 'me',
        visibility: 'PRIVATE',
        ...carrierData,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
    };
    
    allCarriers.push(newCarrier);
    saveCarriers(allCarriers);
    
    return newCarrier;
}

/**
 * Получить всех перевозчиков без фильтров
 * @returns {Array<Carrier>}
 */
function getAllCarriers() {
    try {
        const stored = localStorage.getItem(CARRIERS_STORAGE_KEY);
        if (!stored) return [];
        const parsed = JSON.parse(stored);
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        console.error('Failed to load carriers:', e);
        return [];
    }
}

/**
 * Обновить перевозчика
 * @param {string} carrierId
 * @param {Partial<Carrier>} updates
 * @returns {Carrier|null}
 */
function updateCarrier(carrierId, updates) {
    const allCarriers = getAllCarriers();
    const carrier = allCarriers.find(c => c.id === carrierId);
    
    if (!carrier) {
        throw new Error('Перевозчик не найден');
    }
    
    Object.assign(carrier, updates, {
        updatedAt: new Date().toISOString()
    });
    
    saveCarriers(allCarriers);
    
    return carrier;
}

/**
 * Удалить перевозчика
 * @param {string} carrierId
 */
function deleteCarrier(carrierId) {
    const allCarriers = getAllCarriers();
    const filtered = allCarriers.filter(c => c.id !== carrierId);
    saveCarriers(filtered);
}

/**
 * Получить перевозчика по ID
 * @param {string} carrierId
 * @returns {Carrier|null}
 */
function getCarrierById(carrierId) {
    const allCarriers = getAllCarriers();
    return allCarriers.find(c => c.id === carrierId) || null;
}

/**
 * Поиск перевозчиков
 * @param {string} query - Поисковый запрос (название, ИНН, телефон)
 * @param {CarrierType} [type] - Фильтр по типу
 * @param {string} [ownerId] - Фильтр по владельцу
 * @returns {Array<Carrier>}
 */
function searchCarriers(query, type = null, ownerId = 'me') {
    let filtered = getCarriers(ownerId, 'PRIVATE');
    
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
 * Импорт перевозчиков из текста
 * @param {string} text - Текст для парсинга
 * @returns {Object} - { carriers: Carrier[], errors: string[], stats: { imported, skipped } }
 */
function importCarriers(text) {
    const lines = text.split('\n').filter(line => line.trim());
    const carriers = [];
    const errors = [];
    let imported = 0;
    let skipped = 0;
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        
        try {
            const carrier = parseCarrierLine(line, i + 1);
            if (carrier) {
                carriers.push(carrier);
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
        carriers,
        errors,
        stats: {
            imported,
            skipped,
            total: lines.length
        }
    };
}

/**
 * Парсинг строки перевозчика (улучшенный с распознаванием ключевых слов)
 * @param {string} line
 * @param {number} lineNumber
 * @returns {Carrier|null}
 */
function parseCarrierLine(line, lineNumber) {
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
    const type = determineCarrierType(name);
    
    // Объединяем всю строку для поиска ключевых слов
    const fullLine = line.toLowerCase();
    
    // Извлекаем ИНН
    let inn = null;
    // Сначала ищем "ИНН 123..." или "ИНН: 123..."
    const innPattern = /инн[:\s]+(\d{10,12})/i;
    const innMatch = line.match(innPattern);
    if (innMatch) {
        inn = innMatch[1];
    } else {
        // Ищем ИНН в отдельных частях
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
    }
    
    // Извлекаем телефон из всех полей
    let phone = null;
    for (const part of parts) {
        const phoneMatch = extractPhone(part);
        if (phoneMatch) {
            phone = phoneMatch;
            break;
        }
    }
    
    // Извлекаем паспорт (ищем "Паспорт", "паспорт:", "п/п", "паспортные данные")
    let passport = null;
    const passportKeywords = ['паспорт', 'п/п', 'паспортные данные', 'паспорт:', 'серия паспорта'];
    for (let i = 0; i < parts.length; i++) {
        const part = parts[i].toLowerCase();
        for (const keyword of passportKeywords) {
            if (part.includes(keyword)) {
                // Берем всё после ключевого слова до следующего поля или конца
                const passportIndex = part.indexOf(keyword);
                let passportText = parts[i].substring(part.indexOf(keyword) + keyword.length).trim();
                // Убираем двоеточие и пробелы в начале
                passportText = passportText.replace(/^[:,\s]+/, '');
                // Если есть следующие части, добавляем их до следующего ключевого слова
                if (passportText && passportText.length > 5) {
                    passport = passportText.substring(0, 200);
                } else if (i + 1 < parts.length) {
                    // Берем следующую часть
                    passport = parts[i + 1].substring(0, 200);
                }
                break;
            }
        }
        if (passport) break;
    }
    
    // Извлекаем ТС (ищем "Авто:", "П/п", "госномер", "автомобиль", "машина", "тс:")
    let vehicle = null;
    const vehicleKeywords = ['авто:', 'п/п', 'госномер', 'автомобиль', 'машина', 'тс:', 'номер тс', 'гос. номер'];
    for (let i = 0; i < parts.length; i++) {
        const part = parts[i].toLowerCase();
        for (const keyword of vehicleKeywords) {
            if (part.includes(keyword)) {
                const vehicleIndex = part.indexOf(keyword);
                let vehicleText = parts[i].substring(part.indexOf(keyword) + keyword.length).trim();
                vehicleText = vehicleText.replace(/^[:,\s]+/, '');
                if (vehicleText && vehicleText.length > 2) {
                    vehicle = vehicleText.substring(0, 200);
                } else if (i + 1 < parts.length) {
                    vehicle = parts[i + 1].substring(0, 200);
                }
                break;
            }
        }
        if (vehicle) break;
    }
    
    // Извлекаем платежные реквизиты (ищем "р/с", "к/с", "БИК", "банк", "расчетный счет")
    let paymentDetails = null;
    const paymentKeywords = ['р/с', 'к/с', 'бик', 'банк', 'расчетный счет', 'расч. счет', 'счет'];
    let paymentParts = [];
    for (let i = 0; i < parts.length; i++) {
        const part = parts[i].toLowerCase();
        for (const keyword of paymentKeywords) {
            if (part.includes(keyword)) {
                // Собираем все части, связанные с реквизитами
                paymentParts.push(parts[i]);
                // Добавляем следующие части, пока не встретим другое ключевое слово
                for (let j = i + 1; j < parts.length && j < i + 5; j++) {
                    const nextPart = parts[j].toLowerCase();
                    if (!nextPart.match(/^(паспорт|авто|тс|госномер|телефон|email)/i)) {
                        paymentParts.push(parts[j]);
                    } else {
                        break;
                    }
                }
                break;
            }
        }
    }
    if (paymentParts.length > 0) {
        paymentDetails = paymentParts.join(' ').substring(0, 500);
    } else if (parts.length > 1) {
        // Если нет ключевых слов, но есть вторая колонка - возможно это реквизиты
        paymentDetails = parts[1].substring(0, 500);
    }
    
    // Извлекаем адрес (обычно в отдельной колонке или после адресных ключевых слов)
    let address = null;
    const addressKeywords = ['адрес', 'адр:', 'юр. адрес', 'факт. адрес'];
    for (let i = 0; i < parts.length; i++) {
        const part = parts[i].toLowerCase();
        for (const keyword of addressKeywords) {
            if (part.includes(keyword)) {
                let addressText = parts[i].substring(part.indexOf(keyword) + keyword.length).trim();
                addressText = addressText.replace(/^[:,\s]+/, '');
                if (addressText && addressText.length > 5) {
                    address = addressText.substring(0, 500);
                } else if (i + 1 < parts.length) {
                    address = parts[i + 1].substring(0, 500);
                }
                break;
            }
        }
        if (address) break;
    }
    if (!address && parts.length > 2) {
        address = parts[2].substring(0, 500);
    }
    
    // Извлекаем email
    let email = null;
    for (const part of parts) {
        const emailMatch = part.match(/[\w.-]+@[\w.-]+\.\w+/);
        if (emailMatch) {
            email = emailMatch[0].substring(0, 100);
            break;
        }
    }
    
    // Если нет ни ИНН, ни паспорта, ни названия - пропускаем
    if (!inn && !passport && !name) {
        return null;
    }
    
    return {
        type,
        name: name.substring(0, 200),
        inn: inn || null,
        phone: phone || null,
        email: email || null,
        passport: passport || null,
        vehicle: vehicle || null,
        paymentDetails: paymentDetails || null,
        address: address || null,
        notes: null
    };
}

/**
 * Определить тип перевозчика по названию
 * @param {string} name
 * @returns {CarrierType}
 */
function determineCarrierType(name) {
    if (!name) return 'PERSON';
    const nameLower = name.toLowerCase();
    
    if (nameLower.includes('ип') || nameLower.includes('индивидуальный предприниматель')) {
        return 'IP';
    }
    if (nameLower.includes('ооо') || nameLower.includes('общество')) {
        return 'OOO';
    }
    if (nameLower.includes('каз') || nameLower.includes('kz') || nameLower.includes('бик') || nameLower.includes('bin') || nameLower.includes('иностранн')) {
        return 'FOREIGN';
    }
    return 'PERSON';
}

/**
 * Извлечь телефон из строки
 * @param {string} text
 * @returns {string|null}
 */
function extractPhone(text) {
    if (!text) return null;
    
    // Удаляем все кроме цифр, плюсов и пробелов
    const cleaned = text.replace(/[^\d+\s()-]/g, '');
    
    // Паттерны для телефонов
    const patterns = [
        /\+?7\s?[\(\s]?(\d{3})[\)\s]?\s?(\d{3})[\s-]?(\d{2})[\s-]?(\d{2})/, // +7 (999) 123-45-67
        /8\s?[\(\s]?(\d{3})[\)\s]?\s?(\d{3})[\s-]?(\d{2})[\s-]?(\d{2})/, // 8 (999) 123-45-67
        /(\d{10,11})/, // Просто 10-11 цифр
    ];
    
    for (const pattern of patterns) {
        const match = cleaned.match(pattern);
        if (match) {
            let digits = match[0].replace(/\D/g, '');
            if (digits.startsWith('8')) {
                digits = '7' + digits.substring(1);
            } else if (!digits.startsWith('7')) {
                digits = '7' + digits;
            }
            if (digits.length === 11) {
                return `+7 ${digits[1]}${digits[2]}${digits[3]} ${digits[4]}${digits[5]}${digits[6]}-${digits[7]}${digits[8]}-${digits[9]}${digits[10]}`;
            }
        }
    }
    
    return null;
}

/**
 * Очистить тестовую базу перевозчиков
 */
function clearCarriersDatabase() {
    localStorage.removeItem(CARRIERS_STORAGE_KEY);
}

// Экспорт
if (typeof window !== 'undefined') {
    window.carriersStore = {
        getCarriers,
        addCarrier,
        updateCarrier,
        deleteCarrier,
        getCarrierById,
        searchCarriers,
        importCarriers,
        clearCarriersDatabase
    };
}
