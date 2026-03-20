"""
Генерация новой Telethon StringSession для парсера.

Запуск:
    uv run python scripts/gen_session.py

После входа скопируй строку сессии и обнови PARSER_TG_STRING_SESSION
в docker-compose или .env, затем перезапусти контейнер:

    docker compose up -d --force-recreate parser-bot
"""
import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

# Берём из env контейнера
API_ID = int(os.environ.get("PARSER_TG_API_ID", "37467076"))
API_HASH = os.environ.get("PARSER_TG_API_HASH", "")
PHONE = "+79278996662"


async def main() -> None:
    if not API_HASH:
        print("Укажи PARSER_TG_API_HASH в env или впиши в скрипт")
        sys.exit(1)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(phone=PHONE)

    session_str = client.session.save()
    print("\n" + "=" * 60)
    print("PARSER_TG_STRING_SESSION=", session_str)
    print("=" * 60)
    print("\nСкопируй строку выше и обнови .env / docker-compose.yml")
    print("Затем: docker compose up -d --force-recreate parser-bot")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
