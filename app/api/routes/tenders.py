"""
Тендерный модуль ГрузПоток.

GET    /api/tenders                        — список тендеров (публичный)
POST   /api/tenders                        — создать тендер (auth)
GET    /api/tenders/{id}                   — детали тендера
POST   /api/tenders/{id}/bids              — подать заявку (auth)
GET    /api/tenders/{id}/bids              — заявки (только создатель)
PATCH  /api/tenders/{id}/bids/{bid_id}     — принять / отклонить заявку
POST   /api/tenders/{id}/close             — закрыть тендер
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from jose import jwt

from app.db.database import get_db
from app.models.models import Tender, TenderBid, User
from app.core.security import SECRET_KEY, ALGORITHM

router = APIRouter()

import logging
logger = logging.getLogger(__name__)


def _send_tg(telegram_id: int, text: str) -> None:
    try:
        import httpx
        from app.core.config import settings
        httpx.post(
            f"{settings.tg_bot_internal_url}/internal/send-message",
            json={"telegram_id": telegram_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        logger.warning("tenders send_tg error: %s", e)




# ── Auth helpers ──────────────────────────────────────────────────────────────

def _user_from_token(authorization: Optional[str], db: Session) -> Optional[User]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("sub") or payload.get("id")
        if not uid:
            return None
        return db.query(User).filter(User.id == int(uid), User.is_active == True).first()
    except Exception:
        return None


def _require_user(authorization: Optional[str], db: Session) -> User:
    user = _user_from_token(authorization, db)
    if not user:
        raise HTTPException(401, "Требуется авторизация")
    return user


# ── Serializers ───────────────────────────────────────────────────────────────

def _bid_out(bid: TenderBid) -> dict:
    return {
        "id":         bid.id,
        "bidder_id":  bid.bidder_id,
        "bidder":     bid.bidder.organization_name if bid.bidder else None,
        "bidder_gtp": f"ГТП-{bid.bidder_id:06d}" if bid.bidder_id else None,
        "price":      bid.price,
        "comment":    bid.comment,
        "status":     bid.status,
        "created_at": bid.created_at.isoformat() if bid.created_at else None,
    }


def _tender_out(t: Tender, include_bids: bool = False, uid: Optional[int] = None) -> dict:
    bids = t.bids or []
    min_price = min((b.price for b in bids), default=None)
    out = {
        "id":           t.id,
        "title":        t.title,
        "description":  t.description,
        "from_city":    t.from_city,
        "to_city":      t.to_city,
        "loading_date": t.loading_date,
        "deadline":     t.deadline.isoformat() if t.deadline else None,
        "weight":       t.weight,
        "volume":       t.volume,
        "body_type":    t.body_type,
        "budget_max":   t.budget_max,
        "status":       t.status,
        "bids_count":   len(bids),
        "min_price":    min_price,
        "creator_id":   t.creator_id,
        "creator":      t.creator.organization_name if t.creator else None,
        "creator_gtp":  f"ГТП-{t.creator_id:06d}" if t.creator_id else None,
        "winner_id":    t.winner_id,
        "created_at":   t.created_at.isoformat() if t.created_at else None,
        "is_mine":      uid == t.creator_id if uid else False,
        "my_bid":       None,
    }
    if uid:
        my = next((b for b in bids if b.bidder_id == uid), None)
        if my:
            out["my_bid"] = _bid_out(my)
    if include_bids:
        out["bids"] = [_bid_out(b) for b in sorted(bids, key=lambda b: b.price)]
    return out


# ── Schemas ───────────────────────────────────────────────────────────────────

class TenderCreate(BaseModel):
    title:        str
    description:  Optional[str] = None
    from_city:    str
    to_city:      str
    loading_date: Optional[str] = None
    deadline:     datetime
    weight:       Optional[float] = None
    volume:       Optional[float] = None
    body_type:    Optional[str] = None
    budget_max:   Optional[int] = None


class BidCreate(BaseModel):
    price:   int
    comment: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/tenders")
def list_tenders(
    status:    str = Query("active"),
    from_city: Optional[str] = Query(None),
    to_city:   Optional[str] = Query(None),
    limit:     int = Query(50, ge=1, le=200),
    offset:    int = Query(0, ge=0),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _user_from_token(authorization, db)
    q = db.query(Tender)
    if status != "all":
        q = q.filter(Tender.status == status)
    if from_city:
        q = q.filter(Tender.from_city.ilike(f"%{from_city}%"))
    if to_city:
        q = q.filter(Tender.to_city.ilike(f"%{to_city}%"))
    total = q.count()
    items = q.order_by(Tender.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total":   total,
        "results": [_tender_out(t, uid=user.id if user else None) for t in items],
    }


@router.post("/tenders", status_code=201)
def create_tender(
    body: TenderCreate,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _require_user(authorization, db)
    if body.deadline <= datetime.utcnow():
        raise HTTPException(422, "Deadline должен быть в будущем")
    t = Tender(
        creator_id=user.id, title=body.title, description=body.description,
        from_city=body.from_city, to_city=body.to_city, loading_date=body.loading_date,
        deadline=body.deadline, weight=body.weight, volume=body.volume,
        body_type=body.body_type, budget_max=body.budget_max,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _tender_out(t, uid=user.id)


@router.get("/tenders/{tender_id}")
def get_tender(
    tender_id: int,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _user_from_token(authorization, db)
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(404, "Тендер не найден")
    is_creator = user and user.id == t.creator_id
    return _tender_out(t, include_bids=is_creator, uid=user.id if user else None)


@router.post("/tenders/{tender_id}/bids", status_code=201)
def submit_bid(
    tender_id: int,
    body: BidCreate,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _require_user(authorization, db)
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(404, "Тендер не найден")
    if t.status != "active":
        raise HTTPException(400, "Тендер уже закрыт")
    if t.deadline < datetime.utcnow():
        raise HTTPException(400, "Срок подачи заявок истёк")
    if t.creator_id == user.id:
        raise HTTPException(400, "Нельзя подавать заявку на свой тендер")
    existing = db.query(TenderBid).filter(
        TenderBid.tender_id == tender_id, TenderBid.bidder_id == user.id
    ).first()
    if existing:
        existing.price = body.price
        existing.comment = body.comment
        db.commit()
        db.refresh(existing)
        return _bid_out(existing)
    bid = TenderBid(tender_id=tender_id, bidder_id=user.id, price=body.price, comment=body.comment)
    db.add(bid)
    db.commit()
    db.refresh(bid)
    # Notify tender creator via Telegram
    try:
        creator = db.query(User).filter(User.id == t.creator_id).first()
        if creator and creator.telegram_id:
            bidder_name = user.organization_name or user.full_name or f"ГТП-{user.id:06d}"
            _send_tg(int(creator.telegram_id),
                f"📬 <b>Новая заявка на тендер!</b>\n"
                f"📋 {t.title}\n"
                f"📍 {t.from_city} → {t.to_city}\n"
                f"💰 {body.price:,} ₽\n"
                f"👤 {bidder_name}"
                + (f"\n💬 {body.comment}" if body.comment else "")
            )
    except Exception as e:
        logger.warning("tenders notify_creator error: %s", e)

    return _bid_out(bid)


@router.get("/tenders/{tender_id}/bids")
def list_bids(
    tender_id: int,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _require_user(authorization, db)
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t or t.creator_id != user.id:
        raise HTTPException(403, "Только создатель тендера видит заявки")
    bids = sorted(t.bids or [], key=lambda b: b.price)
    return {"total": len(bids), "bids": [_bid_out(b) for b in bids]}


@router.patch("/tenders/{tender_id}/bids/{bid_id}")
def update_bid_status(
    tender_id: int,
    bid_id: int,
    status: str = Query(...),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    if status not in ("accepted", "rejected"):
        raise HTTPException(422, "status должен быть accepted или rejected")
    user = _require_user(authorization, db)
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t or t.creator_id != user.id:
        raise HTTPException(403, "Нет доступа")
    bid = db.query(TenderBid).filter(TenderBid.id == bid_id, TenderBid.tender_id == tender_id).first()
    if not bid:
        raise HTTPException(404, "Заявка не найдена")
    bid.status = status
    if status == "accepted":
        t.status = "awarded"
        t.winner_id = bid.bidder_id
        for other in (t.bids or []):
            if other.id != bid_id and other.status == "pending":
                other.status = "rejected"
    db.commit()
    db.refresh(bid)
    # Notify bidder via Telegram
    try:
        bidder = db.query(User).filter(User.id == bid.bidder_id).first()
        if bidder and bidder.telegram_id:
            if status == "accepted":
                msg = (
                    f"✅ <b>Ваша заявка принята!</b>\n"
                    f"📋 {t.title}\n"
                    f"📍 {t.from_city} → {t.to_city}\n"
                    f"💰 {bid.price:,} ₽\n"
                    f"Заказчик выбрал вас. Свяжитесь для уточнения деталей."
                )
            else:
                msg = (
                    f"❌ Ваша заявка отклонена\n"
                    f"📋 {t.title}\n"
                    f"💰 {bid.price:,} ₽"
                )
            _send_tg(int(bidder.telegram_id), msg)
    except Exception as e:
        logger.warning("tenders notify_bidder error: %s", e)

    return _bid_out(bid)


@router.post("/tenders/{tender_id}/close")
def close_tender(
    tender_id: int,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _require_user(authorization, db)
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t or t.creator_id != user.id:
        raise HTTPException(403, "Нет доступа")
    if t.status != "active":
        raise HTTPException(400, "Тендер уже закрыт")
    t.status = "closed"
    db.commit()
    return {"ok": True, "id": tender_id, "status": "closed"}
