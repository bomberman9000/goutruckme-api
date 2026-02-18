from __future__ import annotations

"""AI-Юрист: модерация претензий и форума.

Возвращает нормализованный JSON с полями:
- risk_level, risk_score, issues, recommendations, auto_action
- model, version, analyzed_at (ISO UTC), source (auto|manual|admin|view)
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

MODEL_NAME = "gpt-4.1-mini"
VERSION = "ai_jurist_v1"


@dataclass
class AiModerationResult:
    risk_level: str
    risk_score: int
    issues: List[str]
    recommendations: List[str]
    auto_action: str
    needs_moderation: bool | None = None
    can_publish: bool | None = None
    model: str = MODEL_NAME
    version: str = VERSION
    analyzed_at: str = ""
    source: str = "auto"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "issues": self.issues,
            "recommendations": self.recommendations,
            "auto_action": self.auto_action,
            "needs_moderation": self.needs_moderation,
            "can_publish": self.can_publish,
            "model": self.model,
            "version": self.version,
            "analyzed_at": self.analyzed_at,
            "source": self.source,
        }


CRITICAL_WORDS = {"мошеннич", "обман", "украл", "кинул", "пропал"}
INSULT_WORDS = {"дурак", "идиот", "тварь", "мраз", "пидор"}  # расширим позже


def _now_iso_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def analyze_complaint_text(
    text: str, source: str = "auto"
) -> AiModerationResult:
    """Анализ текста претензии.

    auto_action:
      - critical (70+)  → auto_penalty
      - high_risk (50+) → auto_confirm
      - medium (30+)    → send_to_admin
      - низкий риск     → none
    """
    t = (text or "").lower()
    score = 0
    issues: List[str] = []
    recs: List[str] = []

    if any(w in t for w in CRITICAL_WORDS):
        score += 40
        issues.append(
            "Обнаружены критические формулировки (возможное мошенничество)"
        )
        recs.append("Проверить наличие доказательств и историю контрагента")

    if len(t.strip()) < 50:
        score += 10
        issues.append("Текст слишком короткий")
        recs.append("Запросить детали: даты, суммы, документы")

    # risk_level + auto_action
    if score >= 70:
        level = "critical"
        action = "auto_penalty"
    elif score >= 50:
        level = "high_risk"
        action = "auto_confirm"
    elif score >= 30:
        level = "medium_risk"
        action = "send_to_admin"
    else:
        level = "low_risk"
        action = "none"

    return AiModerationResult(
        risk_level=level,
        risk_score=min(score, 100),
        issues=issues,
        recommendations=recs,
        auto_action=action,
        analyzed_at=_now_iso_utc(),
        source=source,
    )


def analyze_forum_text(text: str, source: str = "auto") -> AiModerationResult:
    """Анализ текста поста на форуме.

    auto_action:
      - high_risk (50+) → block
      - medium (30+)    → send_to_admin
      - низкий риск     → publish
    """
    t = (text or "").lower()
    score = 0
    issues: List[str] = []
    recs: List[str] = []

    if any(w in t for w in INSULT_WORDS):
        score += 30
        issues.append("Возможные оскорбления")
        recs.append("Попросить переформулировать без оскорблений")

    if "без доказатель" in t or "нет доказатель" in t:
        score += 15
        issues.append("Нет доказательств")
        recs.append("Добавить фото/документы/скриншоты")

    if len(t.strip()) < 50:
        score += 10
        issues.append("Пост слишком короткий")
        recs.append("Добавить факты: кто/что/когда/суммы/маршрут")

    if score >= 50:
        level = "high_risk"
        action = "block"
    elif score >= 30:
        level = "medium_risk"
        action = "send_to_admin"
    else:
        level = "low_risk"
        action = "publish"

    # Булевы флаги под UI
    if level == "low_risk":
        needs_moderation = False
        can_publish = True
    else:
        needs_moderation = True
        can_publish = False

    return AiModerationResult(
        risk_level=level,
        risk_score=min(score, 100),
        issues=issues,
        recommendations=recs,
        auto_action=action,
        needs_moderation=needs_moderation,
        can_publish=can_publish,
        analyzed_at=_now_iso_utc(),
        source=source,
    )

