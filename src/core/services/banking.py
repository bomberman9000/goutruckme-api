from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode
from uuid import uuid4

from src.core.config import settings


@dataclass(slots=True)
class PaymentLinkResult:
    provider: str
    provider_payment_id: str
    payment_url: str


@dataclass(slots=True)
class BankWebhookResult:
    provider: str
    event: str
    escrow_id: int
    cargo_id: int
    payment_id: str
    amount_rub: int
    status: str
    raw: str


@dataclass(slots=True)
class PayoutResult:
    provider: str
    provider_payout_id: str
    status: str


class BankClient(Protocol):
    provider_name: str
    supports_mock_checkout: bool

    async def create_payment_link(self, *, cargo_id: int, escrow_id: int, amount_rub: int) -> PaymentLinkResult: ...

    async def parse_webhook(self, payload: dict[str, Any]) -> BankWebhookResult: ...

    async def release_funds(
        self,
        *,
        escrow_id: int,
        cargo_id: int,
        amount_rub: int,
        carrier_user_id: int,
    ) -> PayoutResult: ...


def normalize_bank_provider(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"", "mock", "mock_tochka"}:
        return "mock_tochka"
    if raw in {"tochka", "tochka_api"}:
        return "tochka"
    return raw


class MockTochkaBankClient:
    provider_name = "mock_tochka"
    supports_mock_checkout = True

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

    async def parse_webhook(self, payload: dict[str, Any]) -> BankWebhookResult:
        return BankWebhookResult(
            provider=str(payload.get("provider") or self.provider_name),
            event=str(payload.get("event") or "payment.unknown"),
            escrow_id=int(payload.get("escrow_id")),
            cargo_id=int(payload.get("cargo_id")),
            payment_id=str(payload.get("payment_id") or ""),
            amount_rub=int(payload.get("amount_rub") or 0),
            status=str(payload.get("status") or "payment_pending"),
            raw=json.dumps(payload, ensure_ascii=False),
        )

    async def release_funds(
        self,
        *,
        escrow_id: int,
        cargo_id: int,
        amount_rub: int,
        carrier_user_id: int,
    ) -> PayoutResult:
        _ = (escrow_id, cargo_id, amount_rub, carrier_user_id)
        return PayoutResult(
            provider=self.provider_name,
            provider_payout_id=f"mockout_{uuid4().hex[:16]}",
            status="released",
        )


class TochkaBankClient:
    """Provider adapter boundary for future real safe-deal wiring."""

    provider_name = "tochka"
    supports_mock_checkout = False

    async def create_payment_link(self, *, cargo_id: int, escrow_id: int, amount_rub: int) -> PaymentLinkResult:
        _ = (cargo_id, escrow_id, amount_rub)
        raise RuntimeError("Tochka payment link flow is not configured; keep ESCROW_PROVIDER=mock until API credentials and methods are wired")

    async def parse_webhook(self, payload: dict[str, Any]) -> BankWebhookResult:
        _ = payload
        raise RuntimeError("Tochka webhook parsing is not configured in this environment")

    async def release_funds(
        self,
        *,
        escrow_id: int,
        cargo_id: int,
        amount_rub: int,
        carrier_user_id: int,
    ) -> PayoutResult:
        _ = (escrow_id, cargo_id, amount_rub, carrier_user_id)
        raise RuntimeError("Tochka payout flow is not configured; keep release on mock provider until provider methods are wired")


def get_bank_client(provider: str | None = None) -> BankClient:
    provider_name = normalize_bank_provider(provider or settings.escrow_provider)
    if provider_name == "tochka" and settings.tochka_client_id and settings.tochka_client_secret:
        return TochkaBankClient()
    return MockTochkaBankClient()
