// Мок-данные для грузов
import { CITIES, TRUCK_TYPES } from './constants.js';

export function generateMockLoads(count = 50) {
    const loads = [];
    const now = new Date();
    
    for (let i = 1; i <= count; i++) {
        const fromCity = CITIES[Math.floor(Math.random() * CITIES.length)];
        let toCity = CITIES[Math.floor(Math.random() * CITIES.length)];
        while (toCity === fromCity) {
            toCity = CITIES[Math.floor(Math.random() * CITIES.length)];
        }
        
        const weight = Math.round((Math.random() * 20 + 1) * 10) / 10;
        const volume = Math.round((Math.random() * 30 + 5) * 10) / 10;
        const distance = Math.round(Math.random() * 2000 + 200);
        const basePrice = distance * (Math.random() * 40 + 40); // 40-80 руб/км (было 10-25)
        const price = Math.round(basePrice);
        const pricePerKm = Math.round(price / distance);
        
        const daysAgo = Math.floor(Math.random() * 30);
        const createdDate = new Date(now);
        createdDate.setDate(createdDate.getDate() - daysAgo);
        createdDate.setHours(Math.floor(Math.random() * 24));
        createdDate.setMinutes(Math.floor(Math.random() * 60));
        
        const truckType = TRUCK_TYPES[Math.floor(Math.random() * TRUCK_TYPES.length)];
        
        // Простой расчет риска
        let riskLevel = 'low';
        let riskScore = Math.floor(Math.random() * 30 + 5);
        
        if (price < distance * 8) {
            riskLevel = 'high';
            riskScore = Math.floor(Math.random() * 20 + 60);
        } else if (price < distance * 12) {
            riskLevel = 'medium';
            riskScore = Math.floor(Math.random() * 20 + 30);
        }
        
        if (daysAgo > 7) {
            riskLevel = 'medium';
            riskScore = Math.min(riskScore + 20, 80);
        }
        
        loads.push({
            id: i,
            from_city: fromCity,
            to_city: toCity,
            from_address: Math.random() > 0.5 ? `ул. Примерная, ${Math.floor(Math.random() * 100)}` : null,
            to_address: Math.random() > 0.5 ? `пр. Тестовый, ${Math.floor(Math.random() * 100)}` : null,
            weight: weight,
            volume: volume,
            price: price,
            price_per_km: pricePerKm,
            distance: distance,
            truck_type: truckType,
            cargo_type: ['Стройматериалы', 'Продукты', 'Одежда', 'Электроника', 'Мебель'][Math.floor(Math.random() * 5)],
            loading_date: new Date(createdDate.getTime() + Math.random() * 7 * 24 * 60 * 60 * 1000),
            loading_time: `${String(Math.floor(Math.random() * 12) + 8).padStart(2, '0')}:${String(Math.floor(Math.random() * 60)).padStart(2, '0')}`,
            contact_phone: Math.random() > 0.2 ? `+7${Math.floor(Math.random() * 9000000000 + 9000000000)}` : null,
            contact_telegram: Math.random() > 0.5 ? `@user${i}` : null,
            comment: Math.random() > 0.6 ? `Комментарий к грузу #${i}. Требуется аккуратная погрузка.` : null,
            created_at: createdDate.toISOString(),
            status: ['open', 'covered', 'closed'][Math.floor(Math.random() * 3)],
            risk_level: riskLevel,
            risk_score: riskScore,
            creator_rating: Math.round((Math.random() * 2 + 3) * 10) / 10,
            creator_points: Math.floor(Math.random() * 500 + 100)
        });
    }
    
    return loads;
}


