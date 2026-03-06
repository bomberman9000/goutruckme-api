import os
from playwright.sync_api import sync_playwright

# Файл, куда сохраним куки и состояние
STATE_FILE = "ati_state.json"

def run():
    print("🕵️‍♂️ Запускаем операцию 'Живые Куки'...")

    with sync_playwright() as p:
        # Запускаем браузер в видимом режиме (headless=False)
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()

        page = context.new_page()

        print("🌍 Переходим на ATI.SU...")
        page.goto("https://ati.su/login")

        print("\n" + "="*60)
        print("⚡️ ВНИМАНИЕ! РУЧНОЕ УПРАВЛЕНИЕ ⚡️")
        print("1. В открывшемся браузере введи логин и пароль.")
        print("2. Если появится капча — пройди её.")
        print("3. Дождись полной загрузки Личного Кабинета.")
        print("4. Вернись сюда и нажми ENTER, чтобы сохранить доступ.")
        print("="*60 + "\n")

        input("👉 Нажми ENTER после успешного входа...")

        # Сохраняем состояние (куки, storage)
        context.storage_state(path=STATE_FILE)
        print(f"✅ Состояние сохранено в файл: {os.path.abspath(STATE_FILE)}")

        browser.close()

if __name__ == "__main__":
    run()
