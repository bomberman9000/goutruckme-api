// Хранилище сделок (localStorage)

const DEALS_STORAGE_KEY = 'gotruckme_deals_v1';

/**
 * Получить все сделки из localStorage
 * @returns {Array<Deal>}
 */
function getDeals() {
    try {
        const stored = localStorage.getItem(DEALS_STORAGE_KEY);
        if (!stored) return [];
        const parsed = JSON.parse(stored);
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        console.error('Failed to load deals:', e);
        return [];
    }
}

/**
 * Сохранить сделки в localStorage
 * @param {Array<Deal>} deals
 */
function saveDeals(deals) {
    try {
        localStorage.setItem(DEALS_STORAGE_KEY, JSON.stringify(deals));
    } catch (e) {
        console.error('Failed to save deals:', e);
    }
}

/**
 * Активен ли статус (груз "занят" сделкой).
 * Один источник истины по всему проекту.
 * @param {string} status
 * @returns {boolean}
 */
function isActiveStatus(status) {
    return status === 'IN_PROGRESS' || status === 'CONFIRMED';
}

const SNAPSHOT_VERSION = 1;

/**
 * Привести значение к примитиву для snapshot (не прокидывать ссылки на объекты).
 * @param {*} v
 * @param {string|number} [fallback]
 * @returns {string|number|undefined}
 */
function toSnapshotPrimitive(v, fallback) {
    if (v == null) return fallback;
    if (typeof v === 'object') {
        if (typeof v.toISOString === 'function') return v.toISOString();
        if ('code' in v) return String(v.code);
        if ('name' in v) return String(v.name);
        if ('id' in v) return String(v.id);
        return undefined;
    }
    return v;
}

/**
 * Построить снимок груза для сохранения в сделке.
 * Плоский, сериализуемый, без ссылок на backend-модель.
 * @param {Object} cargo - объект груза (load)
 * @returns {CargoSnapshot|null}
 */
function buildCargoSnapshot(cargo) {
    if (!cargo) return null;
    const loadingDate = cargo.loading_date;
    const loadingDateStr = loadingDate == null
        ? undefined
        : (typeof loadingDate === 'string' ? loadingDate : (loadingDate.toISOString ? loadingDate.toISOString() : String(loadingDate)));
    return {
        snapshotVersion: SNAPSHOT_VERSION,
        snapshotAt: new Date().toISOString(),
        id: cargo.id,
        from_city: String(cargo.from_city || ''),
        to_city: String(cargo.to_city || ''),
        price: cargo.price != null ? Number(cargo.price) : 0,
        price_per_km: cargo.price_per_km != null ? Number(cargo.price_per_km) : undefined,
        distance: cargo.distance != null ? Number(cargo.distance) : undefined,
        loading_date: loadingDateStr,
        weight: toSnapshotPrimitive(cargo.weight),
        volume: toSnapshotPrimitive(cargo.volume),
        truck_type: toSnapshotPrimitive(cargo.truck_type) || undefined
    };
}

/**
 * Получить груз для сделки: из списка loads или из snapshot.
 * Один вход для всей логики доступа к данным по сделке.
 * @param {Deal} deal
 * @param {Array} allLoads
 * @returns {Object|null} - груз для отображения или null; __source: "live" | "snapshot"
 */
function getDealCargo(deal, allLoads) {
    const fromList = (allLoads || []).find(l => String(l.id) === String(deal.cargoId));
    if (fromList) return { ...fromList, __source: 'live' };
    if (deal.cargoSnapshot) {
        return { ...deal.cargoSnapshot, id: deal.cargoId, __source: 'snapshot' };
    }
    return null;
}

/** Единые тексты и класс для UI и PDF (один источник формулировок). */
const CARGO_SOURCE_LIVE = { label: 'актуальные данные', pdfText: 'Данные: актуальные', badgeClass: 'bg-green-500/20 text-green-400 border-green-500/30' };
const CARGO_SOURCE_SNAPSHOT = { label: 'данные из сделки (snapshot)', pdfText: 'Данные: из сделки (snapshot)', badgeClass: 'bg-amber-500/20 text-amber-400 border-amber-500/30' };

/**
 * @param {Object|null} cargo - груз с опциональным __source
 * @returns {{ label: string, pdfText: string, badgeClass: string }}
 */
function formatCargoSource(cargo) {
    if (cargo && cargo.__source === 'snapshot') return CARGO_SOURCE_SNAPSHOT;
    return CARGO_SOURCE_LIVE;
}

/**
 * Создать новую сделку
 * @param {string} cargoId
 * @param {Object} carrier - { id, name, phone?, telegram? }
 * @param {Object} [cargo] - груз для snapshot (рекомендуется)
 * @returns {Deal}
 */
function createDeal(cargoId, carrier, cargo) {
    const deals = getDeals();

    if (hasActiveDealForCargo(cargoId)) {
        throw new Error('Груз уже в сделке');
    }

    const newDeal = {
        id: `deal_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
        cargoId: cargoId,
        createdAt: new Date().toISOString(),
        status: 'IN_PROGRESS',
        carrier: {
            id: carrier.id || `carrier_${Date.now()}`,
            name: carrier.name || 'Перевозчик',
            phone: carrier.phone || null,
            telegram: carrier.telegram || null
        },
        counterpartyId: null,
        carrierId: null,
        serverId: null,
        syncedAt: null,
        pendingSync: true,
        lastError: null,
        source: 'local',
        cargoSnapshot: buildCargoSnapshot(cargo) || null
    };

    deals.push(newDeal);
    saveDeals(deals);

    return newDeal;
}

/**
 * Обновить статус сделки
 * @param {string} dealId
 * @param {DealStatus} status
 * @returns {Deal|null}
 */
function updateDealStatus(dealId, status) {
    const deals = getDeals();
    const deal = deals.find(d => d.id === dealId);
    
    if (!deal) {
        throw new Error('Сделка не найдена');
    }
    
    deal.status = status;
    saveDeals(deals);
    
    return deal;
}

/**
 * Установить клиента (контрагента) для сделки
 * @param {string} dealId
 * @param {string} counterpartyId
 * @returns {Deal|null}
 */
function setDealCounterparty(dealId, counterpartyId) {
    const deals = getDeals();
    const deal = deals.find(d => d.id === dealId);
    
    if (!deal) {
        throw new Error('Сделка не найдена');
    }
    
    deal.counterpartyId = counterpartyId;
    saveDeals(deals);
    
    return deal;
}

/**
 * Установить перевозчика для сделки
 * @param {string} dealId
 * @param {string} carrierId
 * @returns {Deal|null}
 */
function setDealCarrierId(dealId, carrierId) {
    const deals = getDeals();
    const deal = deals.find(d => d.id === dealId);
    
    if (!deal) {
        throw new Error('Сделка не найдена');
    }
    
    deal.carrierId = carrierId;
    saveDeals(deals);
    
    return deal;
}

/**
 * Установить serverId, syncedAt после успешной синхронизации; сбросить pendingSync, lastError
 * @param {string} dealId
 * @param {number} serverId
 * @param {string} [syncedAt] - ISO строка (server updated_at)
 * @returns {Deal|null}
 */
function setDealServerSync(dealId, serverId, syncedAt) {
    const deals = getDeals();
    const deal = deals.find(d => d.id === dealId);
    if (!deal) return null;
    deal.serverId = serverId;
    deal.syncedAt = syncedAt || new Date().toISOString();
    deal.pendingSync = false;
    deal.lastError = null;
    deal.source = 'server';
    saveDeals(deals);
    return deal;
}

/**
 * Установить lastError для сделки (при ошибке синхронизации)
 * @param {string} dealId
 * @param {string} message
 */
function setDealLastError(dealId, message) {
    const deals = getDeals();
    const deal = deals.find(d => d.id === dealId);
    if (!deal) return;
    deal.lastError = message;
    deal.pendingSync = true;
    saveDeals(deals);
}

/**
 * Слить сделки с сервера в локальный список с правилами конфликт-резолвинга:
 * - Если у локальной сделки нет syncedAt → сервер главнее.
 * - Если server.updated_at > deal.syncedAt → сервер главнее.
 * - Иначе локаль главнее → оставляем локаль, ставим pendingSync = true.
 * @param {Array<{server_id: number, local_id: string, payload: object, updated_at: string}>} serverList
 */
function mergeDealsFromServer(serverList) {
    if (!Array.isArray(serverList) || serverList.length === 0) return;
    const deals = getDeals();
    for (const row of serverList) {
        const serverUpdatedAt = row.updated_at || row.created_at || '';
        const existing = deals.find(d => d.id === row.local_id);
        if (existing) {
            var serverWins = false;
            if (!existing.syncedAt) {
                serverWins = true;
            } else if (serverUpdatedAt && new Date(serverUpdatedAt) > new Date(existing.syncedAt)) {
                serverWins = true;
            }
            if (serverWins) {
                var p = row.payload || {};
                Object.keys(existing).forEach(function(k) { delete existing[k]; });
                Object.assign(existing, p, {
                    id: row.local_id,
                    serverId: row.server_id,
                    syncedAt: serverUpdatedAt,
                    pendingSync: false,
                    lastError: null,
                    source: 'server'
                });
            } else {
                existing.pendingSync = true;
                existing.serverId = row.server_id;
            }
        } else {
            var payload = row.payload || {};
            if (payload.id === row.local_id) {
                deals.push({
                    ...payload,
                    serverId: row.server_id,
                    syncedAt: serverUpdatedAt,
                    pendingSync: false,
                    lastError: null,
                    source: 'server'
                });
            }
        }
    }
    saveDeals(deals);
}

/**
 * Получить сделку по ID
 * @param {string} dealId
 * @returns {Deal|null}
 */
function getDealById(dealId) {
    const deals = getDeals();
    return deals.find(d => d.id === dealId) || null;
}

/**
 * Проверить, есть ли активная сделка для груза
 * @param {string} cargoId
 * @returns {boolean}
 */
function hasActiveDealForCargo(cargoId) {
    const deals = getDeals();
    return deals.some(d =>
        d.cargoId === String(cargoId) && isActiveStatus(d.status)
    );
}

/**
 * Получить сделку для груза
 * @param {string} cargoId
 * @returns {Deal|null}
 */
function getDealByCargoId(cargoId) {
    const deals = getDeals();
    return deals.find(d => d.cargoId === String(cargoId)) || null;
}

/**
 * Удалить сделку (для отладки)
 * @param {string} dealId
 */
function deleteDeal(dealId) {
    const deals = getDeals();
    const filtered = deals.filter(d => d.id !== dealId);
    saveDeals(filtered);
}

// Экспорт
if (typeof window !== 'undefined') {
    window.dealsStore = {
        getDeals,
        saveDeals,
        createDeal,
        updateDealStatus,
        getDealById,
        hasActiveDealForCargo,
        getDealByCargoId,
        deleteDeal,
        setDealCounterparty,
        setDealCarrierId,
        setDealServerSync,
        setDealLastError,
        mergeDealsFromServer,
        isActiveStatus,
        buildCargoSnapshot,
        getDealCargo,
        formatCargoSource
    };
}
