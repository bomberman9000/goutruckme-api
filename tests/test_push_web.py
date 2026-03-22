"""Tests for Web Push endpoints."""
import pytest
from unittest.mock import MagicMock, patch


def test_vapid_key_endpoint_no_config():
    """Without VAPID config, endpoint should 503."""
    from fastapi import HTTPException
    from app.api.routes.push_web import get_vapid_public_key

    with patch("app.api.routes.push_web.settings") as mock_settings:
        mock_settings.VAPID_PUBLIC_KEY = ""
        with pytest.raises(HTTPException) as exc:
            get_vapid_public_key()
        assert exc.value.status_code == 503


def test_vapid_key_endpoint_with_config():
    """With VAPID config, endpoint returns publicKey."""
    from app.api.routes.push_web import get_vapid_public_key

    with patch("app.api.routes.push_web.settings") as mock_settings:
        mock_settings.VAPID_PUBLIC_KEY = "test_public_key_base64"
        result = get_vapid_public_key()
        assert result == {"publicKey": "test_public_key_base64"}


def test_subscribe_creates_entry():
    """POST /push/subscribe should create or update subscription."""
    from app.api.routes.push_web import subscribe, SubscribeBody
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    body = SubscribeBody(
        endpoint="https://example.com/push/abc",
        p256dh="key123",
        auth="auth456",
    )
    result = subscribe(body, authorization=None, db=mock_db)
    assert result["ok"] is True
    assert result["action"] == "created"
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
