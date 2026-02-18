// Профиль компании (заказчик/экспедитор по умолчанию)

const STORAGE_KEY = 'gotruckme_company_profile';

/**
 * Получить профиль компании
 * @returns {Object|null}
 */
function getCompanyProfile() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (!stored) return null;
        return JSON.parse(stored);
    } catch (e) {
        console.error('Failed to load company profile:', e);
        return null;
    }
}

/**
 * Сохранить профиль компании
 * @param {Object} profile
 */
function saveCompanyProfile(profile) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
    } catch (e) {
        console.error('Failed to save company profile:', e);
    }
}

/**
 * Проверить, есть ли профиль компании
 * @returns {boolean}
 */
function hasCompanyProfile() {
    return getCompanyProfile() !== null;
}

// Экспорт
if (typeof window !== 'undefined') {
    window.companyProfile = {
        getCompanyProfile,
        saveCompanyProfile,
        hasCompanyProfile
    };
}


