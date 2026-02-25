from __future__ import annotations

from dataclasses import dataclass
import logging
import os

import httpx


logger = logging.getLogger(__name__)


@dataclass
class SmsSendResult:
    ok: bool
    provider: str
    message_id: str | None = None


class BaseSmsProvider:
    provider_name: str = "base"

    def send_otp(self, phone: str, otp_code: str) -> SmsSendResult:
        raise NotImplementedError


class StubSmsProvider(BaseSmsProvider):
    provider_name = "stub"

    def send_otp(self, phone: str, otp_code: str) -> SmsSendResult:
        logger.warning("SMS STUB: phone=%s otp=%s", phone, otp_code)
        return SmsSendResult(ok=True, provider=self.provider_name, message_id=None)


class HttpSmsProvider(BaseSmsProvider):
    provider_name = "http"

    def __init__(self, base_url: str, api_token: str, sender: str | None = None):
        self.base_url = base_url.strip()
        self.api_token = api_token.strip()
        self.sender = (sender or "").strip()

    def send_otp(self, phone: str, otp_code: str) -> SmsSendResult:
        if not self.base_url or not self.api_token:
            raise RuntimeError("SMS HTTP provider is not configured")

        payload = {
            "phone": phone,
            "text": f"Код подтверждения ГрузПоток: {otp_code}",
        }
        if self.sender:
            payload["sender"] = self.sender

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        response = httpx.post(self.base_url, json=payload, headers=headers, timeout=10.0)
        response.raise_for_status()

        message_id = None
        try:
            data = response.json()
            if isinstance(data, dict):
                message_id = str(data.get("message_id") or data.get("id") or "")
        except Exception:
            message_id = None

        return SmsSendResult(ok=True, provider=self.provider_name, message_id=message_id or None)


def get_sms_provider() -> BaseSmsProvider:
    provider_name = (os.getenv("SMS_PROVIDER", "stub") or "stub").strip().lower()
    if provider_name == "http":
        return HttpSmsProvider(
            base_url=os.getenv("SMS_HTTP_URL", ""),
            api_token=os.getenv("SMS_HTTP_TOKEN", ""),
            sender=os.getenv("SMS_HTTP_SENDER", ""),
        )
    return StubSmsProvider()


def send_otp(phone: str, otp_code: str) -> SmsSendResult:
    provider = get_sms_provider()
    return provider.send_otp(phone=phone, otp_code=otp_code)
