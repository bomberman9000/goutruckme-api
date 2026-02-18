/**
 * Очередь синхронизации сделок (localStorage outbox).
 * Операции: create (POST upsert), update (PATCH).
 * При ошибке: остаётся в очереди, retryCount++, backoff до следующей попытки.
 */

const OUTBOX_KEY = 'gotruckme_sync_outbox_v1';
const BACKOFF_BASE_SEC = 5;
const BACKOFF_MAX_SEC = 300;  // 5 min

function getOutbox() {
    try {
        var raw = localStorage.getItem(OUTBOX_KEY);
        if (!raw) return [];
        var arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
    } catch (e) {
        return [];
    }
}

function saveOutbox(arr) {
    try {
        localStorage.setItem(OUTBOX_KEY, JSON.stringify(arr));
    } catch (e) {
        console.error('dealsSyncOutbox save:', e);
    }
}

/**
 * Добавить операцию в outbox.
 * @param {string} type - 'create' | 'update'
 * @param {string} localId - id сделки на клиенте
 * @param {object} payload - полный объект сделки (для POST/PATCH body)
 * @param {number} [serverId] - для type 'update'
 * @returns {string} id операции в outbox
 */
function addToOutbox(type, localId, payload, serverId) {
    var box = getOutbox();
    var id = 'out_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    box.push({
        id: id,
        type: type,
        localId: localId,
        serverId: serverId || null,
        payload: payload,
        ts: new Date().toISOString(),
        retryCount: 0,
        nextRetryAt: null,
        lastError: null
    });
    saveOutbox(box);
    return id;
}

function removeFromOutbox(opId) {
    var box = getOutbox().filter(function(op) { return op.id !== opId; });
    saveOutbox(box);
}

/**
 * Вычислить время следующей попытки (backoff).
 * @param {number} retryCount
 * @returns {number} Unix ms
 */
function getNextRetryAt(retryCount) {
    var sec = Math.min(BACKOFF_BASE_SEC * Math.pow(2, retryCount), BACKOFF_MAX_SEC);
    return Date.now() + sec * 1000;
}

/**
 * Заголовки для запросов (X-Client-Key если задан).
 */
function getSyncHeaders() {
    var key = (typeof window !== 'undefined' && (window.CLIENT_SYNC_KEY || (window.localStorage && window.localStorage.getItem('gotruckme_client_sync_key')))) || '';
    var h = { 'Content-Type': 'application/json' };
    if (key) h['X-Client-Key'] = key;
    return h;
}

/**
 * Обработать один элемент outbox: отправить на сервер, при успехе удалить и обновить сделку, при ошибке — backoff.
 * @param {object} op - элемент outbox
 * @returns {Promise<'ok'|'retry'>}
 */
function processOne(op) {
    var now = Date.now();
    if (op.nextRetryAt != null && op.nextRetryAt > now) return Promise.resolve('retry');
    var baseUrl = (typeof window !== 'undefined' && window.location && window.location.origin) || '';
    var prefix = baseUrl + '/api/deals-sync';
    var headers = getSyncHeaders();

    if (op.type === 'create') {
        return fetch(prefix, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ local_id: op.localId, payload: op.payload })
        }).then(function(res) {
            if (!res.ok) throw new Error(res.status + ' ' + res.statusText);
            return res.json();
        }).then(function(data) {
            removeFromOutbox(op.id);
            if (window.dealsStore && data && data.server_id != null) {
                window.dealsStore.setDealServerSync(op.localId, data.server_id, data.updated_at);
            }
            return 'ok';
        }).catch(function(err) {
            var box = getOutbox();
            var item = box.find(function(o) { return o.id === op.id; });
            if (item) {
                item.retryCount = (item.retryCount || 0) + 1;
                item.nextRetryAt = getNextRetryAt(item.retryCount);
                item.lastError = err && err.message ? err.message : String(err);
                saveOutbox(box);
            }
            if (window.dealsStore && window.dealsStore.setDealLastError) {
                window.dealsStore.setDealLastError(op.localId, item && item.lastError ? item.lastError : 'Sync failed');
            }
            return 'retry';
        });
    }

    if (op.type === 'update' && op.serverId != null) {
        return fetch(prefix + '/' + op.serverId, {
            method: 'PATCH',
            headers: headers,
            body: JSON.stringify({ payload: op.payload })
        }).then(function(res) {
            if (!res.ok) throw new Error(res.status + ' ' + res.statusText);
            return res.json();
        }).then(function(data) {
            removeFromOutbox(op.id);
            if (window.dealsStore && data && data.updated_at) {
                window.dealsStore.setDealServerSync(op.localId, op.serverId, data.updated_at);
            }
            return 'ok';
        }).catch(function(err) {
            var box = getOutbox();
            var item = box.find(function(o) { return o.id === op.id; });
            if (item) {
                item.retryCount = (item.retryCount || 0) + 1;
                item.nextRetryAt = getNextRetryAt(item.retryCount);
                item.lastError = err && err.message ? err.message : String(err);
                saveOutbox(box);
            }
            if (window.dealsStore && window.dealsStore.setDealLastError) {
                window.dealsStore.setDealLastError(op.localId, item && item.lastError ? item.lastError : 'Sync failed');
            }
            return 'retry';
        });
    }

    return Promise.resolve('retry');
}

/**
 * Пройти по outbox и отправить все, у которых nextRetryAt <= now или не задан.
 */
function processOutbox() {
    var box = getOutbox();
    var now = Date.now();
    var eligible = box.filter(function(op) {
        return op.nextRetryAt == null || op.nextRetryAt <= now;
    });
    if (eligible.length === 0) return Promise.resolve();
    return eligible.reduce(function(p, op) {
        return p.then(function() { return processOne(op); });
    }, Promise.resolve());
}

/**
 * Запуск воркера: интервал 15–30 сек + onOnline.
 */
function startSyncWorker() {
    if (typeof window === 'undefined') return;
    var intervalMs = 15000;
    window.dealsSyncWorkerTimer = setInterval(processOutbox, intervalMs);
    window.addEventListener('online', function() { processOutbox(); });
    processOutbox();
}

function stopSyncWorker() {
    if (typeof window !== 'undefined' && window.dealsSyncWorkerTimer) {
        clearInterval(window.dealsSyncWorkerTimer);
        window.dealsSyncWorkerTimer = null;
    }
}

if (typeof window !== 'undefined') {
    window.dealsSyncOutbox = {
        getOutbox: getOutbox,
        addToOutbox: addToOutbox,
        removeFromOutbox: removeFromOutbox,
        processOutbox: processOutbox,
        getSyncHeaders: getSyncHeaders,
        startSyncWorker: startSyncWorker,
        stopSyncWorker: stopSyncWorker
    };
}
