#!/usr/bin/env python3
"""
🔍 Скрипт проверки конфигурации ИИ для GouTruckMe

Проверяет:
- Наличие API ключей
- Доступность LLM провайдеров
- Работоспособность модулей
"""

import os
import sys
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

def check_env_file():
    """Проверка наличия .env файла"""
    env_path = Path(".env")
    if env_path.exists():
        print("✅ Файл .env найден")
        return True
    else:
        print("⚠️  Файл .env не найден (используются значения по умолчанию)")
        return False

def check_api_keys():
    """Проверка API ключей"""
    print("\n📋 Проверка API ключей:")
    
    openai_key = os.getenv("OPENAI_API_KEY", "")
    yandex_key = os.getenv("YANDEX_GPT_KEY", "")
    gigachat_key = os.getenv("GIGACHAT_KEY", "")
    
    providers = []
    
    if openai_key and len(openai_key) > 10:
        print("  ✅ OpenAI API ключ найден")
        providers.append("OpenAI")
    else:
        print("  ❌ OpenAI API ключ не найден")
    
    if yandex_key and len(yandex_key) > 10:
        print("  ✅ Yandex GPT ключ найден")
        providers.append("Yandex GPT")
    else:
        print("  ❌ Yandex GPT ключ не найден")
    
    if gigachat_key and len(gigachat_key) > 10:
        print("  ✅ GigaChat ключ найден")
        providers.append("GigaChat")
    else:
        print("  ❌ GigaChat ключ не найден")
    
    return providers

def check_ai_settings():
    """Проверка настроек ИИ"""
    print("\n⚙️  Настройки ИИ:")
    
    ai_use_llm = os.getenv("AI_USE_LLM", "false").lower() == "true"
    ai_fallback = os.getenv("AI_FALLBACK_TO_LOCAL", "true").lower() == "true"
    ai_timeout = int(os.getenv("AI_TIMEOUT_SECONDS", "30"))
    
    print(f"  AI_USE_LLM: {'✅ Включено' if ai_use_llm else '❌ Выключено (локальный режим)'}")
    print(f"  AI_FALLBACK_TO_LOCAL: {'✅ Включено' if ai_fallback else '❌ Выключено'}")
    print(f"  AI_TIMEOUT_SECONDS: {ai_timeout} сек")
    
    return ai_use_llm

def check_modules():
    """Проверка импорта модулей"""
    print("\n🤖 Проверка ИИ-модулей:")
    
    modules = {
        "AI-Юрист": "app.services.ai_lawyer_llm",
        "AI-Логист": "app.services.ai_logist",
        "AI-Антимошенник": "app.services.ai_antifraud",
        "AI-Документы": "app.services.ai_documents",
        "AI-Чатбот": "app.services.ai_chatbot"
    }
    
    all_ok = True
    for name, module_path in modules.items():
        try:
            __import__(module_path)
            print(f"  ✅ {name} - OK")
        except Exception as e:
            print(f"  ❌ {name} - Ошибка: {e}")
            all_ok = False
    
    return all_ok

def check_ai_lawyer():
    """Проверка AI-Юриста"""
    print("\n⚖️  Проверка AI-Юриста:")
    
    try:
        from app.services.ai_lawyer_llm import ai_lawyer_llm
        
        print(f"  Провайдер: {ai_lawyer_llm.llm_provider}")
        print(f"  LLM доступен: {'✅ Да' if ai_lawyer_llm.llm_provider != 'mock' else '❌ Нет (локальный режим)'}")
        
        # Тестовый анализ
        test_data = {
            "from_city": "Москва",
            "to_city": "Казань",
            "price": 25000,
            "weight": 10
        }
        
        result = ai_lawyer_llm.analyze_load(test_data, use_llm=False)
        print(f"  Тестовый анализ: ✅ Работает")
        print(f"    Риск-скор: {result.get('risk_score', 0)}/100")
        print(f"    Уровень риска: {result.get('risk_level', 'unknown')}")
        
        return True
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        return False

def main():
    """Главная функция"""
    print("=" * 60)
    print("🔍 Проверка конфигурации ИИ для GouTruckMe")
    print("=" * 60)
    
    # Загружаем .env если есть
    from dotenv import load_dotenv
    load_dotenv()
    
    check_env_file()
    providers = check_api_keys()
    ai_enabled = check_ai_settings()
    modules_ok = check_modules()
    lawyer_ok = check_ai_lawyer()
    
    print("\n" + "=" * 60)
    print("📊 ИТОГОВЫЙ СТАТУС:")
    print("=" * 60)
    
    if ai_enabled and providers:
        print(f"✅ ИИ работает с LLM: {', '.join(providers)}")
    elif not ai_enabled:
        print("⚠️  ИИ работает в локальном режиме (без LLM)")
        print("   Для включения LLM установите AI_USE_LLM=true в .env")
    else:
        print("❌ ИИ включен, но API ключи не найдены")
        print("   Добавьте API ключ в .env файл")
    
    if modules_ok and lawyer_ok:
        print("✅ Все модули работают корректно")
    else:
        print("⚠️  Некоторые модули имеют проблемы")
    
    print("\n💡 Рекомендации:")
    if not ai_enabled:
        print("   1. Для начала используйте локальный режим (текущий)")
        print("   2. Для продакшена добавьте API ключ и установите AI_USE_LLM=true")
    elif not providers:
        print("   1. Добавьте API ключ в .env файл")
        print("   2. См. AI_SETUP.md для инструкций")
    else:
        print("   ✅ Всё настроено правильно!")
    
    print("=" * 60)

if __name__ == "__main__":
    main()




