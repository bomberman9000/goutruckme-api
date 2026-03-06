from __future__ import annotations

import asyncio
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


API_ID = int(os.environ.get("PARSER_TG_API_ID") or "37467076")
API_HASH = os.environ.get("PARSER_TG_API_HASH") or "1d3efbe568cb22c55949f898e091fdd8"


async def main() -> None:
    phone = input("PHONE (+7...): ").strip()
    if not phone:
        raise SystemExit("PHONE is required")

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        print("CODE_SENT_TO_APP")
        print("Check Telegram app/service chat, not SMS.")

        code = input("CODE: ").strip()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            password = input("2FA_PASSWORD: ").strip()
            await client.sign_in(password=password)

        print("STRING_SESSION=" + client.session.save())
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
