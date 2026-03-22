"""Tests for /api/tenders endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


# ── Unit tests (no DB needed) ─────────────────────────────────────────────────

def test_tender_out_serializer():
    """_tender_out should produce expected dict shape."""
    from app.api.routes.tenders import _tender_out, _bid_out
    from types import SimpleNamespace

    t = SimpleNamespace(
        id=1, title="Тест", description=None,
        from_city="Москва", to_city="Казань",
        loading_date=None, deadline=datetime.utcnow() + timedelta(days=3),
        weight=20.0, volume=None, body_type="тент",
        budget_max=100000, status="active",
        creator_id=42, winner_id=None,
        created_at=datetime.utcnow(), updated_at=None,
        creator=SimpleNamespace(organization_name="ООО Тест"),
        bids=[],
    )
    out = _tender_out(t, uid=42)
    assert out["id"] == 1
    assert out["from_city"] == "Москва"
    assert out["is_mine"] is True
    assert out["bids_count"] == 0
    assert out["min_price"] is None


def test_bid_out_serializer():
    from app.api.routes.tenders import _bid_out
    from types import SimpleNamespace

    bid = SimpleNamespace(
        id=10, tender_id=1, bidder_id=5, price=80000,
        comment="Готов", status="pending",
        created_at=datetime.utcnow(),
        bidder=SimpleNamespace(organization_name="ООО Перевозчик"),
    )
    out = _bid_out(bid)
    assert out["price"] == 80000
    assert out["status"] == "pending"
    assert out["bidder"] == "ООО Перевозчик"


def test_tender_deadline_must_be_future():
    """Creating tender with past deadline should raise 422."""
    from fastapi import HTTPException
    from app.api.routes.tenders import create_tender, TenderCreate
    from types import SimpleNamespace
    import pytest

    past = datetime.utcnow() - timedelta(hours=1)
    body = TenderCreate(
        title="Test", from_city="A", to_city="B",
        deadline=past,
    )
    mock_user = SimpleNamespace(id=1)
    mock_db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        with patch("app.api.routes.tenders._require_user", return_value=mock_user):
            create_tender(body, authorization="Bearer tok", db=mock_db)
    assert exc.value.status_code == 422


def test_cannot_bid_own_tender():
    """Owner should not be able to bid on their own tender."""
    from fastapi import HTTPException
    from app.api.routes.tenders import submit_bid, BidCreate
    from types import SimpleNamespace
    import pytest

    mock_tender = SimpleNamespace(id=1, status="active", creator_id=42,
                                   deadline=datetime.utcnow() + timedelta(days=1))
    mock_user = SimpleNamespace(id=42)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = mock_tender

    body = BidCreate(price=50000)
    with pytest.raises(HTTPException) as exc:
        with patch("app.api.routes.tenders._require_user", return_value=mock_user):
            submit_bid(1, body, authorization="Bearer tok", db=mock_db)
    assert exc.value.status_code == 400
    assert "свой" in exc.value.detail
