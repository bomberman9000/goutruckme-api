"""
Bid scoring service.
Score 0–100, fit_label, fit_warnings.
"""
from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.models import Bid, Load

# ── Weights ──────────────────────────────────────────────────────────────────
W_VEHICLE_MATCH   = 25
W_CAPACITY_MATCH  = 25
W_VOLUME_MATCH    = 15
W_PRICE           = 20
W_SPEED           = 10
W_COMPLETENESS    =  5

_BODY_ALIASES: dict[str, str] = {
    "tent": "тент", "тент": "тент",
    "ref": "реф",  "реф": "реф", "рефрижератор": "реф",
    "bort": "борт", "борт": "борт",
    "tank": "цистерна", "цистерна": "цистерна",
    "platform": "площадка", "площадка": "площадка",
}

def _norm(v: str | None) -> str:
    return _BODY_ALIASES.get((v or "").lower().strip(), (v or "").lower().strip())


def compute_bid_score(bid: "Bid", load: "Load") -> tuple[int, str, list[str]]:
    """
    Returns (score 0-100, fit_label, fit_warnings[]).
    fit_label: best_match | good_fit | risky | incomplete
    """
    score = 0
    warnings: list[str] = []

    # 1. Vehicle type match (25 pts)
    bid_vt  = _norm(bid.vehicle_type)
    load_vt = _norm(getattr(load, "required_body_type", None))
    if not bid_vt:
        score += 0  # no info
    elif not load_vt or bid_vt == load_vt:
        score += W_VEHICLE_MATCH
    else:
        warnings.append("body_type_mismatch")
        score += 5  # partial

    # 2. Capacity vs load weight (25 pts)
    load_weight = getattr(load, "weight", None) or getattr(load, "weight_t", None)
    if bid.capacity_tons and load_weight:
        ratio = bid.capacity_tons / float(load_weight)
        if ratio >= 1.0:
            score += W_CAPACITY_MATCH
        elif ratio >= 0.8:
            score += 10
            warnings.append("may_not_fit_weight")
        else:
            warnings.append("may_not_fit_weight")
    elif bid.capacity_tons:
        score += 15  # has capacity, no load weight to compare
    else:
        score += 0   # incomplete

    # 3. Volume vs load volume (15 pts)
    load_vol = getattr(load, "volume", None) or getattr(load, "volume_m3", None)
    if bid.volume_m3 and load_vol:
        ratio = bid.volume_m3 / float(load_vol)
        if ratio >= 1.0:
            score += W_VOLUME_MATCH
        elif ratio >= 0.8:
            score += 7
            warnings.append("may_not_fit_volume")
        else:
            warnings.append("may_not_fit_volume")
    elif bid.volume_m3:
        score += 10
    # no volume info → 0

    # 4. Price competitiveness (20 pts)
    load_price = float(getattr(load, "price", 0) or 0)
    bid_price  = float(bid.price or 0)
    if load_price > 0 and bid_price > 0:
        ratio = bid_price / load_price
        if ratio <= 1.0:
            score += W_PRICE           # at or below ask
        elif ratio <= 1.1:
            score += 15                # up to +10%
        elif ratio <= 1.25:
            score += 8
        else:
            score += 2
    elif bid_price > 0:
        score += 10

    # 5. Response speed (10 pts)
    load_created = getattr(load, "created_at", None)
    bid_created  = bid.created_at
    if load_created and bid_created:
        delta_h = (bid_created - load_created).total_seconds() / 3600
        if delta_h <= 1:
            score += W_SPEED
        elif delta_h <= 6:
            score += 7
        elif delta_h <= 24:
            score += 4
        else:
            score += 1

    # 6. Profile completeness (5 pts)
    filled = sum(bool(x) for x in [bid.phone, bid.vehicle_type, bid.capacity_tons, bid.volume_m3, bid.ready_date])
    score += round(W_COMPLETENESS * filled / 5)

    score = max(0, min(100, score))

    # fit_label
    if score >= 80 and not warnings:
        fit_label = "best_match"
    elif score >= 60:
        fit_label = "good_fit"
    elif score >= 35:
        fit_label = "risky"
    else:
        fit_label = "incomplete"

    return score, fit_label, warnings
