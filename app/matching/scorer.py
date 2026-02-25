from __future__ import annotations

from typing import Any


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def trust_score_to_stars(score: float) -> int:
    score_i = int(round(_clamp(score, 0.0, 100.0)))
    if score_i <= 20:
        return 1
    if score_i <= 40:
        return 2
    if score_i <= 60:
        return 3
    if score_i <= 80:
        return 4
    return 5


def normalize_trust(score: Any) -> float:
    try:
        value = float(score)
    except (TypeError, ValueError):
        value = 50.0
    return _clamp(value, 0.0, 100.0) / 100.0


def _risk_to_level(base_risk: str) -> int:
    value = str(base_risk or "low").lower()
    if value == "high":
        return 2
    if value == "medium":
        return 1
    return 0


def _level_to_risk(level: int) -> str:
    if level >= 2:
        return "high"
    if level == 1:
        return "medium"
    return "low"


def evaluate_trust(
    carrier_trust_score: Any,
    client_trust_score: Any,
    *,
    base_risk: str = "low",
    carrier_flags_high: int = 0,
    client_flags_high: int = 0,
) -> dict[str, Any]:
    carrier_norm = normalize_trust(carrier_trust_score)
    client_norm = normalize_trust(client_trust_score)
    trust_norm = (carrier_norm + client_norm) / 2.0
    trust_score_avg = int(round(trust_norm * 100.0))

    # Явное влияние trust на ранжирование (-8..+8).
    trust_influence = int(round((trust_norm - 0.5) * 16.0))
    low_trust_penalty_applied = trust_score_avg < 40

    risk_level_num = _risk_to_level(base_risk)
    if trust_norm < 0.40:
        risk_level_num += 1
    if trust_norm < 0.25:
        risk_level_num += 1
    if int(carrier_flags_high or 0) + int(client_flags_high or 0) > 0:
        risk_level_num += 1

    risk_level = _level_to_risk(min(risk_level_num, 2))

    if low_trust_penalty_applied:
        trust_explain = "Снижение ранга из-за низкого доверия контрагентов"
    elif trust_influence >= 4:
        trust_explain = "Повышение ранга за счёт высокой надёжности"
    else:
        trust_explain = ""

    return {
        "carrier_trust_score": int(round(carrier_norm * 100.0)),
        "client_trust_score": int(round(client_norm * 100.0)),
        "trust_norm": round(trust_norm, 4),
        "trust_score_avg": trust_score_avg,
        "trust_stars": trust_score_to_stars(trust_score_avg),
        "trust_influence": trust_influence,
        "trust_bonus": trust_influence,
        "low_trust_penalty_applied": low_trust_penalty_applied,
        "trust_explain": trust_explain,
        "risk_level": risk_level,
    }
