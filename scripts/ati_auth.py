"""
Захват сессии ATI.SU.

Запуск:
    uv run python scripts/ati_auth.py

Откроется браузер. Залогинься вручную. После успешного входа нажми Enter.
Сессия сохранится в ati_state.json.
"""
import json
import os

from playwright.sync_api import sync_playwright

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "ati_state.json")

STEALTH_JS = """
// Убираем следы автоматизации
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US'] });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'permissions', {
    get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })
});
"""


def main() -> None:
    with sync_playwright() as p:
        browser = p.firefox.launch(
            headless=False,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )

        page = context.new_page()

        # Идём сначала на главную, потом на логин — более естественно
        print("\n⏳ Открываю ATI.SU...")
        page.goto("https://ati.su", timeout=30_000)

        print("\n✅ Браузер открыт на странице входа.")
        print("   Залогинься в АТИ вручную (введи логин/пароль, пройди капчу).")
        print("   После успешного входа — нажми Enter здесь.\n")
        input(">>> Нажми Enter когда залогинился: ")

        # Сохраняем cookies + localStorage
        state = context.storage_state()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        cookie_count = len(state.get("cookies", []))
        print(f"\n✅ Сессия сохранена: {os.path.abspath(STATE_FILE)}")
        print(f"   Cookies: {cookie_count} шт.")

        if cookie_count < 3:
            print("⚠️  Мало cookies — возможно вход не прошёл. Попробуй ещё раз.")

        browser.close()


if __name__ == "__main__":
    main()
