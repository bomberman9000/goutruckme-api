"""
Обновление cookies для ATI.SU loads.ati.su.

Запуск:
    uv run python scripts/ati_refresh_cookies.py

Скрипт откроет браузер на loads.ati.su.
Залогинься и убедись что видишь грузы на бирже.
После этого нажми Enter — cookies сохранятся в ati_state.json.
"""
import json
import os
import sys

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "ati_state.json")


def check_session(cookies: list) -> dict:
    """Проверить свежесть сессии через profile API."""
    import httpx

    cookie_dict = {c["name"]: c["value"] for c in cookies}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    result = {"profile_ok": False, "loads_ok": False, "authorized": False}

    with httpx.Client(cookies=cookie_dict, headers=headers, timeout=15) as client:
        # Проверяем профиль
        r = client.get("https://d.ati.su/webapi/profile/v1/profile")
        if r.status_code == 200:
            profile = r.json()
            firm = profile.get("firm_name") or profile.get("firmName")
            if firm:
                result["profile_ok"] = True
                result["firm"] = firm
                result["ati_code"] = profile.get("ati_code") or profile.get("atiCode")

        # Проверяем доступ к бирже
        r2 = client.post(
            "https://loads.ati.su/webapi/v1.0/loads/search",
            json={"filter": {}, "paging": {"skip": 0, "take": 3}},
            headers={
                **headers,
                "Content-Type": "application/json",
                "Origin": "https://loads.ati.su",
                "Referer": "https://loads.ati.su/",
            },
        )
        if r2.status_code == 200:
            data = r2.json()
            result["authorized"] = data.get("isUserAuthorized", False)
            result["total_items"] = data.get("totalItems", 0)
            result["loads_count"] = len(data.get("loads", []))
            result["loads_ok"] = result["loads_count"] > 0

    return result


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ playwright не установлен. Запусти: pip install playwright && playwright install firefox")
        sys.exit(1)

    print("\n🌐 Открываю loads.ati.su в браузере...")
    print("   1. Залогинься в АТИ")
    print("   2. Перейди на страницу грузов (убедись что видишь список грузов)")
    print("   3. Вернись сюда и нажми Enter\n")

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )

        # Пробуем восстановить предыдущую сессию если есть
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, list) and existing:
                    for c in existing:
                        try:
                            context.add_cookies([{
                                "name": c["name"],
                                "value": c["value"],
                                "domain": c.get("domain", ".ati.su"),
                                "path": c.get("path", "/"),
                            }])
                        except Exception:
                            pass
                    print("   ℹ️  Загружены предыдущие cookies")
            except Exception:
                pass

        page = context.new_page()
        page.goto("https://loads.ati.su", timeout=30_000)

        input(">>> Нажми Enter когда залогинился и видишь грузы: ")

        # Сохраняем все cookies со всех доменов ати
        all_cookies = context.cookies(urls=[
            "https://ati.su",
            "https://loads.ati.su",
            "https://d.ati.su",
        ])

        browser.close()

    if not all_cookies:
        print("❌ Cookies не получены")
        sys.exit(1)

    print(f"\n📦 Получено {len(all_cookies)} cookies")
    for c in all_cookies:
        print(f"   {c['domain']}: {c['name']}={'*' * min(8, len(c['value']))}")

    # Проверяем сессию
    print("\n🔍 Проверяю сессию...")
    status = check_session(all_cookies)

    if status.get("profile_ok"):
        print(f"✅ Профиль: {status.get('firm')} (ATI #{status.get('ati_code')})")
    else:
        print("⚠️  Профиль недоступен")

    if status.get("authorized"):
        total = status.get("total_items", 0)
        found = status.get("loads_count", 0)
        if found > 0:
            print(f"✅ Биржа: {total} грузов (получено {found})")
        else:
            print(f"⚠️  Биржа: авторизован но 0 грузов (total={total})")
            print("   Возможно нужна платная подписка на АТИ для просмотра биржи")
    else:
        print("❌ Биржа: не авторизован")

    # Сохраняем в state.json
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_cookies, f, ensure_ascii=False, indent=4)

    print(f"\n✅ Сохранено в {os.path.abspath(STATE_FILE)}")

    if not status.get("loads_ok"):
        print("\n⚠️  ВАЖНО: Биржа вернула 0 грузов.")
        print("   Скорее всего аккаунт без подписки 'Биржа грузов'.")
        print("   Для парсинга чужих грузов нужен платный аккаунт ATI.SU.")
        print("   Альтернатива: используй API калькулятора ставок АТИ.")


if __name__ == "__main__":
    main()
