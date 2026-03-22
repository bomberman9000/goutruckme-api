from datetime import date, datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.models import Bid, Load, User, CargoStatus
from app.core.security import SECRET_KEY, ALGORITHM
from app.trust.service import recalc_company_trust
from app.services.bid_scoring import compute_bid_score
from jose import jwt

router = APIRouter()
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_user_id(token: str) -> Optional[int]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["id"]
    except Exception:
        return None


class BidCreate(BaseModel):
    price: float
    vat_type: str = "no_vat"          # with_vat / no_vat / cash
    vehicle_type: Optional[str] = None
    capacity_tons: Optional[float] = None
    volume_m3: Optional[float] = None
    ready_date: Optional[date] = None
    phone: Optional[str] = None
    comment: Optional[str] = None


class BidStatusUpdate(BaseModel):
    status: str  # viewed / shortlisted / accepted / rejected / cancelled


# ── Valid transitions ────────────────────────────────────────────────────────
# owner can: new→viewed, viewed→shortlisted, shortlisted→accepted/rejected, viewed→accepted/rejected
# carrier can: new/viewed/shortlisted→cancelled

_OWNER_TRANSITIONS: dict[str, set[str]] = {
    "new":         {"viewed", "shortlisted", "accepted", "rejected"},
    "viewed":      {"shortlisted", "accepted", "rejected"},
    "shortlisted": {"accepted", "rejected"},
}

_CARRIER_TRANSITIONS: dict[str, set[str]] = {
    "new":         {"cancelled"},
    "viewed":      {"cancelled"},
    "shortlisted": {"cancelled"},
}


def _bid_to_dict(b: Bid, with_carrier: bool = False) -> dict:
    d: dict = {
        "id": b.id,
        "load_id": b.load_id,
        "carrier_id": b.carrier_id,
        "price": b.price,
        "vat_type": b.vat_type,
        "vehicle_type": b.vehicle_type,
        "capacity_tons": b.capacity_tons,
        "volume_m3": b.volume_m3,
        "ready_date": b.ready_date.isoformat() if b.ready_date else None,
        "phone": b.phone,
        "comment": b.comment,
        "status": b.status,
        "score": getattr(b, "score", 0),
        "fit_label": getattr(b, "fit_label", "incomplete"),
        "fit_warnings": getattr(b, "fit_warnings", []) or [],
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "viewed_at": b.viewed_at.isoformat() if getattr(b, "viewed_at", None) else None,
        "accepted_at": b.accepted_at.isoformat() if getattr(b, "accepted_at", None) else None,
        "rejected_at": b.rejected_at.isoformat() if getattr(b, "rejected_at", None) else None,
        "updated_at": b.updated_at.isoformat() if getattr(b, "updated_at", None) else None,
    }
    if with_carrier and b.carrier:
        d["carrier"] = {
            "id": b.carrier.id,
            "name": getattr(b.carrier, "full_name", None) or getattr(b.carrier, "name", None) or "—",
            "phone": getattr(b.carrier, "phone", None),
            "company": getattr(b.carrier, "company_name", None),
        }
    return d


# ── Respond to load ─────────────────────────────────────────────────────────

@router.post("/{load_id}/respond")
def respond_to_load(
    load_id: int,
    body: BidCreate,
    token: str,
    db: Session = Depends(get_db),
):
    user_id = _get_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Load not found")
    if load.status not in (CargoStatus.active.value, "active"):
        raise HTTPException(status_code=400, detail="Load is not active")
    if load.user_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot respond to own load")

    existing = (
        db.query(Bid)
        .filter(Bid.load_id == load_id, Bid.carrier_id == user_id)
        .first()
    )

    if existing:
        existing.price = body.price
        existing.vat_type = body.vat_type
        existing.vehicle_type = body.vehicle_type
        existing.capacity_tons = body.capacity_tons
        existing.volume_m3 = body.volume_m3
        existing.ready_date = body.ready_date
        existing.phone = body.phone
        existing.comment = body.comment
        existing.status = "new"
        existing.updated_at = datetime.utcnow()
        # Recompute score
        try:
            sc, fl, fw = compute_bid_score(existing, load)
            existing.score = sc
            existing.fit_label = fl
            existing.fit_warnings = fw
        except Exception:
            pass
        db.commit()
        db.refresh(existing)
        bid = existing
        updated = True
    else:
        bid = Bid(
            load_id=load_id,
            carrier_id=user_id,
            price=body.price,
            vat_type=body.vat_type,
            vehicle_type=body.vehicle_type,
            capacity_tons=body.capacity_tons,
            volume_m3=body.volume_m3,
            ready_date=body.ready_date,
            phone=body.phone,
            comment=body.comment,
            status="new",
        )
        db.add(bid)
        db.flush()  # get id
        try:
            sc, fl, fw = compute_bid_score(bid, load)
            bid.score = sc
            bid.fit_label = fl
            bid.fit_warnings = fw
        except Exception:
            pass
        db.commit()
        db.refresh(bid)
        updated = False

        try:
            from app.services.rating_system import rating_system
            rating_system.on_bid_created(db, user_id, bid.id, load_id)
        except Exception:
            pass

    try:
        _notify_load_owner(db, load, bid, updated=updated)
    except Exception as e:
        logger.warning("notify owner error: %s", e)

    return {"msg": "ok", "bid_id": bid.id, "updated": updated, "score": bid.score, "fit_label": bid.fit_label}


# ── PATCH status ─────────────────────────────────────────────────────────────

@router.patch("/responses/{response_id}/status")
def update_bid_status(
    response_id: int,
    body: BidStatusUpdate,
    token: str,
    db: Session = Depends(get_db),
):
    user_id = _get_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    bid = db.query(Bid).filter(Bid.id == response_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Response not found")

    load = db.query(Load).filter(Load.id == bid.load_id).first()
    is_owner = load and load.user_id == user_id
    is_carrier = bid.carrier_id == user_id

    if not is_owner and not is_carrier:
        raise HTTPException(status_code=403, detail="Forbidden")

    current = bid.status or "new"
    new_status = body.status

    # Validate transition
    if is_owner:
        allowed = _OWNER_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition {current} → {new_status} as owner",
            )
    else:  # carrier
        allowed = _CARRIER_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition {current} → {new_status} as carrier",
            )

    now = datetime.utcnow()
    bid.status = new_status
    bid.updated_at = now

    if new_status == "viewed" and not getattr(bid, "viewed_at", None):
        bid.viewed_at = now
    elif new_status == "accepted":
        bid.accepted_at = now
        # Close load when accepted
        if load:
            load.status = CargoStatus.closed.value
            # Reject all other bids
            others = db.query(Bid).filter(
                Bid.load_id == bid.load_id,
                Bid.id != bid.id,
                Bid.status.notin_(["rejected", "cancelled"]),
            ).all()
            for other in others:
                other.status = "rejected"
                other.rejected_at = now
                other.updated_at = now
            # Recalc trust
            try:
                recalc_company_trust(db, load.user_id)
                recalc_company_trust(db, bid.carrier_id)
            except Exception:
                pass
            # Rating
            try:
                from app.services.rating_system import rating_system
                rating_system.on_successful_deal(db, shipper_id=load.user_id, carrier_id=bid.carrier_id, load_id=load.id)
                rating_system.on_load_completed(db, load.user_id, load.id)
                rating_system.on_load_completed(db, bid.carrier_id, load.id)
            except Exception:
                pass
    elif new_status == "rejected":
        bid.rejected_at = now

    db.commit()

    # Notify carrier on status change
    try:
        if new_status in ("accepted", "rejected", "shortlisted") and is_owner:
            _notify_carrier(db, load, bid)
    except Exception as e:
        logger.warning("notify carrier error: %s", e)

    return {"msg": "ok", "bid_id": bid.id, "status": new_status}


# ── Get responses for a load ─────────────────────────────────────────────────

@router.get("/{load_id}/responses")
def get_responses(load_id: int, token: str, db: Session = Depends(get_db)):
    user_id = _get_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Load not found")
    if load.user_id != user_id:
        raise HTTPException(status_code=403, detail="Only load owner can view responses")

    bids = (
        db.query(Bid)
        .filter(Bid.load_id == load_id)
        .order_by(Bid.score.desc(), Bid.created_at.asc())
        .all()
    )
    return [_bid_to_dict(b, with_carrier=True) for b in bids]


@router.get("/{load_id}/my-response")
def my_response(load_id: int, token: str, db: Session = Depends(get_db)):
    user_id = _get_user_id(token)
    if not user_id:
        return {"bid": None}
    bid = (
        db.query(Bid)
        .filter(Bid.load_id == load_id, Bid.carrier_id == user_id)
        .first()
    )
    return {"bid": _bid_to_dict(bid) if bid else None}


# ── Notifications ────────────────────────────────────────────────────────────

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
        logger.warning("send tg error: %s", e)


def _notify_load_owner(db: Session, load: Load, bid: Bid, *, updated: bool = False) -> None:
    owner = db.query(User).filter(User.id == load.user_id).first()
    if not owner or not owner.telegram_id:
        return
    carrier = db.query(User).filter(User.id == bid.carrier_id).first()
    carrier_name = getattr(carrier, "full_name", None) or getattr(carrier, "name", None) or "Перевозчик"

    vat_labels = {"with_vat": "с НДС", "no_vat": "без НДС", "cash": "нал"}
    vat = vat_labels.get(bid.vat_type or "", bid.vat_type or "")

    label_icons = {"best_match": "🏆", "good_fit": "✅", "risky": "⚠️", "incomplete": "❓"}
    fit_label = getattr(bid, "fit_label", "incomplete") or "incomplete"
    score = getattr(bid, "score", 0) or 0
    icon = label_icons.get(fit_label, "")

    header = "✏️ Обновлён отклик" if updated else "📬 Новый отклик"
    text = (
        f"{header} на груз <b>#{load.id}</b>\n"
        f"📍 {load.from_city} → {load.to_city}\n\n"
        f"💰 {bid.price:,.0f} ₽ {vat}\n"
        f"{icon} {score}/100 — {fit_label.replace('_', ' ')}\n"
        f"👤 {carrier_name}\n"
        + (f"📞 {bid.phone}\n" if bid.phone else "")
        + (f"🚛 {bid.vehicle_type}, {bid.capacity_tons}т\n" if bid.vehicle_type else "")
        + (f"📅 Готов: {bid.ready_date.strftime('%d.%m.%Y')}\n" if bid.ready_date else "")
        + (f"💬 {bid.comment}\n" if bid.comment else "")
    )
    _send_tg(owner.telegram_id, text)


def _notify_carrier(db: Session, load: Optional[Load], bid: Bid) -> None:
    carrier = db.query(User).filter(User.id == bid.carrier_id).first()
    if not carrier or not carrier.telegram_id:
        return

    icons = {"accepted": "✅", "rejected": "❌", "shortlisted": "📌"}
    labels = {
        "accepted": "Ваш отклик <b>принят</b>! Заказчик выбрал вас.",
        "rejected": "Ваш отклик отклонён.",
        "shortlisted": "Ваш отклик добавлен в шортлист.",
    }
    icon = icons.get(bid.status, "")
    msg = labels.get(bid.status, "")
    if not msg:
        return

    route = f"{load.from_city} → {load.to_city}" if load else f"груз #{bid.load_id}"
    text = (
        f"{icon} {msg}\n"
        f"📍 {route}\n"
        f"💰 {bid.price:,.0f} ₽"
    )
    _send_tg(carrier.telegram_id, text)


# ── Legacy endpoints (backward compat) ──────────────────────────────────────

@router.post("/{load_id}/add")
def add_bid(load_id: int, token: str, price: float, comment: str = "", db: Session = Depends(get_db)):
    return respond_to_load(load_id, BidCreate(price=price, comment=comment), token, db)


@router.get("/{load_id}")
def get_bids(load_id: int, db: Session = Depends(get_db)):
    bids = db.query(Bid).filter(Bid.load_id == load_id).all()
    return [_bid_to_dict(b) for b in bids]


@router.post("/{bid_id}/accept")
def accept_bid(bid_id: int, token: str = "", db: Session = Depends(get_db)):
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        return {"error": "Bid not found"}
    user_id = _get_user_id(token) if token else None
    body = BidStatusUpdate(status="accepted")
    if user_id:
        return update_bid_status(bid_id, body, token, db)
    # legacy path (no auth)
    bid.status = "accepted"
    load = db.query(Load).filter(Load.id == bid.load_id).first()
    if load:
        load.status = CargoStatus.closed.value
    db.commit()
    try:
        recalc_company_trust(db, int(load.user_id))
        recalc_company_trust(db, int(bid.carrier_id))
    except Exception:
        pass
    return {"msg": "bid accepted", "load_id": load.id if load else None, "carrier_id": bid.carrier_id}
