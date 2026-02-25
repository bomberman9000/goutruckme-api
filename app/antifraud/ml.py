from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import AntifraudModel, CounterpartyRiskHistory, EnforcementDecision, FraudSignal


FEATURE_NAMES = [
    "rate_per_km",
    "distance_km",
    "prepay_percent",
    "payment_cash",
    "payment_bank",
    "payment_card",
    "complaints_count",
    "trust_score",
    "suspicious_words_count",
    "missing_dimensions",
    "invalid_dates",
    "network_component_risk",
    "repeat_pattern_flags",
    "blacklist_match",
]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _sigmoid(value: float) -> float:
    clipped = max(min(value, 40.0), -40.0)
    return 1.0 / (1.0 + math.exp(-clipped))


def _default_weights() -> dict[str, Any]:
    return {
        "bias": -2.2,
        "rate_per_km": -0.006,
        "distance_km": 0.0004,
        "prepay_percent": 0.018,
        "payment_cash": 0.8,
        "payment_bank": -0.15,
        "payment_card": 0.2,
        "complaints_count": 0.35,
        "trust_score": -0.02,
        "suspicious_words_count": 0.25,
        "missing_dimensions": 0.35,
        "invalid_dates": 0.4,
        "network_component_risk": 0.03,
        "repeat_pattern_flags": 0.9,
        "blacklist_match": 1.4,
    }


def _normalize_feature_vector(features: dict[str, Any]) -> dict[str, float]:
    vector = {name: 0.0 for name in FEATURE_NAMES}
    for name in FEATURE_NAMES:
        vector[name] = _to_float(features.get(name), 0.0)
    return vector


def build_features(
    deal: dict[str, Any],
    rules_result: dict[str, Any],
    network_summary: dict[str, Any],
) -> dict[str, float]:
    payload = deal if isinstance(deal, dict) else {}
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    price = payload.get("price") if isinstance(payload.get("price"), dict) else {}
    payment = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}
    counterparty = payload.get("counterparty") if isinstance(payload.get("counterparty"), dict) else {}

    flags = rules_result.get("flags") if isinstance(rules_result.get("flags"), dict) else {}
    reason_codes = [str(code) for code in (rules_result.get("reason_codes") or [])]

    payment_type = str(payment.get("type") or "unknown").strip().lower()

    suspicious_words = flags.get("suspicious_words")
    if isinstance(suspicious_words, list):
        suspicious_words_count = len(suspicious_words)
    elif suspicious_words:
        suspicious_words_count = 1
    else:
        suspicious_words_count = 0

    vector = {
        "rate_per_km": _to_float(price.get("rate_per_km"), 0.0),
        "distance_km": _to_float(route.get("distance_km"), 0.0),
        "prepay_percent": _to_float(payment.get("prepay_percent"), 0.0),
        "payment_cash": 1.0 if payment_type == "cash" else 0.0,
        "payment_bank": 1.0 if payment_type == "bank" else 0.0,
        "payment_card": 1.0 if payment_type == "card" else 0.0,
        "complaints_count": _to_float(counterparty.get("complaints_count"), 0.0),
        "trust_score": _to_float(counterparty.get("trust_score"), 0.0),
        "suspicious_words_count": float(suspicious_words_count),
        "missing_dimensions": 1.0 if bool(flags.get("missing_dimensions")) else 0.0,
        "invalid_dates": 1.0 if bool(flags.get("invalid_dates")) else 0.0,
        "network_component_risk": _to_float(network_summary.get("component_risk"), 0.0),
        "repeat_pattern_flags": 1.0
        if ("repeat_high_risk_pattern" in reason_codes or "chronic_risk_profile" in reason_codes)
        else 0.0,
        "blacklist_match": 1.0 if bool(flags.get("blacklist_match")) else 0.0,
    }

    return _normalize_feature_vector(vector)


def _predict_with_weights(features: dict[str, float], weights: dict[str, Any]) -> float:
    score = _to_float(weights.get("bias"), 0.0)
    for name in FEATURE_NAMES:
        score += features.get(name, 0.0) * _to_float(weights.get(name), 0.0)
    return _sigmoid(score)


def _get_active_model(db: Session) -> AntifraudModel | None:
    return (
        db.query(AntifraudModel)
        .filter(AntifraudModel.is_active.is_(True))
        .order_by(AntifraudModel.version.desc(), AntifraudModel.id.desc())
        .first()
    )


async def predict_fraud_probability(db: Session, features: dict[str, Any]) -> dict[str, Any]:
    model_row = _get_active_model(db)
    if model_row and isinstance(model_row.weights, dict):
        weights = dict(model_row.weights)
        version = int(model_row.version or 1)
    else:
        weights = _default_weights()
        version = 0

    feature_vector = _normalize_feature_vector(features)
    probability = _predict_with_weights(feature_vector, weights)
    return {
        "probability": max(0.0, min(float(probability), 1.0)),
        "model_version": version,
    }


def _history_row_to_features(row: CounterpartyRiskHistory) -> dict[str, float]:
    reason_codes = [str(code) for code in (row.reason_codes or [])]
    score_total = _to_float(row.score_total, 0.0)

    vector = {
        "rate_per_km": 0.0,
        "distance_km": 0.0,
        "prepay_percent": 100.0 if "high_prepay" in reason_codes else 0.0,
        "payment_cash": 1.0 if "cash_payment" in reason_codes else 0.0,
        "payment_bank": 0.0,
        "payment_card": 0.0,
        "complaints_count": 1.0 if "has_complaints" in reason_codes else 0.0,
        "trust_score": 20.0 if "low_trust_score" in reason_codes else 70.0,
        "suspicious_words_count": 1.0 if "suspicious_words" in reason_codes else 0.0,
        "missing_dimensions": 1.0 if "missing_dimensions" in reason_codes else 0.0,
        "invalid_dates": 1.0 if "invalid_dates" in reason_codes else 0.0,
        "network_component_risk": min(score_total * 10.0, 100.0),
        "repeat_pattern_flags": 1.0 if "repeat_high_risk_pattern" in reason_codes else 0.0,
        "blacklist_match": 1.0 if "blacklist_match" in reason_codes else 0.0,
    }
    return _normalize_feature_vector(vector)


def _train_logistic_regression(
    samples: list[tuple[dict[str, float], int]],
    *,
    iterations: int = 250,
    learning_rate: float = 0.05,
) -> dict[str, Any]:
    weights = _default_weights()

    for _ in range(int(iterations)):
        grad_bias = 0.0
        grad: dict[str, float] = {name: 0.0 for name in FEATURE_NAMES}

        for vector, label in samples:
            pred = _predict_with_weights(vector, weights)
            error = pred - float(label)
            grad_bias += error
            for name in FEATURE_NAMES:
                grad[name] += error * vector.get(name, 0.0)

        n = float(len(samples)) if samples else 1.0
        weights["bias"] = _to_float(weights.get("bias"), 0.0) - learning_rate * (grad_bias / n)
        for name in FEATURE_NAMES:
            weights[name] = _to_float(weights.get(name), 0.0) - learning_rate * (grad[name] / n)

    correct = 0
    for vector, label in samples:
        pred = _predict_with_weights(vector, weights)
        y_hat = 1 if pred >= 0.5 else 0
        if y_hat == int(label):
            correct += 1

    accuracy = (float(correct) / float(len(samples))) if samples else 0.0
    return {
        "weights": weights,
        "metrics": {
            "accuracy": round(accuracy, 4),
            "samples": len(samples),
        },
    }


async def train_model(db: Session) -> dict[str, Any]:
    confirmed_rows = db.query(FraudSignal.deal_id).filter(FraudSignal.signal_type == "fraud_confirmed").all()
    confirmed_ids = {int(item[0]) for item in confirmed_rows if item and item[0] is not None}

    allow_rows = (
        db.query(EnforcementDecision.scope_id)
        .filter(EnforcementDecision.scope == "deal", EnforcementDecision.decision == "allow")
        .all()
    )
    allow_ids: set[int] = set()
    for item in allow_rows:
        try:
            allow_ids.add(int(item[0]))
        except Exception:
            continue

    history_rows = db.query(CounterpartyRiskHistory).all()

    samples: list[tuple[dict[str, float], int]] = []
    for row in history_rows:
        deal_id = _to_int(row.deal_id, 0)
        if deal_id <= 0:
            continue

        if deal_id in confirmed_ids:
            label = 1
        elif deal_id in allow_ids:
            label = 0
        else:
            continue

        samples.append((_history_row_to_features(row), label))

    positives = sum(1 for _, label in samples if int(label) == 1)
    negatives = sum(1 for _, label in samples if int(label) == 0)

    if len(samples) < 10 or positives == 0 or negatives == 0:
        weights = _default_weights()
        metrics = {
            "accuracy": None,
            "samples": len(samples),
            "positive": positives,
            "negative": negatives,
            "status": "fallback_default_weights",
        }
    else:
        trained = _train_logistic_regression(samples)
        weights = trained["weights"]
        metrics = {
            **trained["metrics"],
            "positive": positives,
            "negative": negatives,
            "status": "trained",
        }

    current_version = db.query(AntifraudModel.version).order_by(AntifraudModel.version.desc()).first()
    next_version = int(current_version[0]) + 1 if current_version else 1

    db.query(AntifraudModel).update({AntifraudModel.is_active: False})
    row = AntifraudModel(
        model_type="logreg",
        version=next_version,
        weights=weights,
        metrics=metrics,
        trained_at=datetime.utcnow(),
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "model_version": int(row.version or 0),
        "metrics": row.metrics or {},
    }
