from src.core.config import settings


def cargo_deeplink(cargo_id: int) -> str:
    username = (settings.bot_username or "").lstrip("@").strip()
    payload = f"cargo_{cargo_id}"
    if username:
        return f"https://t.me/{username}?start={payload}"
    return f"/start {payload}"
