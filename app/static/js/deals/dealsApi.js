/**
 * API синхронизации сделок с бэкендом.
 * Базовый URL берётся из текущего origin (тот же хост, что и фронт).
 */

const DEALS_SYNC_PREFIX = '/api/deals-sync';

function getBaseUrl() {
    if (typeof window === 'undefined') return '';
    return window.location.origin || '';
}

function getSyncHeaders() {
    var key = (typeof window !== 'undefined' && (window.CLIENT_SYNC_KEY || (window.localStorage && window.localStorage.getItem('gotruckme_client_sync_key')))) || '';
    var h = { 'Content-Type': 'application/json' };
    if (key) h['X-Client-Key'] = key;
    return h;
}

/**
 * Получить все сделки с сервера.
 * @returns {Promise<Array<{server_id: number, local_id: string, payload: object, created_at: string, updated_at: string}>>}
 */
async function getDealsFromServer() {
    var url = getBaseUrl() + DEALS_SYNC_PREFIX;
    var res = await fetch(url, { headers: getSyncHeaders() });
    if (!res.ok) throw new Error('Не удалось загрузить сделки с сервера: ' + res.status);
    return res.json();
}

/**
 * Сохранить сделку на сервере (upsert по local_id). Возвращает {server_id, updated_at}.
 * Для фоновой синхронизации используйте outbox (dealsSyncOutbox.addToOutbox('create', ...)).
 * @param {object} deal - объект сделки с фронта
 * @returns {Promise<{server_id: number, updated_at: string}>}
 */
async function createDealOnServer(deal) {
    var url = getBaseUrl() + DEALS_SYNC_PREFIX;
    var res = await fetch(url, {
        method: 'POST',
        headers: getSyncHeaders(),
        body: JSON.stringify({ local_id: deal.id, payload: deal })
    });
    if (!res.ok) throw new Error('Не удалось сохранить сделку: ' + res.status);
    return res.json();
}

/**
 * Обновить сделку на сервере по server_id.
 * @param {number} serverId - ID на бэкенде
 * @param {object} payload - полный объект сделки (обновлённый)
 * @returns {Promise<{server_id: number, updated_at: string}>}
 */
async function updateDealOnServer(serverId, payload) {
    var url = getBaseUrl() + DEALS_SYNC_PREFIX + '/' + serverId;
    var res = await fetch(url, {
        method: 'PATCH',
        headers: getSyncHeaders(),
        body: JSON.stringify({ payload: payload })
    });
    if (!res.ok) throw new Error('Не удалось обновить сделку: ' + res.status);
    return res.json();
}

if (typeof window !== 'undefined') {
    window.dealsApi = {
        getDealsFromServer,
        createDealOnServer,
        updateDealOnServer
    };
}
