"""
Telegram WebApp initData validation via HMAC-SHA256.

See: core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, unquote

from src.core.config import settings


def _compute_secret_key(bot_token: str) -> bytes:
    """HMAC-SHA256 of bot token with 'WebAppData' as key."""
    return hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()


def validate_init_data(init_data: str, max_age: int = 86400) -> dict | None:
    """Validate Telegram WebApp initData string.

    Returns parsed user dict on success, None on failure.
    ``max_age`` is the maximum allowed age in seconds (default 24h).
    """
    if not init_data:
        return None

    try:
        parsed = parse_qs(init_data, keep_blank_values=True)

        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            return None

        # Build the check string: sorted key=value pairs excluding hash
        pairs = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            pairs.append(f"{key}={values[0]}")
        pairs.sort()
        data_check_string = "\n".join(pairs)

        # Compute HMAC-SHA256
        secret = _compute_secret_key(settings.bot_token)
        computed = hmac.new(
            secret,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(computed, received_hash):
            return None

        # Validate auth_date freshness
        auth_date_str = parsed.get("auth_date", [None])[0]
        if auth_date_str:
            auth_date = int(auth_date_str)
            if time.time() - auth_date > max_age:
                return None

        # Parse user JSON
        user_raw = parsed.get("user", [None])[0]
        if user_raw:
            return json.loads(unquote(user_raw))

        return None
    except Exception:
        return None
