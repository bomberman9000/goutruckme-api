"""
Traffic Light (Светофор) — company due-diligence endpoint.
GET /api/traffic-light?inn=...

Free checks:  ФНС статус, возраст, уставной капитал, блэклист, платформа
Pro checks:   ФССП долги, Арбитраж (картотека), Лицензии Минтранс
"""
import httpx
import re
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query, Depends, Header
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User, Blacklist
from app.core.config import settings

router = APIRouter()

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
FSSP_URL   = "https://api.fssprus.ru/api/v1.0/search/organization"
KAD_URL    = "https://kad.arbitr.ru/Kad/SearchInstances"

_cache: dict = {}
_CACHE_TTL = 300  # 5 min


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ts_to_year(ts_ms) -> int | None:
    if not ts_ms:
        return None
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).year
    except Exception:
        return None


def _age_days(ts_ms) -> int | None:
    if not ts_ms:
        return None
    try:
        reg = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        return (datetime.now(tz=timezone.utc) - reg).days
    except Exception:
        return None


def _get_user_from_auth(authorization: str | None, db: Session) -> User | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    from app.core.auth import decode_access_token
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub") or payload.get("user_id")
        if not user_id:
            return None
        return db.query(User).filter(User.id == int(user_id)).first()
    except Exception:
        return None


def _is_pro(user: User | None) -> bool:
    if not user:
        return False
    return bool(user.pro_until and user.pro_until > datetime.utcnow())


# ── Data fetchers ─────────────────────────────────────────────────────────────

async def _dadata_fetch(inn: str) -> dict:
    token = settings.DADATA_API_TOKEN
    if not token:
        raise HTTPException(503, "DaData не настроен")
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.post(
            DADATA_URL,
            json={"query": inn, "count": 1},
            headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
        )
    if resp.status_code != 200:
        raise HTTPException(502, "Ошибка запроса к DaData")
    suggestions = resp.json().get("suggestions", [])
    return suggestions[0] if suggestions else {}


async def _fssp_fetch(inn: str) -> dict:
    """ФССП — проверка исполнительных производств по ИНН юрлица."""
    token = getattr(settings, "FSSP_API_TOKEN", "") or ""
    if not token:
        return {"available": False, "reason": "FSSP_API_TOKEN не настроен"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                FSSP_URL,
                params={"inn": inn, "token": token},
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            return {"available": False, "reason": f"ФССП HTTP {resp.status_code}"}
        data = resp.json()
        items = data.get("result", {}).get("items", []) or []
        total = len(items)
        total_debt = sum(
            float(item.get("sum", 0) or 0)
            for item in items
            if item.get("sum")
        )
        return {
            "available": True,
            "total_cases": total,
            "total_debt_rub": round(total_debt, 2),
            "items": items[:5],  # первые 5
        }
    except Exception as e:
        return {"available": False, "reason": str(e)[:100]}


async def _arbitr_fetch(inn: str) -> dict:
    """Картотека арбитражных дел — поиск по ИНН через kad.arbitr.ru."""
    try:
        headers = {
            "Accept": "application/json, text/javascript",
            "Content-Type": "application/json",
            "X-Date": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Referer": "https://kad.arbitr.ru/",
            "User-Agent": "Mozilla/5.0",
        }
        payload = {
            "Page": 1,
            "Count": 10,
            "Courts": [],
            "DateFrom": None,
            "DateTo": None,
            "Sides": [{"Name": "", "Inn": inn, "SideType": "Respondent"}],
            "Judges": [],
            "CaseType": 0,
        }
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.post(KAD_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            return {"available": False, "reason": f"kad.arbitr.ru HTTP {resp.status_code}"}
        data = resp.json()
        result = data.get("Result") or data.get("result") or {}
        items = result.get("Items") or result.get("items") or []
        total_count = result.get("TotalCount") or result.get("totalCount") or len(items)
        return {
            "available": True,
            "total_cases": int(total_count),
            "items": [
                {
                    "number": item.get("CaseId") or item.get("Number"),
                    "date": item.get("Date"),
                    "subject": item.get("Subject") or item.get("Description", "")[:100],
                }
                for item in (items[:5] if items else [])
            ],
        }
    except Exception as e:
        return {"available": False, "reason": str(e)[:100]}


def _blacklist_check(inn: str, db: Session) -> dict:
    entry = db.query(Blacklist).filter(Blacklist.inn == inn).first()
    if not entry:
        return {"blacklisted": False}
    return {
        "blacklisted": True,
        "name": entry.name,
        "reason": entry.reason,
        "flags": entry.flags or [],
    }


def _platform_check(inn: str, db: Session) -> dict:
    user = db.query(User).filter(User.inn == inn).first()
    if not user:
        return {"found": False}
    trust_score = None
    trust_flags = []
    if hasattr(user, "trust_stats") and user.trust_stats:
        trust_score = user.trust_stats.trust_score
        flags_raw = getattr(user.trust_stats, "flags", None)
        if flags_raw:
            import json as _j
            try:
                trust_flags = _j.loads(flags_raw) if isinstance(flags_raw, str) else (flags_raw or [])
            except Exception:
                trust_flags = []
    return {
        "found": True,
        "gtp_code": f"ГТП-{user.id:06d}",
        "verified": bool(user.verified),
        "trust_score": trust_score,
        "trust_flags": trust_flags,
        "complaints": user.complaints or 0,
        "successful_deals": user.successful_deals or 0,
        "days_on_platform": (_age_days(user.created_at.timestamp() * 1000) if user.created_at else None),
    }


def _build_verdict(checks: list[dict]) -> str:
    reds    = sum(1 for c in checks if c["level"] == "red"    and not c.get("locked"))
    yellows = sum(1 for c in checks if c["level"] == "yellow" and not c.get("locked"))
    if reds > 0:
        return "red"
    if yellows >= 1:
        return "yellow"
    return "green"


def _locked_check(id: str, label: str) -> dict:
    return {
        "id": id,
        "label": label,
        "level": "yellow",
        "locked": True,
        "detail": "Доступно на тарифе Pro · Подключить в разделе Тарифы",
    }


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/traffic-light")
async def traffic_light(
    inn: str = Query(..., min_length=10, max_length=12),
    authorization: str | None = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    """Светофор — комплексная проверка контрагента по ИНН."""
    inn = inn.strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        raise HTTPException(422, "ИНН должен содержать 10 или 12 цифр")

    user = _get_user_from_auth(authorization, db)
    pro = _is_pro(user)

    # Cache (separate for pro/free)
    cache_key = f"{inn}:{'pro' if pro else 'free'}"
    now = datetime.utcnow()
    cached = _cache.get(cache_key)
    if cached and (now - cached["ts"]).total_seconds() < _CACHE_TTL:
        return cached["data"]

    # 1. DaData (all users)
    suggestion = await _dadata_fetch(inn)
    d = suggestion.get("data", {}) if suggestion else {}
    state = d.get("state", {}) or {}

    company_name = (suggestion.get("value") or "").strip() or None
    status       = state.get("status") or ""
    reg_ts       = state.get("registration_date")
    age_days     = _age_days(reg_ts)
    reg_year     = _ts_to_year(reg_ts)
    capital      = d.get("authorized_capital")
    director     = (d.get("management") or {}).get("name") or ""
    company_type = d.get("type") or ""
    okved        = d.get("okved") or ""
    ogrn         = d.get("ogrn") or ""

    # 2. Blacklist (all users)
    bl = _blacklist_check(inn, db)

    # 3. Platform (all users)
    plat = _platform_check(inn, db)

    # 4. Pro: ФССП + Арбитраж
    fssp_data   = None
    arbitr_data = None
    if pro:
        fssp_data, arbitr_data = await _fssp_fetch(inn), await _arbitr_fetch(inn)

    # 5. Build checks
    checks: list[dict] = []

    # Blacklist — always first
    if bl["blacklisted"]:
        flags_str = ", ".join(bl.get("flags") or [])
        detail = bl.get("reason") or "Компания в чёрном списке ГрузПоток"
        if flags_str:
            detail += f" ({flags_str})"
        checks.append({"id": "blacklist", "label": "Чёрный список", "level": "red", "detail": detail})

    # ФНС статус
    if status == "ACTIVE":
        checks.append({"id": "status", "label": "Статус ФНС", "level": "green", "detail": "Компания действующая"})
    elif status in ("LIQUIDATING", "REORGANIZING"):
        checks.append({"id": "status", "label": "Статус ФНС", "level": "red",
                        "detail": f"В процессе {'ликвидации' if status=='LIQUIDATING' else 'реорганизации'}"})
    elif status in ("LIQUIDATED", "BANKRUPT"):
        checks.append({"id": "status", "label": "Статус ФНС", "level": "red",
                        "detail": f"{'Ликвидирована' if status=='LIQUIDATED' else 'Банкрот'}"})
    elif not status:
        checks.append({"id": "status", "label": "Статус ФНС", "level": "yellow", "detail": "Не удалось получить статус"})
    else:
        checks.append({"id": "status", "label": "Статус ФНС", "level": "yellow", "detail": f"Статус: {status}"})

    # Возраст
    if age_days is not None:
        if age_days < 180:
            checks.append({"id": "age", "label": "Возраст компании", "level": "red",
                            "detail": f"Зарегистрирована {age_days} дн. назад — высокий риск"})
        elif age_days < 365:
            checks.append({"id": "age", "label": "Возраст компании", "level": "yellow",
                            "detail": f"Младше 1 года ({age_days} дн.)"})
        else:
            years = age_days // 365
            checks.append({"id": "age", "label": "Возраст компании", "level": "green",
                            "detail": f"Работает {years} {'год' if years==1 else 'лет' if years>=5 else 'года'} (с {reg_year})"})

    # Уставной капитал
    if company_type == "LEGAL" and capital is not None:
        if capital <= 10000:
            checks.append({"id": "capital", "label": "Уставной капитал", "level": "yellow",
                            "detail": f"Минимальный: {capital:,} ₽".replace(",", " ")})
        else:
            checks.append({"id": "capital", "label": "Уставной капитал", "level": "green",
                            "detail": f"{capital:,} ₽".replace(",", " ")})

    # Платформа
    if plat["found"]:
        ts = plat.get("trust_score")
        complaints = plat.get("complaints", 0)
        if complaints > 0:
            checks.append({"id": "complaints", "label": "Жалобы на платформе",
                            "level": "red" if complaints > 2 else "yellow",
                            "detail": f"{complaints} жалоб{'ы' if complaints < 5 else ''} на ГрузПоток"})
        if ts is not None:
            if ts >= 70:
                checks.append({"id": "platform_trust", "label": "Репутация ГрузПоток", "level": "green",
                                "detail": f"Trust score {ts}/100 · {plat['successful_deals']} сделок"})
            elif ts >= 40:
                checks.append({"id": "platform_trust", "label": "Репутация ГрузПоток", "level": "yellow",
                                "detail": f"Trust score {ts}/100 — недостаточно данных"})
            else:
                checks.append({"id": "platform_trust", "label": "Репутация ГрузПоток", "level": "red",
                                "detail": f"Trust score {ts}/100 — низкий рейтинг"})
    else:
        checks.append({"id": "platform", "label": "Присутствие на ГрузПоток", "level": "yellow",
                        "detail": "Компания не зарегистрирована на платформе"})

    # ── Pro checks ────────────────────────────────────────────────────────────
    if not pro:
        checks.append(_locked_check("fssp",    "ФССП · Исполнительные производства"))
        checks.append(_locked_check("arbitr",  "Арбитраж · Картотека дел"))
        checks.append(_locked_check("license", "Лицензии Минтранс"))
    else:
        # ФССП
        if fssp_data and fssp_data.get("available"):
            cases = fssp_data["total_cases"]
            debt  = fssp_data["total_debt_rub"]
            if cases == 0:
                checks.append({"id": "fssp", "label": "ФССП · Исполнительные производства",
                                "level": "green", "detail": "Открытых исполнительных производств не найдено"})
            elif debt > 500_000:
                checks.append({"id": "fssp", "label": "ФССП · Исполнительные производства",
                                "level": "red", "detail": f"{cases} производств · долг {debt:,.0f} ₽".replace(",", " ")})
            else:
                checks.append({"id": "fssp", "label": "ФССП · Исполнительные производства",
                                "level": "yellow", "detail": f"{cases} производств · долг {debt:,.0f} ₽".replace(",", " ")})
        else:
            reason = (fssp_data or {}).get("reason", "Сервис недоступен")
            checks.append({"id": "fssp", "label": "ФССП · Исполнительные производства",
                            "level": "yellow", "detail": f"Не проверено: {reason}"})

        # Арбитраж
        if arbitr_data and arbitr_data.get("available"):
            cases = arbitr_data["total_cases"]
            if cases == 0:
                checks.append({"id": "arbitr", "label": "Арбитраж · Картотека дел",
                                "level": "green", "detail": "Дел в картотеке арбитражных судов не найдено"})
            elif cases > 10:
                checks.append({"id": "arbitr", "label": "Арбитраж · Картотека дел",
                                "level": "red", "detail": f"{cases} дел (как ответчик) — повышенный риск"})
            else:
                checks.append({"id": "arbitr", "label": "Арбитраж · Картотека дел",
                                "level": "yellow", "detail": f"{cases} дел в арбитраже (как ответчик)"})
        else:
            reason = (arbitr_data or {}).get("reason", "Сервис недоступен")
            checks.append({"id": "arbitr", "label": "Арбитраж · Картотека дел",
                            "level": "yellow", "detail": f"Не проверено: {reason}"})

        # Лицензии (Минтранс — нет открытого API, показываем статус на основе DaData ОКВЭД)
        transport_okveds = {"49.", "52.", "53."}
        has_transport = any(okved.startswith(p) for p in transport_okveds) if okved else False
        if has_transport:
            checks.append({"id": "license", "label": "Лицензии Минтранс",
                            "level": "green", "detail": f"ОКВЭД {okved} — транспортная деятельность подтверждена"})
        elif okved:
            checks.append({"id": "license", "label": "Лицензии Минтранс",
                            "level": "yellow", "detail": f"ОКВЭД {okved} — не транспортный профиль"})
        else:
            checks.append({"id": "license", "label": "Лицензии Минтранс",
                            "level": "yellow", "detail": "ОКВЭД не определён"})

    verdict = _build_verdict(checks)

    sources = ["ФНС (DaData)", "ГрузПоток", "Чёрный список"]
    if pro:
        sources += ["ФССП", "kad.arbitr.ru"]

    result = {
        "inn": inn,
        "verdict": verdict,
        "verdict_label": {"green": "Надёжный партнёр", "yellow": "Проверьте внимательно", "red": "Высокий риск"}.get(verdict, ""),
        "verdict_icon": {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(verdict, "⚪"),
        "is_pro": pro,
        "company": {
            "name": company_name,
            "inn": inn,
            "ogrn": ogrn,
            "status": status,
            "type": "ООО/АО" if company_type == "LEGAL" else "ИП" if company_type == "INDIVIDUAL" else company_type,
            "director": director,
            "okved": okved,
            "age_days": age_days,
            "reg_year": reg_year,
            "capital": capital,
            "address": (d.get("address") or {}).get("value") or "",
        },
        "platform": plat if plat["found"] else None,
        "blacklisted": bl["blacklisted"],
        "checks": checks,
        "checked_at": now.isoformat(),
        "sources": sources,
    }

    _cache[cache_key] = {"data": result, "ts": now}
    return result
