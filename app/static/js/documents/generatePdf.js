// Генерация PDF документов

/**
 * Генерация номера документа
 * @param {string} prefix - Префикс (ДОГ, ТТН, УПД)
 * @returns {string}
 */
function generateDocumentNumber(prefix) {
    const date = new Date();
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const random = Math.floor(Math.random() * 10000).toString().padStart(4, '0');
    return `${prefix}-${year}${month}${day}-${random}`;
}

/**
 * Форматирование даты для документов
 * @param {string|Date} date
 * @returns {string}
 */
function formatDocumentDate(date) {
    const d = new Date(date);
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const year = d.getFullYear();
    return `${day}.${month}.${year}`;
}

/**
 * Генерация договора перевозки
 * @param {Object} deal - Сделка
 * @param {Object} cargo - Груз
 * @param {Object} companyProfile - Профиль компании (заказчик)
 * @param {Object} draftFields - Поля черновика (время, адреса, условия)
 * @returns {string} - Имя файла
 */
function generateContractPDF(deal, cargo, companyProfile = null, draftFields = null) {
    if (typeof window.jspdf === 'undefined') {
        alert('❌ Библиотека jsPDF не загружена');
        return null;
    }
    if (!cargo) {
        alert('Груз не найден');
        return null;
    }

    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    const yHeaderBase = 35;
    const yTitle = yHeaderBase - 15;
    const yBodyStart = yHeaderBase + 15;
    const pageW = doc.internal.pageSize.getWidth();

    const docNumber = generateDocumentNumber('ДОГ');
    const docDate = formatDocumentDate(deal.createdAt);

    // Заголовок
    doc.setFontSize(16);
    doc.setFont(undefined, 'bold');
    doc.text('ДОГОВОР ПЕРЕВОЗКИ ГРУЗА', pageW / 2, yTitle, { align: 'center' });

    // Номер, дата и источник данных
    doc.setFontSize(12);
    doc.setFont(undefined, 'normal');
    doc.text(`№ ${docNumber}`, 20, yHeaderBase);
    doc.text(`от ${docDate} г.`, pageW / 2, yHeaderBase, { align: 'center' });
    const sourceFmt = window.dealsStore && window.dealsStore.formatCargoSource ? window.dealsStore.formatCargoSource(cargo) : { pdfText: 'Данные: актуальные' };
    doc.setFontSize(8);
    doc.setTextColor(120, 120, 120);
    doc.text(sourceFmt.pdfText, 20, yHeaderBase + 7);
    doc.setTextColor(0, 0, 0);
    doc.setFontSize(11);

    let y = yBodyStart;

    // Стороны
    doc.setFontSize(11);
    doc.setFont(undefined, 'bold');
    doc.text('ЗАКАЗЧИК:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    // Используем профиль компании, если есть
    const customerName = companyProfile ? companyProfile.name : (cargo.organization_name || 'Не указано');
    const customerInn = companyProfile ? companyProfile.inn : (cargo.inn || 'Не указано');
    const customerAddress = companyProfile ? companyProfile.address : '';
    const customerPhone = companyProfile ? companyProfile.phone : '';
    const customerDirector = companyProfile ? companyProfile.director : '';
    
    doc.text(`Организация: ${customerName}`, 20, y);
    y += 6;
    doc.text(`ИНН: ${customerInn}`, 20, y);
    if (companyProfile && companyProfile.ogrn) {
        y += 6;
        doc.text(`ОГРН: ${companyProfile.ogrn}`, 20, y);
    }
    if (customerAddress) {
        y += 6;
        doc.text(`Адрес: ${customerAddress}`, 20, y);
    }
    if (customerPhone) {
        y += 6;
        doc.text(`Телефон: ${customerPhone}`, 20, y);
    }
    
    y += 5;
    doc.setFont(undefined, 'bold');
    doc.text('КОНТРАГЕНТ (КЛИЕНТ):', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    // Получаем клиента из базы, если выбран
    const counterparty = deal.counterpartyId && window.clientsStore ? 
        window.clientsStore.getClientById(deal.counterpartyId) : null;
    
    if (counterparty) {
        doc.text(`Организация: ${counterparty.name}`, 20, y);
        y += 6;
        if (counterparty.inn) {
            doc.text(`ИНН: ${counterparty.inn}`, 20, y);
            y += 6;
        }
        if (counterparty.kpp) {
            doc.text(`КПП: ${counterparty.kpp}`, 20, y);
            y += 6;
        }
        if (counterparty.ogrn) {
            doc.text(`ОГРН: ${counterparty.ogrn}`, 20, y);
            y += 6;
        }
        if (counterparty.director) {
            doc.text(`Директор: ${counterparty.director}`, 20, y);
            y += 6;
        }
        if (counterparty.actingBasis) {
            doc.text(`На основании: ${counterparty.actingBasis}`, 20, y);
            y += 6;
        }
        if (counterparty.legalAddress) {
            doc.text(`Адрес: ${counterparty.legalAddress}`, 20, y, { maxWidth: 170 });
            y += 8;
        }
        if (counterparty.phone) {
            doc.text(`Телефон: ${counterparty.phone}`, 20, y);
            y += 6;
        }
        if (counterparty.email) {
            doc.text(`Email: ${counterparty.email}`, 20, y);
            y += 6;
        }
        if (counterparty.paymentDetails) {
            doc.text(`Реквизиты: ${counterparty.paymentDetails}`, 20, y, { maxWidth: 170 });
            y += 10;
        }
    } else {
        doc.text('Клиент не выбран', 20, y);
        y += 6;
    }
    
    y += 5;
    doc.setFont(undefined, 'bold');
    doc.text('ПЕРЕВОЗЧИК:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    
    // Получаем перевозчика из базы, если выбран
    const carrier = deal.carrierId && window.carriersStore ? 
        window.carriersStore.getCarrierById(deal.carrierId) : null;
    
    if (carrier) {
        doc.text(`Организация: ${carrier.name}`, 20, y);
        y += 6;
        if (carrier.inn) {
            doc.text(`ИНН: ${carrier.inn}`, 20, y);
            y += 6;
        }
        if (carrier.type === 'PERSON' && carrier.passport) {
            doc.text(`Паспорт: ${carrier.passport}`, 20, y, { maxWidth: 170 });
            y += 8;
        }
        if (carrier.address) {
            doc.text(`Адрес: ${carrier.address}`, 20, y, { maxWidth: 170 });
            y += 8;
        }
        if (carrier.phone) {
            doc.text(`Телефон: ${carrier.phone}`, 20, y);
            y += 6;
        }
        if (carrier.email) {
            doc.text(`Email: ${carrier.email}`, 20, y);
            y += 6;
        }
        if (carrier.paymentDetails) {
            doc.text(`Реквизиты: ${carrier.paymentDetails}`, 20, y, { maxWidth: 170 });
            y += 10;
        }
        if (carrier.vehicle) {
            doc.text(`ТС: ${carrier.vehicle}`, 20, y, { maxWidth: 170 });
            y += 8;
        }
    } else {
        // Если перевозчик не выбран
        doc.text('Перевозчик не выбран', 20, y);
        y += 6;
        // Используем данные из deal.carrier (для обратной совместимости)
        if (deal.carrier && deal.carrier.name) {
            doc.text(`Организация: ${deal.carrier.name}`, 20, y);
            y += 6;
            if (deal.carrier.phone) {
                doc.text(`Телефон: ${deal.carrier.phone}`, 20, y);
                y += 6;
            }
        }
    }
    
    y += 10;
    
    // Предмет договора
    doc.setFont(undefined, 'bold');
    doc.text('1. ПРЕДМЕТ ДОГОВОРА', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    const route = `${cargo.from_city} → ${cargo.to_city}`;
    doc.text(`Перевозчик обязуется перевезти груз по маршруту: ${route}`, 20, y);
    y += 6;
    
    // Адреса из черновика, если есть
    if (draftFields && draftFields.pickupAddress) {
        doc.text(`Адрес загрузки: ${draftFields.pickupAddress}`, 20, y);
        y += 6;
    }
    if (draftFields && draftFields.deliveryAddress) {
        doc.text(`Адрес выгрузки: ${draftFields.deliveryAddress}`, 20, y);
        y += 6;
    }
    
    doc.text(`Вес: ${cargo.weight || '—'} т, Объём: ${cargo.volume || '—'} м³`, 20, y);
    y += 6;
    doc.text(`Тип ТС: ${cargo.truck_type || '—'}`, 20, y);
    if (cargo.loading_date) {
        y += 6;
        const loadingDateText = formatDocumentDate(cargo.loading_date);
        const loadingTimeText = draftFields && draftFields.pickupTime ? `, время: ${draftFields.pickupTime}` : '';
        doc.text(`Дата погрузки: ${loadingDateText}${loadingTimeText}`, 20, y);
    }
    if (draftFields && draftFields.deliveryTime) {
        y += 6;
        doc.text(`Время выгрузки: ${draftFields.deliveryTime}`, 20, y);
    }
    if (draftFields && draftFields.specialTerms) {
        y += 6;
        doc.text(`Особые условия: ${draftFields.specialTerms}`, 20, y, { maxWidth: 170 });
        y += 10;
    }
    
    y += 10;
    
    // Стоимость
    doc.setFont(undefined, 'bold');
    doc.text('2. СТОИМОСТЬ УСЛУГ', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    doc.text(`Стоимость перевозки составляет: ${cargo.price.toLocaleString('ru-RU')} руб.`, 20, y);
    y += 6;
    if (cargo.distance) {
        doc.text(`Расстояние: ${cargo.distance} км`, 20, y);
        y += 6;
        const pricePerKm = Math.round(cargo.price / cargo.distance);
        doc.text(`Ставка за км: ${pricePerKm} руб./км`, 20, y);
    }
    
    y += 10;
    
    // Подписи
    doc.setFont(undefined, 'bold');
    doc.text('ЗАКАЗЧИК:', 20, y);
    doc.text('ПЕРЕВОЗЧИК:', 110, y);
    y += 15;
    doc.setFont(undefined, 'normal');
    doc.text('_________________', 20, y);
    doc.text('_________________', 110, y);
    y += 5;
    doc.text('(подпись)', 20, y);
    doc.text('(подпись)', 110, y);
    y += 10;
    doc.text('М.П.', 20, y);
    doc.text('М.П.', 110, y);
    
    // Печать (заглушка)
    doc.setDrawColor(200, 200, 200);
    doc.setFillColor(240, 240, 240);
    doc.circle(30, y - 5, 8, 'FD');
    doc.setFontSize(8);
    doc.text('Печать', 25, y - 2, { align: 'center' });
    
    const fileName = `Договор_${docNumber}.pdf`;
    doc.save(fileName);
    
    return fileName;
}

/**
 * Генерация ТТН (Товарно-транспортная накладная)
 * @param {Object} deal - Сделка
 * @param {Object} cargo - Груз
 * @param {Object} companyProfile - Профиль компании (заказчик)
 * @param {Object} draftFields - Поля черновика (время, адреса, условия)
 * @returns {string} - Имя файла
 */
function generateTTNPDF(deal, cargo, companyProfile = null, draftFields = null) {
    if (typeof window.jspdf === 'undefined') {
        alert('❌ Библиотека jsPDF не загружена');
        return null;
    }
    if (!cargo) {
        alert('Груз не найден');
        return null;
    }

    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    const yHeaderBase = 35;
    const yTitle = yHeaderBase - 15;
    const yBodyStart = yHeaderBase + 15;
    const pageW = doc.internal.pageSize.getWidth();

    const docNumber = generateDocumentNumber('ТТН');
    const docDate = formatDocumentDate(deal.createdAt);

    // Заголовок
    doc.setFontSize(16);
    doc.setFont(undefined, 'bold');
    doc.text('ТОВАРНО-ТРАНСПОРТНАЯ НАКЛАДНАЯ', pageW / 2, yTitle, { align: 'center' });

    // Номер, дата и источник данных
    doc.setFontSize(12);
    doc.setFont(undefined, 'normal');
    doc.text(`№ ${docNumber}`, 20, yHeaderBase);
    doc.text(`от ${docDate} г.`, pageW / 2, yHeaderBase, { align: 'center' });
    const sourceFmt = window.dealsStore && window.dealsStore.formatCargoSource ? window.dealsStore.formatCargoSource(cargo) : { pdfText: 'Данные: актуальные' };
    doc.setFontSize(8);
    doc.setTextColor(120, 120, 120);
    doc.text(sourceFmt.pdfText, 20, yHeaderBase + 7);
    doc.setTextColor(0, 0, 0);
    doc.setFontSize(11);

    let y = yBodyStart;

    // Грузоотправитель
    doc.setFontSize(11);
    doc.setFont(undefined, 'bold');
    doc.text('ГРУЗООТПРАВИТЕЛЬ:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    const shipperName = companyProfile ? companyProfile.name : (cargo.organization_name || 'Не указано');
    const shipperInn = companyProfile ? companyProfile.inn : (cargo.inn || 'Не указано');
    const shipperAddress = draftFields && draftFields.pickupAddress ? draftFields.pickupAddress : 
                          (companyProfile ? companyProfile.address : `${cargo.from_city}${cargo.from_address ? ', ' + cargo.from_address : ''}`);
    
    doc.text(`Организация: ${shipperName}`, 20, y);
    y += 6;
    doc.text(`Адрес: ${shipperAddress}`, 20, y);
    y += 6;
    doc.text(`ИНН: ${shipperInn}`, 20, y);
    
    y += 10;
    
    // Грузополучатель
    doc.setFont(undefined, 'bold');
    doc.text('ГРУЗОПОЛУЧАТЕЛЬ:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    
    // Если выбран клиент, используем его данные
    const counterparty = deal.counterpartyId && window.clientsStore ? 
        window.clientsStore.getClientById(deal.counterpartyId) : null;
    
    if (counterparty) {
        doc.text(`Организация: ${counterparty.name}`, 20, y);
        y += 6;
        if (counterparty.inn) {
            doc.text(`ИНН: ${counterparty.inn}`, 20, y);
            y += 6;
        }
        if (counterparty.kpp) {
            doc.text(`КПП: ${counterparty.kpp}`, 20, y);
            y += 6;
        }
        const consigneeAddress = draftFields && draftFields.deliveryAddress ? draftFields.deliveryAddress : 
                                (counterparty.legalAddress || `${cargo.to_city}${cargo.to_address ? ', ' + cargo.to_address : ''}`);
        doc.text(`Адрес: ${consigneeAddress}`, 20, y, { maxWidth: 170 });
        y += 8;
    } else {
        doc.text(`Адрес: ${cargo.to_city}${cargo.to_address ? ', ' + cargo.to_address : ''}`, 20, y);
        y += 6;
    }
    
    y += 10;
    
    // Перевозчик
    doc.setFont(undefined, 'bold');
    doc.text('ПЕРЕВОЗЧИК:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    
    // Получаем перевозчика из базы, если выбран
    const carrier = deal.carrierId && window.carriersStore ? 
        window.carriersStore.getCarrierById(deal.carrierId) : null;
    
    if (carrier) {
        doc.text(`Организация: ${carrier.name}`, 20, y);
        y += 6;
        if (carrier.inn) {
            doc.text(`ИНН: ${carrier.inn}`, 20, y);
            y += 6;
        }
        if (carrier.type === 'PERSON' && carrier.passport) {
            doc.text(`Паспорт: ${carrier.passport}`, 20, y, { maxWidth: 170 });
            y += 8;
        }
        if (carrier.address) {
            doc.text(`Адрес: ${carrier.address}`, 20, y, { maxWidth: 170 });
            y += 8;
        }
        if (carrier.phone) {
            doc.text(`Телефон: ${carrier.phone}`, 20, y);
            y += 6;
        }
        if (carrier.vehicle) {
            doc.text(`ТС: ${carrier.vehicle}`, 20, y);
            y += 6;
        }
    } else {
        // Если перевозчик не выбран
        doc.text('Перевозчик не выбран', 20, y);
        y += 6;
        // Используем данные из deal.carrier (для обратной совместимости)
        if (deal.carrier && deal.carrier.name) {
            doc.text(`Организация: ${deal.carrier.name}`, 20, y);
            if (deal.carrier.phone) {
                y += 6;
                doc.text(`Телефон: ${deal.carrier.phone}`, 20, y);
            }
        }
    }
    
    y += 10;
    
    // Маршрут
    doc.setFont(undefined, 'bold');
    doc.text('МАРШРУТ:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    const pickupAddr = draftFields && draftFields.pickupAddress ? draftFields.pickupAddress : 
                       `${cargo.from_city}${cargo.from_address ? ', ' + cargo.from_address : ''}`;
    const deliveryAddr = draftFields && draftFields.deliveryAddress ? draftFields.deliveryAddress : 
                         `${cargo.to_city}${cargo.to_address ? ', ' + cargo.to_address : ''}`;
    
    doc.text(`Откуда: ${pickupAddr}`, 20, y);
    y += 6;
    if (draftFields && draftFields.pickupTime) {
        doc.text(`Время погрузки: ${draftFields.pickupTime}`, 20, y);
        y += 6;
    }
    doc.text(`Куда: ${deliveryAddr}`, 20, y);
    y += 6;
    if (draftFields && draftFields.deliveryTime) {
        doc.text(`Время выгрузки: ${draftFields.deliveryTime}`, 20, y);
        y += 6;
    }
    if (cargo.distance) {
        doc.text(`Расстояние: ${cargo.distance} км`, 20, y);
        y += 6;
    }
    if (draftFields && draftFields.vehiclePlate) {
        doc.text(`Номер ТС: ${draftFields.vehiclePlate}`, 20, y);
        y += 6;
    }
    
    y += 10;
    
    // Параметры груза
    doc.setFont(undefined, 'bold');
    doc.text('ПАРАМЕТРЫ ГРУЗА:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    doc.text(`Вес: ${cargo.weight || '—'} т`, 20, y);
    y += 6;
    doc.text(`Объём: ${cargo.volume || '—'} м³`, 20, y);
    y += 6;
    doc.text(`Тип ТС: ${cargo.truck_type || '—'}`, 20, y);
    if (cargo.loading_date) {
        y += 6;
        doc.text(`Дата погрузки: ${formatDocumentDate(cargo.loading_date)}`, 20, y);
    }
    
    y += 10;
    
    // Подписи
    doc.setFont(undefined, 'bold');
    doc.text('ГРУЗООТПРАВИТЕЛЬ:', 20, y);
    doc.text('ПЕРЕВОЗЧИК:', 110, y);
    y += 15;
    doc.setFont(undefined, 'normal');
    doc.text('_________________', 20, y);
    doc.text('_________________', 110, y);
    y += 5;
    doc.text('(подпись)', 20, y);
    doc.text('(подпись)', 110, y);
    
    // Печать (заглушка)
    doc.setDrawColor(200, 200, 200);
    doc.setFillColor(240, 240, 240);
    doc.circle(30, y - 5, 8, 'FD');
    doc.setFontSize(8);
    doc.text('Печать', 25, y - 2, { align: 'center' });
    
    const fileName = `ТТН_${docNumber}.pdf`;
    doc.save(fileName);
    
    return fileName;
}

/**
 * Генерация УПД (Универсальный передаточный документ)
 * @param {Object} deal - Сделка
 * @param {Object} cargo - Груз
 * @param {Object} companyProfile - Профиль компании (заказчик)
 * @param {Object} draftFields - Поля черновика (время, адреса, условия)
 * @returns {string} - Имя файла
 */
function generateUPDPDF(deal, cargo, companyProfile = null, draftFields = null) {
    if (typeof window.jspdf === 'undefined') {
        alert('❌ Библиотека jsPDF не загружена');
        return null;
    }
    if (!cargo) {
        alert('Груз не найден');
        return null;
    }

    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    const yHeaderBase = 35;
    const yTitle = yHeaderBase - 15;
    const yBodyStart = yHeaderBase + 15;
    const pageW = doc.internal.pageSize.getWidth();

    const docNumber = generateDocumentNumber('УПД');
    const docDate = formatDocumentDate(deal.createdAt);

    // Заголовок
    doc.setFontSize(16);
    doc.setFont(undefined, 'bold');
    doc.text('УНИВЕРСАЛЬНЫЙ ПЕРЕДАТОЧНЫЙ ДОКУМЕНТ', pageW / 2, yTitle, { align: 'center' });

    // Номер, дата и источник данных
    doc.setFontSize(12);
    doc.setFont(undefined, 'normal');
    doc.text(`№ ${docNumber}`, 20, yHeaderBase);
    doc.text(`от ${docDate} г.`, pageW / 2, yHeaderBase, { align: 'center' });
    const sourceFmt = window.dealsStore && window.dealsStore.formatCargoSource ? window.dealsStore.formatCargoSource(cargo) : { pdfText: 'Данные: актуальные' };
    doc.setFontSize(8);
    doc.setTextColor(120, 120, 120);
    doc.text(sourceFmt.pdfText, 20, yHeaderBase + 7);
    doc.setTextColor(0, 0, 0);
    doc.setFontSize(11);

    let y = yBodyStart;

    // Продавец
    doc.setFontSize(11);
    doc.setFont(undefined, 'bold');
    doc.text('ПРОДАВЕЦ:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    const sellerName = companyProfile ? companyProfile.name : (cargo.organization_name || 'Не указано');
    const sellerInn = companyProfile ? companyProfile.inn : (cargo.inn || 'Не указано');
    const sellerAddress = companyProfile ? companyProfile.address : '';
    
    doc.text(`Организация: ${sellerName}`, 20, y);
    y += 6;
    doc.text(`ИНН: ${sellerInn}`, 20, y);
    if (companyProfile && companyProfile.ogrn) {
        y += 6;
        doc.text(`ОГРН: ${companyProfile.ogrn}`, 20, y);
    }
    if (sellerAddress) {
        y += 6;
        doc.text(`Адрес: ${sellerAddress}`, 20, y);
    }
    
    y += 5;
    doc.setFont(undefined, 'bold');
    doc.text('ПОКУПАТЕЛЬ (КОНТРАГЕНТ):', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    // Получаем клиента из базы, если выбран
    const counterparty = deal.counterpartyId && window.clientsStore ? 
        window.clientsStore.getClientById(deal.counterpartyId) : null;
    
    if (counterparty) {
        doc.text(`Организация: ${counterparty.name}`, 20, y);
        y += 6;
        if (counterparty.inn) {
            doc.text(`ИНН: ${counterparty.inn}`, 20, y);
            y += 6;
        }
        if (counterparty.kpp) {
            doc.text(`КПП: ${counterparty.kpp}`, 20, y);
            y += 6;
        }
        if (counterparty.ogrn) {
            doc.text(`ОГРН: ${counterparty.ogrn}`, 20, y);
            y += 6;
        }
        if (counterparty.director) {
            doc.text(`Директор: ${counterparty.director}`, 20, y);
            y += 6;
        }
        if (counterparty.legalAddress) {
            doc.text(`Адрес: ${counterparty.legalAddress}`, 20, y, { maxWidth: 170 });
            y += 8;
        }
        if (counterparty.paymentDetails) {
            doc.text(`Реквизиты: ${counterparty.paymentDetails}`, 20, y, { maxWidth: 170 });
            y += 10;
        }
    } else {
        doc.text('Клиент не выбран', 20, y);
        y += 6;
    }
    
    y += 10;
    
    // Покупатель (используем клиента, если выбран)
    doc.setFont(undefined, 'bold');
    doc.text('ПОКУПАТЕЛЬ:', 20, y);
    doc.setFont(undefined, 'normal');
    y += 7;
    
    const counterparty = deal.counterpartyId && window.clientsStore ? 
        window.clientsStore.getClientById(deal.counterpartyId) : null;
    
    if (counterparty) {
        doc.text(`Организация: ${counterparty.name}`, 20, y);
        y += 6;
        if (counterparty.inn) {
            doc.text(`ИНН: ${counterparty.inn}`, 20, y);
            y += 6;
        }
        if (counterparty.phone) {
            doc.text(`Телефон: ${counterparty.phone}`, 20, y);
            y += 6;
        }
    } else {
        // Если клиент не выбран, используем перевозчика из базы или deal.carrier
        const carrier = deal.carrierId && window.carriersStore ? 
            window.carriersStore.getCarrierById(deal.carrierId) : null;
        
        if (carrier) {
            doc.text(`Организация: ${carrier.name}`, 20, y);
            y += 6;
            if (carrier.inn) {
                doc.text(`ИНН: ${carrier.inn}`, 20, y);
                y += 6;
            }
            if (carrier.phone) {
                doc.text(`Телефон: ${carrier.phone}`, 20, y);
                y += 6;
            }
        } else {
            doc.text(`Организация: ${deal.carrier.name}`, 20, y);
            if (deal.carrier.phone) {
                y += 6;
                doc.text(`Телефон: ${deal.carrier.phone}`, 20, y);
            }
        }
    }
    
    y += 10;
    
    // Таблица товаров
    doc.setFont(undefined, 'bold');
    doc.text('ТОВАРЫ (РАБОТЫ, УСЛУГИ):', 20, y);
    y += 7;
    
    // Заголовки таблицы
    doc.setFontSize(9);
    doc.setFont(undefined, 'bold');
    doc.text('№', 20, y);
    doc.text('Наименование', 30, y);
    doc.text('Кол-во', 120, y);
    doc.text('Ед.', 140, y);
    doc.text('Цена', 155, y);
    doc.text('Сумма', 175, y);
    
    y += 6;
    doc.setDrawColor(0, 0, 0);
    doc.line(20, y, 190, y);
    
    y += 5;
    doc.setFont(undefined, 'normal');
    doc.setFontSize(10);
    
    // Строка товара
    const serviceName = `Услуги по перевозке груза: ${cargo.from_city} → ${cargo.to_city}`;
    doc.text('1', 20, y);
    doc.text(serviceName, 30, y, { maxWidth: 85 });
    doc.text('1', 120, y);
    doc.text('усл.', 140, y);
    doc.text(cargo.price.toLocaleString('ru-RU'), 155, y);
    doc.text(cargo.price.toLocaleString('ru-RU'), 175, y);
    
    y += 8;
    doc.line(20, y, 190, y);
    
    y += 10;
    
    // Итого
    doc.setFont(undefined, 'bold');
    doc.text('ИТОГО:', 120, y);
    doc.text(cargo.price.toLocaleString('ru-RU') + ' руб.', 175, y);
    
    y += 15;
    
    // Подписи
    doc.setFont(undefined, 'bold');
    doc.text('ПРОДАВЕЦ:', 20, y);
    doc.text('ПОКУПАТЕЛЬ:', 110, y);
    y += 15;
    doc.setFont(undefined, 'normal');
    doc.text('_________________', 20, y);
    doc.text('_________________', 110, y);
    y += 5;
    doc.text('(подпись)', 20, y);
    doc.text('(подпись)', 110, y);
    y += 10;
    doc.text('М.П.', 20, y);
    doc.text('М.П.', 110, y);
    
    // Печать (заглушка)
    doc.setDrawColor(200, 200, 200);
    doc.setFillColor(240, 240, 240);
    doc.circle(30, y - 5, 8, 'FD');
    doc.setFontSize(8);
    doc.text('Печать', 25, y - 2, { align: 'center' });
    
    const fileName = `УПД_${docNumber}.pdf`;
    doc.save(fileName);
    
    return fileName;
}

// Экспорт
if (typeof window !== 'undefined') {
    window.generatePDF = {
        generateContractPDF,
        generateTTNPDF,
        generateUPDPDF,
        generateDocumentNumber,
        formatDocumentDate
    };
}

