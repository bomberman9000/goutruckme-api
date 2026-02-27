from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from src.core.config import settings


@dataclass(slots=True)
class PaymentLinkResult:
    provider: str
    provider_payment_id: str
    payment_url: str


class MockTochkaBankClient:
    provider_name = "mock_tochka"

    def _sign_token(self, escrow_id: int, provider_payment_id: str) -> str:
        raw = f"{escrow_id}:{provider_payment_id}".encode()
        return hmac.new(settings.secret_key.encode(), raw, hashlib.sha256).hexdigest()

    def verify_token(self, escrow_id: int, provider_payment_id: str, token: str) -> bool:
        expected = self._sign_token(escrow_id, provider_payment_id)
        return hmac.compare_digest(expected, token or "")

    async def create_payment_link(self, *, cargo_id: int, escrow_id: int, amount_rub: int) -> PaymentLinkResult:
        provider_payment_id = f"mockpay_{uuid4().hex[:16]}"
        token = self._sign_token(escrow_id, provider_payment_id)
        base = (settings.webapp_url or "http://localhost:8001").rstrip("/")
        query = urlencode(
            {
                "escrow_id": escrow_id,
                "payment_id": provider_payment_id,
                "token": token,
                "amount": amount_rub,
            }
        )
        payment_url = f"{base}/api/v1/escrow/{cargo_id}/pay/mock?{query}"
        return PaymentLinkResult(
            provider=self.provider_name,
            provider_payment_id=provider_payment_id,
            payment_url=payment_url,
        )

    async def build_mock_webhook_payload(
        self,
        *,
        escrow_id: int,
        cargo_id: int,
        provider_payment_id: str,
        amount_rub: int,
    ) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "event": "payment.succeeded",
            "escrow_id": escrow_id,
            "cargo_id": cargo_id,
            "payment_id": provider_payment_id,
            "amount_rub": amount_rub,
            "status": "funded",
        }

    async def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Mock provider simply normalizes a webhook payload into the fields the app needs.
        return {
            "provider": str(payload.get("provider") or self.provider_name),
            "event": str(payload.get("event") or "payment.unknown"),
            "escrow_id": int(payload.get("escrow_id")),
            "cargo_id": int(payload.get("cargo_id")),
            "payment_id": str(payload.get("payment_id") or ""),
            "amount_rub": int(payload.get("amount_rub") or 0),
            "status": str(payload.get("status") or "payment_pending"),
            "raw": json.dumps(payload, ensure_ascii=False),
        }


class TochkaBankClient:
    """Placeholder adapter. Real credentials and safe-deal methods are not wired in this prototype."""

    provider_name = "tochka"

    async def create_payment_link(self, *, cargo_id: int, escrow_id: int, amount_rub: int) -> PaymentLinkResult:
        raise RuntimeError("Real Tochka integration is not configured in this environment")

    async def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("Real Tochka integration is not configured in this environment")


def get_bank_client() -> MockTochkaBankClient | TochkaBankClient:
    if settings.escrow_provider.lower() == "tochka" and settings.tochka_client_id and settings.tochka_client_secret:
        return TochkaBankClient()
    return MockTochkaBankClient()
