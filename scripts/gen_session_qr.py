"""
Генерация StringSession через QR-код (без SMS-кода).

Запуск:
    PARSER_TG_API_HASH=<hash> uv run python scripts/gen_session_qr.py

1. В терминале появится QR-код
2. Открой Telegram → Настройки → Устройства → Привязать устройство
3. Отсканируй QR-код
4. Скрипт выдаст PARSER_TG_STRING_SESSION=...
"""
import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.auth import ExportLoginTokenRequest, AcceptLoginTokenRequest
from telethon.tl.types import auth as tl_auth
import base64

API_ID = int(os.environ.get("PARSER_TG_API_ID", "37467076"))
API_HASH = os.environ.get("PARSER_TG_API_HASH", "")


def _qr(data: bytes) -> str:
    """Генерирует ASCII QR-код без внешних библиотек через qrencode."""
    import shutil, subprocess, tempfile
    if shutil.which("qrencode"):
        url = "tg://login?token=" + base64.urlsafe_b64encode(data).decode().rstrip("=")
        result = subprocess.run(
            ["qrencode", "-t", "UTF8", "-o", "-", url],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout
    return None


async def main() -> None:
    if not API_HASH:
        print("Укажи PARSER_TG_API_HASH")
        sys.exit(1)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    print("\nЗапрашиваю QR-код у Telegram...")

    for attempt in range(30):
        try:
            result = await client(ExportLoginTokenRequest(
                api_id=API_ID,
                api_hash=API_HASH,
                except_ids=[],
            ))
        except Exception as e:
            print(f"Ошибка: {e}")
            await asyncio.sleep(3)
            continue

        if isinstance(result, tl_auth.LoginToken):
            url = "tg://login?token=" + base64.urlsafe_b64encode(result.token).decode().rstrip("=")

            qr = _qr(result.token)
            if qr:
                print("\033[2J\033[H")  # clear screen
                print(qr)
            else:
                # Fallback: текстовая ссылка
                print(f"\nСсылка для входа:\n{url}\n")
                print("Или открой Telegram → Настройки → Устройства → Привязать устройство")
                print("и введи эту ссылку вручную / отсканируй телефоном\n")

            expires_in = max(0, int(result.expires - __import__("time").time()))
            print(f"Ожидаю сканирования... (осталось {expires_in}с)")

            await asyncio.sleep(5)
            continue

        if isinstance(result, tl_auth.LoginTokenMigrateTo):
            await client._switch_dc(result.dc_id)
            await client(AcceptLoginTokenRequest(token=result.token))
            continue

        if isinstance(result, tl_auth.LoginTokenSuccess):
            break

        # Проверяем авторизацию
        if await client.is_user_authorized():
            break

        await asyncio.sleep(3)

    if not await client.is_user_authorized():
        # Ждём ещё — иногда авторизация чуть запаздывает
        for _ in range(10):
            if await client.is_user_authorized():
                break
            await asyncio.sleep(2)

    if not await client.is_user_authorized():
        print("\n❌ Авторизация не прошла. Попробуй ещё раз.")
        await client.disconnect()
        sys.exit(1)

    session_str = client.session.save()
    me = await client.get_me()
    print(f"\n✅ Авторизован как: {me.first_name} (@{me.username})")
    print("\n" + "=" * 60)
    print("PARSER_TG_STRING_SESSION=" + session_str)
    print("=" * 60)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
