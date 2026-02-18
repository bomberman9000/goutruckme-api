#!/usr/bin/env python3
"""
🧪 Скрипт для тестирования ИИ-модулей GouTruckMe

Использование:
    python test_ai.py
"""

import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(__file__))

from app.services.ai_lawyer_llm import ai_lawyer_llm


def run_ai_lawyer_test() -> bool:
    """Тест AI-Юриста."""
    print("=" * 60)
    print("🧪 Тестирование AI-Юриста")
    print("=" * 60)
    
    # Проверка конфигурации
    print(f"\n📋 Конфигурация:")
    print(f"  - LLM Provider: {ai_lawyer_llm.llm_provider}")
    print(f"  - OpenAI Key: {'✅ Установлен' if ai_lawyer_llm.OPENAI_API_KEY else '❌ Не установлен'}")
    print(f"  - Use LLM: {ai_lawyer_llm.AI_USE_LLM}")
    print(f"  - Fallback: {ai_lawyer_llm.AI_FALLBACK_TO_LOCAL}")
    
    # Тестовая заявка
    test_load = {
        "from_city": "Москва",
        "to_city": "Казань",
        "weight": 10.0,
        "volume": 20.0,
        "price": 25000,
        "description": "ТНП, упаковано",
        "shipper_inn": "7707083893",
        "truck_type": "10т"
    }
    
    print(f"\n📦 Тестовая заявка:")
    print(f"  - Маршрут: {test_load['from_city']} → {test_load['to_city']}")
    print(f"  - Вес: {test_load['weight']} т")
    print(f"  - Цена: {test_load['price']} ₽")
    
    # Анализ
    print(f"\n🔍 Выполняю анализ...")
    try:
        result = ai_lawyer_llm.analyze_load(test_load)
        
        print(f"\n✅ Результат анализа:")
        print(f"  - Уровень риска: {result.get('risk_level', 'unknown')}")
        print(f"  - Риск-скор: {result.get('risk_score', 0)}/100")
        print(f"  - LLM использован: {result.get('llm_used', False)}")
        print(f"  - Провайдер: {result.get('llm_provider', 'local')}")
        
        if result.get('issues'):
            print(f"\n⚠️ Проблемы:")
            for issue in result['issues']:
                print(f"  - {issue}")
        
        if result.get('recommendations'):
            print(f"\n💡 Рекомендации:")
            for rec in result['recommendations']:
                print(f"  - {rec}")
        
        if result.get('llm_error'):
            print(f"\n❌ Ошибка LLM: {result['llm_error']}")
            print(f"  (Использован локальный анализ)")
        
        print(f"\n✅ Тест пройден успешно!")
        return True
        
    except Exception as e:
        print(f"\n❌ Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_quick_check_test() -> bool:
    """Тест быстрой проверки."""
    print("\n" + "=" * 60)
    print("🧪 Тестирование быстрой проверки")
    print("=" * 60)
    
    try:
        result = ai_lawyer_llm._mock_analysis({
            "from_city": "Москва",
            "to_city": "Москва",  # Ошибка - одинаковые города
            "price": 1000,  # Подозрительно низкая цена
            "weight": 50  # Превышает лимит
        })
        
        print(f"\n✅ Результат:")
        print(f"  - Риск-скор: {result['risk_score']}/100")
        print(f"  - Уровень: {result['risk_level']}")
        
        if result['issues']:
            print(f"\n⚠️ Найдено проблем: {len(result['issues'])}")
            for issue in result['issues']:
                print(f"  - {issue}")
        
        return True
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        return False


def test_ai_lawyer():
    assert run_ai_lawyer_test()


def test_quick_check():
    assert run_quick_check_test()


if __name__ == "__main__":
    print("\n🚀 Запуск тестов ИИ-модулей GouTruckMe\n")
    
    success = True
    success &= run_ai_lawyer_test()
    success &= run_quick_check_test()
    
    print("\n" + "=" * 60)
    if success:
        print("✅ Все тесты пройдены успешно!")
    else:
        print("❌ Некоторые тесты не прошли")
    print("=" * 60 + "\n")
