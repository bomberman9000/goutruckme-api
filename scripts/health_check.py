#!/usr/bin/env python3
"""
🔍 GouTruckMe - Health Check Script
Проверяет работоспособность всех компонентов системы
"""
import sys
import os
import httpx
from pathlib import Path
from dotenv import load_dotenv

# КРИТИЧНО: Загрузить .env файл ДО импорта переменных
load_dotenv()

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

def check_database():
    """Проверка подключения к базе данных."""
    try:
        from app.db.database import SessionLocal, engine
        from sqlalchemy import text
        
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        print("✅ База данных: подключение успешно")
        return True
    except Exception as e:
        print(f"❌ База данных: ошибка - {e}")
        return False

def check_imports():
    """Проверка импортов основных модулей."""
    modules = [
        ("app.api.main", "FastAPI приложение"),
        ("app.core.security", "Безопасность"),
        ("app.core.config", "Конфигурация"),
        ("app.db.database", "База данных"),
    ]
    
    all_ok = True
    for module_name, description in modules:
        try:
            __import__(module_name)
            print(f"✅ {description}: импорт успешен")
        except Exception as e:
            print(f"❌ {description}: ошибка импорта - {e}")
            all_ok = False
    
    return all_ok

def check_env_vars():
    """Проверка переменных окружения."""
    required_vars = ["SECRET_KEY", "DATABASE_URL"]
    optional_vars = ["OPENAI_API_KEY", "YANDEX_GPT_KEY", "GIGACHAT_KEY"]
    
    print("\n📋 Переменные окружения:")
    all_ok = True
    
    for var in required_vars:
        value = os.getenv(var)
        if value:
            # Маскируем SECRET_KEY для безопасности
            if var == "SECRET_KEY":
                masked = value[:8] + "..." if len(value) > 8 else "***"
                print(f"  ✅ {var}: установлена ({masked})")
            else:
                print(f"  ✅ {var}: {value}")
        else:
            print(f"  ❌ {var}: НЕ УСТАНОВЛЕНА (критично!)")
            all_ok = False
    
    for var in optional_vars:
        value = os.getenv(var)
        if value:
            print(f"  ✅ {var}: установлена")
        else:
            print(f"  ⚪ {var}: не установлена (опционально)")
    
    return all_ok

def check_api_server(port=8080):
    """Проверка доступности API сервера."""
    # Пробуем несколько эндпоинтов
    endpoints = [
        ("Health check", f"http://localhost:{port}/health"),
        ("API info", f"http://localhost:{port}/api"),
        ("Root", f"http://localhost:{port}/"),
    ]
    
    for name, url in endpoints:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(url)
                if response.status_code == 200:
                    if name == "API info":
                        data = response.json()
                        print(f"✅ {name}: работает ({url})")
                        print(f"   Проект: {data.get('project', 'N/A')}")
                        print(f"   Версия: {data.get('version', 'N/A')}")
                    else:
                        print(f"✅ {name}: работает ({url})")
                    return True
                else:
                    print(f"⚠️  {name}: код {response.status_code}")
        except httpx.ConnectError:
            print(f"⚠️  {name}: не удалось подключиться к {url}")
        except Exception as e:
            print(f"⚠️  {name}: {e}")
    
    print(f"\n❌ API сервер не отвечает ни на один эндпоинт")
    print(f"💡 Запустите сервер: uvicorn app.api.main:app --host 0.0.0.0 --port {port} --reload")
    return False

def main():
    """Основная функция проверки."""
    print("=" * 60)
    print("🔍 GouTruckMe - Health Check")
    print("=" * 60)
    
    results = []
    
    print("\n1️⃣ Проверка импортов:")
    results.append(("Импорты", check_imports()))
    
    print("\n2️⃣ Проверка базы данных:")
    results.append(("База данных", check_database()))
    
    print("\n3️⃣ Проверка переменных окружения:")
    results.append(("Переменные окружения", check_env_vars()))
    
    print("\n4️⃣ Проверка API сервера:")
    results.append(("API сервер", check_api_server()))
    
    print("\n" + "=" * 60)
    print("📊 Итоги:")
    print("=" * 60)
    
    for name, status in results:
        status_icon = "✅" if status else "❌"
        print(f"{status_icon} {name}")
    
    all_passed = all(status for _, status in results)
    
    if all_passed:
        print("\n🎉 Все проверки пройдены! Система готова к работе.")
        return 0
    else:
        print("\n⚠️  Некоторые проверки не пройдены. Проверьте ошибки выше.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
