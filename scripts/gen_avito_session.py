"""
Capture an Avito session with Playwright and save it into avito_state.json.

Run:
    uv run python scripts/gen_avito_session.py
"""
import json
import os

from playwright.sync_api import sync_playwright

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "avito_state.json")

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US'] });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'permissions', {
    get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })
});
"""


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )

        page = context.new_page()
        page.add_init_script(STEALTH_JS)
        print("\nOpening avito.ru...")
        page.goto("https://www.avito.ru", timeout=30_000)
        print("\nLogin manually in the opened browser, then press Enter here.\n")
        input(">>> Press Enter after successful login: ")

        state = context.storage_state()
        with open(STATE_FILE, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)

        cookie_count = len(state.get("cookies", []))
        print(f"\nSaved session: {os.path.abspath(STATE_FILE)}")
        print(f"Cookies: {cookie_count}")
        if cookie_count < 3:
            print("Warning: too few cookies, login may have failed.")

        browser.close()


if __name__ == "__main__":
    main()
