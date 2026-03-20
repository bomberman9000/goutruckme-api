// Черновики документов (draft поля)

const DOCUMENT_DRAFTS_STORAGE_KEY = 'gotruckme_document_drafts_v1';

/**
 * Получить все черновики
 * @returns {Object} - { "dealId:docType": DocumentDraftFields }
 */
function getDocumentDrafts() {
    try {
        const stored = localStorage.getItem(DOCUMENT_DRAFTS_STORAGE_KEY);
        if (!stored) return {};
        return JSON.parse(stored);
    } catch (e) {
        console.error('Failed to load document drafts:', e);
        return {};
    }
}

/**
 * Сохранить черновик
 * @param {string} dealId
 * @param {string} docType
 * @param {Object} draftFields
 */
function saveDocumentDraft(dealId, docType, draftFields) {
    const drafts = getDocumentDrafts();
    const key = `${dealId}:${docType}`;
    drafts[key] = {
        ...draftFields,
        updatedAt: new Date().toISOString()
    };
    try {
        localStorage.setItem(DOCUMENT_DRAFTS_STORAGE_KEY, JSON.stringify(drafts));
    } catch (e) {
        console.error('Failed to save document draft:', e);
    }
}

/**
 * Получить черновик
 * @param {string} dealId
 * @param {string} docType
 * @returns {Object|null}
 */
function getDocumentDraft(dealId, docType) {
    const drafts = getDocumentDrafts();
    const key = `${dealId}:${docType}`;
    return drafts[key] || null;
}

/**
 * Удалить черновик
 * @param {string} dealId
 * @param {string} docType
 */
function deleteDocumentDraft(dealId, docType) {
    const drafts = getDocumentDrafts();
    const key = `${dealId}:${docType}`;
    delete drafts[key];
    try {
        localStorage.setItem(DOCUMENT_DRAFTS_STORAGE_KEY, JSON.stringify(drafts));
    } catch (e) {
        console.error('Failed to delete document draft:', e);
    }
}

// Экспорт
if (typeof window !== 'undefined') {
    window.documentDrafts = {
        getDocumentDrafts,
        saveDocumentDraft,
        getDocumentDraft,
        deleteDocumentDraft
    };
}

