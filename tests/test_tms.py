"""Tests for TMS API routes."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from types import SimpleNamespace


def test_plan_limits_defined():
    from app.api.routes.tms import PLAN_LIMITS
    assert PLAN_LIMITS["free"] == 100
    assert PLAN_LIMITS["pro"] == 1000
    assert PLAN_LIMITS["business"] is None


def test_key_out_shape():
    from app.api.routes.tms import _key_out
    ak = SimpleNamespace(
        id=1, key="abc123", name="Test", plan="free",
        is_active=True, calls_today=5, last_used=None,
        created_at=datetime.utcnow(),
    )
    out = _key_out(ak)
    assert out["limit_day"] == 100
    assert out["calls_today"] == 5
    assert out["key"] == "abc123"


def test_resolve_api_key_missing():
    """No key header → 401."""
    from fastapi import HTTPException
    from app.api.routes.tms import _resolve_api_key
    mock_db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        _resolve_api_key(None, mock_db)
    assert exc.value.status_code == 401


def test_resolve_api_key_invalid():
    """Wrong key → 401."""
    from fastapi import HTTPException
    from app.api.routes.tms import _resolve_api_key
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(HTTPException) as exc:
        _resolve_api_key("bad-key", mock_db)
    assert exc.value.status_code == 401


def test_rate_limit_exceeded():
    """Exceed daily limit → 429."""
    from fastapi import HTTPException
    from app.api.routes.tms import _resolve_api_key
    from datetime import date
    mock_db = MagicMock()
    ak = SimpleNamespace(
        key="good", is_active=True, plan="free",
        calls_today=100, reset_date=datetime.utcnow(),
        last_used=None,
    )
    ak.reset_date = datetime.combine(date.today(), datetime.min.time())
    mock_db.query.return_value.filter.return_value.first.return_value = ak
    with pytest.raises(HTTPException) as exc:
        _resolve_api_key("good", mock_db)
    assert exc.value.status_code == 429
