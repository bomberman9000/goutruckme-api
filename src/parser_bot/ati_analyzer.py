import asyncio
import json
import logging
import random
from playwright.async_api import async_playwright

logger = logging.getLogger("ati-parser")

STATE_FILE = "ati_state.json"

async def get_ati_market_price(from_city: str, to_city: str, weight_t: float = 20.0) -> int | None:
    """
    Партизанский метод: заходит на АТИ под твоей сессией и проверяет цену.
    """
    async with async_playwright() as p:
        # Запускаем браузер (можно headless=True, но для отладки лучше False)
        # slow_mo добавляет задержку между действиями, имитируя человека
        browser = await p.chromium.launch(headless=True, slow_mo=100)

        try:
            # Загружаем сохраненное состояние
            context = await browser.new_context(storage_state=STATE_FILE)
            page = await context.new_page()

            # Идем в калькулятор ("Расчет расстояний")
            await page.goto("https://ati.su/trace")

            # Ждем загрузки формы
            await page.wait_for_selector('input[placeholder="Откуда"]', timeout=10000)

            # --- Вбиваем "Откуда" ---
            await page.fill('input[placeholder="Откуда"]', from_city)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await page.keyboard.press("ArrowDown") # Выбираем первую подсказку
            await page.keyboard.press("Enter")

            # --- Вбиваем "Куда" ---
            await page.fill('input[placeholder="Куда"]', to_city)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")

            # --- Жмем "Рассчитать" ---
            # Селектор кнопки может меняться, ищем по тексту или классу
            submit_btn = page.locator("button", has_text="Рассчитать")
            await submit_btn.click()

            # --- Ждем результатов ---
            # АТИ обычно показывает блок "Средняя ставка" или "Расходы"
            # Здесь нужно будет подстроить селектор под актуальную верстку
            # Например, ищем блок со знаком рубля

            # Ждем появления блока с ценой (примерный селектор, нужно уточнить через Inspector)
            # Обычно это что-то вроде [class*="Summary-price"]
            await page.wait_for_selector('div[class*="Summary-price"]', timeout=15000)

            price_text = await page.locator('div[class*="Summary-price"]').first.inner_text()

            # Чистим текст: "135 000 ₽" -> 135000
            clean_price = "".join(filter(str.isdigit, price_text))

            if clean_price:
                logger.info(f"ATI Price for {from_city}-{to_city}: {clean_price}")
                return int(clean_price)

        except Exception as e:
            logger.error(f"ATI Parser error: {e}")
            # Можно сделать скриншот ошибки
            # await page.screenshot(path="ati_error.png")

        finally:
            await browser.close()

    return None

# Тестовый запуск
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Для теста нужен файл ati_state.json
    try:
        price = asyncio.run(get_ati_market_price("Москва", "Казань"))
        print(f"💰 Результат: {price} ₽")
    except Exception as e:
        print(f"Ошибка (возможно, нет файла авторизации): {e}")
