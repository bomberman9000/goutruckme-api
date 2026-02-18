// Хранилище документов (localStorage)

const STORAGE_KEY = 'gotruckme_documents_v1';

/**
 * Получить все документы из localStorage
 * @returns {Array<GeneratedDocument>}
 */
function getDocuments() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (!stored) return [];
        const parsed = JSON.parse(stored);
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        console.error('Failed to load documents:', e);
        return [];
    }
}

/**
 * Сохранить документы в localStorage
 * @param {Array<GeneratedDocument>} documents
 */
function saveDocuments(documents) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(documents));
    } catch (e) {
        console.error('Failed to save documents:', e);
    }
}

/**
 * Добавить документ
 * @param {string} dealId
 * @param {DocumentType} type
 * @param {string} fileName
 * @returns {GeneratedDocument}
 */
function addDocument(dealId, type, fileName) {
    const documents = getDocuments();
    
    const newDocument = {
        id: `doc_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
        dealId: dealId,
        type: type,
        createdAt: new Date().toISOString(),
        fileName: fileName
    };
    
    documents.push(newDocument);
    saveDocuments(documents);
    
    return newDocument;
}

/**
 * Получить документы по ID сделки
 * @param {string} dealId
 * @returns {Array<GeneratedDocument>}
 */
function getDocumentsByDealId(dealId) {
    const documents = getDocuments();
    return documents.filter(d => d.dealId === dealId);
}

/**
 * Получить документ по ID
 * @param {string} documentId
 * @returns {GeneratedDocument|null}
 */
function getDocumentById(documentId) {
    const documents = getDocuments();
    return documents.find(d => d.id === documentId) || null;
}

/**
 * Проверить, существует ли документ для сделки
 * @param {string} dealId
 * @param {DocumentType} type
 * @returns {boolean}
 */
function hasDocument(dealId, type) {
    const documents = getDocuments();
    return documents.some(d => d.dealId === dealId && d.type === type);
}

// Экспорт
if (typeof window !== 'undefined') {
    window.documentsStore = {
        getDocuments,
        addDocument,
        getDocumentsByDealId,
        getDocumentById,
        hasDocument
    };
}


