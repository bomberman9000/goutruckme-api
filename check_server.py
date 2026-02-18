#!/usr/bin/env python3
"""Проверка работы сервера"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    print("1. Проверка импорта...")
    from app.api.main import app
    print("   ✅ Импорт успешен")
    
    print("\n2. Проверка статических файлов...")
    static_dir = os.path.join(os.path.dirname(__file__), "app", "static")
    if os.path.exists(static_dir):
        print(f"   ✅ Директория существует: {static_dir}")
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            print(f"   ✅ index.html существует: {index_path}")
        else:
            print(f"   ❌ index.html НЕ найден: {index_path}")
    else:
        print(f"   ❌ Директория НЕ существует: {static_dir}")
    
    print("\n3. Проверка модулей JS...")
    js_dirs = [
        "app/static/js/clients",
        "app/static/js/carriers",
        "app/static/js/deals",
        "app/static/js/documents",
        "app/static/js/risk",
    ]
    optional_js_dirs = {"app/static/js/admin"}  # админка на Jinja2, JS не обязателен
    for js_dir in js_dirs + list(optional_js_dirs):
        full_path = os.path.join(os.path.dirname(__file__), js_dir)
        if os.path.exists(full_path):
            files = [f for f in os.listdir(full_path) if f.endswith('.js')]
            print(f"   ✅ {js_dir}: {len(files)} файлов")
        else:
            if js_dir in optional_js_dirs:
                print(f"   ℹ️  {js_dir}: не используется (ok)")
            else:
                print(f"   ❌ {js_dir}: НЕ существует")
    
    print("\n4. Проверка роутеров...")
    routers = [
        # core
        'auth', 'loads', 'bids', 'messages',
        # business modules
        'ai', 'lawyer', 'logist', 'antifraud',
        'documents', 'chatbot', 'rating',
        'complaints', 'forum',
        # integrations / bot
        'telegram', 'bot_api', 'complaints_ai',
        # vehicles
        'vehicles',
        # utils / seed
        'test_data',
    ]
    for router_name in routers:
        try:
            module = __import__(f'app.api.routes.{router_name}', fromlist=['router'])
            if hasattr(module, 'router'):
                print(f"   ✅ {router_name}")
            else:
                print(f"   ⚠️  {router_name}: нет router")
        except Exception as e:
            print(f"   ❌ {router_name}: {e}")

    print("\n5. Health-check: ключевые таблицы...")
    try:
        from sqlalchemy import inspect
        from app.db.database import engine
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        required = {"users", "loads", "vehicles", "documents", "complaints", "forum_posts", "audit_events"}
        missing = required - tables
        if missing:
            print(f"   ❌ Нет таблиц: {', '.join(sorted(missing))} → миграции/seed")
        else:
            print(f"   ✅ Все ключевые таблицы: {', '.join(sorted(required))}")
    except Exception as e:
        print(f"   ❌ Таблицы: {e}")

    print("\n6. Health-check: audit_events (колонки + индексы)...")
    try:
        from sqlalchemy import inspect
        from app.db.database import engine
        insp = inspect(engine)
        if "audit_events" not in insp.get_table_names():
            print("   ❌ Таблица audit_events не найдена (alembic upgrade head)")
        else:
            cols = [c["name"] for c in insp.get_columns("audit_events")]
            print(f"   ✅ audit_events: {len(cols)} колонок ({', '.join(cols)})")
            index_names = {idx["name"] for idx in insp.get_indexes("audit_events")}
            required_idx = {
                "ix_audit_events_entity",
                "ix_audit_events_actor_role",
                "ix_audit_events_actor_user_id",
            }
            missing_idx = required_idx - index_names
            if missing_idx:
                print(f"   ❌ Нет индексов: {', '.join(sorted(missing_idx))} → миграции")
            else:
                print(f"   ✅ Индексы: {', '.join(sorted(required_idx))}")
    except Exception as e:
        print(f"   ❌ audit_events: {e}")

    print("\n7. Роуты зарегистрированы в app...")
    def collect_paths(routes, prefix=""):
        out = []
        for r in routes:
            p = getattr(r, "path", None)
            if p is None:
                continue
            full = (prefix + p).replace("//", "/")
            if hasattr(r, "methods"):
                out.append(full)
            if hasattr(r, "routes"):
                out.extend(collect_paths(r.routes, full))
            if hasattr(r, "app") and hasattr(r.app, "routes"):
                out.extend(collect_paths(r.app.routes, full))
        return out

    paths = collect_paths(app.routes)
    bot_paths = sorted(p for p in paths if p.startswith("/api/bot"))
    required_bot = {"/api/bot/link", "/api/bot/loads", "/api/bot/loads/{load_id}", "/api/bot/loads/{load_id}/take"}
    has_bot = required_bot <= set(bot_paths)

    checks = [
        ("/admin", lambda: any(p == "/admin" or p.startswith("/admin/") for p in paths)),
        ("/admin/applications", lambda: any(
                    p == "/admin/applications" or p.startswith("/admin/applications/")
                    for p in paths
                )),
        ("/api/.../complaints/.../ai-analysis", lambda: any("complaints" in p and "ai-analysis" in p for p in paths)),
        ("/api/telegram", lambda: any(p.startswith("/api/telegram") for p in paths)),
        ("/api/bot (link, loads, take)", lambda: has_bot),
    ]
    for label, pred in checks:
        if pred():
            print(f"   ✅ {label}")
        else:
            print(f"   ❌ {label} не найден (include_router?)")
    if bot_paths and not has_bot:
        print(f"   ℹ️  Зарегистрированы: {', '.join(bot_paths)}")

    print("\n✅ Все проверки пройдены! Сервер должен работать.")
    print("\nЗапуск: uvicorn app.api.main:app --host 0.0.0.0 --port 8080 --reload")
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
