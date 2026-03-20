"""
Генерация StringSession через ссылку tg:// — открывает Telegram автоматически.
Запуск: uv run python scripts/gen_session_link.py
"""
import asyncio, base64, os, subprocess, sys, time
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.auth import ExportLoginTokenRequest, AcceptLoginTokenRequest
from telethon.tl import types as tl_types
from telethon.tl.types import auth as tl_auth

API_ID = int(os.environ.get("PARSER_TG_API_ID", "37467076"))
API_HASH = os.environ.get("PARSER_TG_API_HASH", "")


async def main():
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    for attempt in range(60):
        result = await client(ExportLoginTokenRequest(api_id=API_ID, api_hash=API_HASH, except_ids=[]))

        if isinstance(result, tl_auth.LoginTokenMigrateTo):
            await client._switch_dc(result.dc_id)
            await client(AcceptLoginTokenRequest(token=result.token))
            result = await client(ExportLoginTokenRequest(api_id=API_ID, api_hash=API_HASH, except_ids=[]))

        if isinstance(result, tl_auth.LoginToken):
            token_b64 = base64.urlsafe_b64encode(result.token).decode().rstrip("=")
            tg_url = f"tg://login?token={token_b64}"

            if attempt == 0 or attempt % 6 == 0:
                subprocess.run(["open", tg_url])
                try:
                    expires_dt = result.expires
                    if hasattr(expires_dt, 'timestamp'):
                        expires_in = max(0, int(expires_dt.timestamp() - time.time()))
                    else:
                        expires_in = int(expires_dt - time.time())
                except Exception:
                    expires_in = "?"
                print(f"[{attempt}] Открываю Telegram... (ссылка действует {expires_in}с)")
                print(f"      Если не открылся: {tg_url}")
            else:
                print(f"[{attempt}] Жду авторизации...")

            await asyncio.sleep(5)
            if await client.is_user_authorized():
                break
            continue

        if isinstance(result, tl_auth.LoginTokenSuccess) or await client.is_user_authorized():
            break

        await asyncio.sleep(3)

    for _ in range(10):
        if await client.is_user_authorized():
            break
        await asyncio.sleep(2)

    if not await client.is_user_authorized():
        print("❌ Не удалось авторизоваться")
        await client.disconnect()
        sys.exit(1)

    session_str = client.session.save()
    me = await client.get_me()
    print(f"\n✅ Авторизован: {me.first_name} (@{getattr(me, 'username', 'no username')})")
    print("\n" + "="*60)
    print("PARSER_TG_STRING_SESSION=" + session_str)
    print("="*60)
    print("\nСкопируй строку выше и скинь в чат!")

    with open("/tmp/tg_session.txt", "w") as f:
        f.write(session_str)
    print("(также сохранено в /tmp/tg_session.txt)")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
