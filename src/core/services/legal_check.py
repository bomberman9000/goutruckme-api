"""
Светофор — проверка контрагента по ИНН.

Free: ФНС + Арбитраж (счётчики) + ФССП + Банкротство
Pro:  + DaData (учредители, ОКВЭД, капитал)
      + РНП ФАС (реестр недобросовестных поставщиков)
      + Реестр залогов движимого имущества
      + Детализация арбитража (суммы, последние дела)
"""

import os
import httpx
from datetime import datetime

from src.core.logger import logger


async def check_fns(inn: str) -> dict:
    """Проверка в ФНС — статус, название, адрес, директор"""
    url = "https://egrul.nalog.ru/api/search"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json={"query": inn})
            data = resp.json()

            if not data.get("rows"):
                return {
                    "status": "not_found",
                    "message": "Компания не найдена в ЕГРЮЛ",
                }

            row = data["rows"][0]
            token = row.get("t")

            detail_resp = await client.get(
                f"https://egrul.nalog.ru/api/vyp/{token}"
            )
            detail_resp.raise_for_status()

            return {
                "status": "ok",
                "name": row.get("n", ""),
                "inn": row.get("i", ""),
                "ogrn": row.get("o", ""),
                "address": row.get("a", ""),
                "director": row.get("g", ""),
                "reg_date": row.get("r", ""),
                "is_active": row.get("s") != "ликвидирована",
                "raw": row,
            }
    except Exception as e:
        logger.error("FNS check error: %s", e)
        return {"status": "error", "message": str(e)}


async def check_arbitr(inn: str) -> dict:
    """Проверка арбитражных дел на kad.arbitr.ru"""
    url = "https://kad.arbitr.ru/Kad/SearchInstances"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; LogisticsBot/1.0)",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "Participants": [{"Name": inn, "Type": -1}],
        "Page": 1,
        "Count": 25,
        "DateFrom": None,
        "DateTo": None,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=payload, headers=headers)
            data = resp.json()

            result = data.get("Result", {})
            cases = result.get("Items", [])
            total = result.get("TotalCount", 0)

            as_plaintiff = 0
            as_defendant = 0

            for case in cases:
                for p in case.get("Participants", []):
                    if inn in str(p.get("Inn", "")):
                        if p.get("Type") == 1:
                            as_plaintiff += 1
                        elif p.get("Type") == 2:
                            as_defendant += 1

            return {
                "status": "ok",
                "total_cases": total,
                "as_plaintiff": as_plaintiff,
                "as_defendant": as_defendant,
                "recent_cases": cases[:5],
            }
    except Exception as e:
        logger.error("Arbitr check error: %s", e)
        return {"status": "error", "message": str(e)}


async def check_fssp(inn: str, region: int = 0) -> dict:
    """Проверка исполнительных производств в ФССП (по названию из ФНС)."""
    url = "https://fssp.gov.ru/iss/ip"

    try:
        fns = await check_fns(inn)
        if fns["status"] != "ok":
            return {"status": "skip", "message": "Не удалось получить название"}

        name = fns.get("name", "")

        async with httpx.AsyncClient(timeout=15) as client:
            params = {
                "is": "ip",
                "searchstring": name[:50],
                "region_id": region,
            }
            resp = await client.get(url, params=params)

        has_debts = (
            "Найдено:" in resp.text and "Ничего не найдено" not in resp.text
        )
        msg = (
            "Найдены исполнительные производства"
            if has_debts
            else "Долгов не найдено"
        )
        return {"status": "ok", "has_debts": has_debts, "message": msg}
    except Exception as e:
        logger.error("FSSP check error: %s", e)
        return {"status": "error", "message": str(e)}


async def check_bankrupt(inn: str) -> dict:
    """Проверка на банкротство в fedresurs"""
    url = "https://bankrot.fedresurs.ru/backend/sfactmessages"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            params = {
                "searchString": inn,
                "pageSize": 10,
            }
            resp = await client.get(url, params=params)
            data = resp.json()

        messages = data.get("pageData", [])
        is_bankrupt = len(messages) > 0

        msg = (
            "⚠️ Есть сведения о банкротстве"
            if is_bankrupt
            else "Банкротств не найдено"
        )
        return {
            "status": "ok",
            "is_bankrupt": is_bankrupt,
            "messages_count": len(messages),
            "message": msg,
        }
    except Exception as e:
        logger.error("Bankrupt check error: %s", e)
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Pro checks
# ---------------------------------------------------------------------------

async def check_dadata_pro(inn: str) -> dict:
    """Расширенные данные компании через DaData (учредители, ОКВЭД, капитал)."""
    token = os.getenv("DADATA_API_TOKEN", "")
    if not token:
        return {"status": "skip", "message": "DaData токен не настроен"}
    url = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={"query": inn, "count": 1},
                headers={
                    "Authorization": f"Token {token}",
                    "Content-Type": "application/json",
                },
            )
        suggestions = resp.json().get("suggestions", [])
        if not suggestions:
            return {"status": "not_found"}
        d = suggestions[0].get("data", {})
        founders = []
        for f in (d.get("founders") or [])[:5]:
            name = f.get("fio", {}).get("name") or f.get("name", "")
            share = f.get("share", {}).get("value", "")
            founders.append(f"{name} {share}%".strip())
        managers = []
        for m in (d.get("managers") or [])[:3]:
            fio = m.get("fio", {})
            name = f"{fio.get('surname','')} {fio.get('name','')} {fio.get('patronymic','')}".strip()
            post = m.get("post", "")
            managers.append(f"{name} ({post})" if post else name)
        capital = (d.get("finance") or {}).get("ustavnyj_kapital")
        okved = d.get("okved", "")
        okved_name = d.get("okved_type", "")
        state = (d.get("state") or {})
        liquidation_date = state.get("liquidation_date")
        return {
            "status": "ok",
            "founders": founders,
            "managers": managers,
            "capital_rub": capital,
            "okved": okved,
            "okved_name": okved_name,
            "employee_count": (d.get("finance") or {}).get("average_employees"),
            "liquidation_date": liquidation_date,
            "registration_date": state.get("registration_date"),
        }
    except Exception as e:
        logger.warning("dadata_pro error inn=%s error=%s", inn, e)
        return {"status": "error", "message": str(e)}


async def check_rnp(inn: str) -> dict:
    """Проверка в реестре недобросовестных поставщиков ФАС."""
    url = "https://rnp.fas.gov.ru/rnp/search/json"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, params={"searchString": inn, "page": 0, "size": 5})
        if resp.status_code != 200:
            return {"status": "skip", "message": f"РНП недоступен ({resp.status_code})"}
        data = resp.json()
        items = data.get("data", []) or data.get("items", []) or (data if isinstance(data, list) else [])
        in_rnp = len(items) > 0
        return {
            "status": "ok",
            "in_rnp": in_rnp,
            "count": len(items),
            "message": f"⛔ В РНП ({len(items)} записей)" if in_rnp else "✅ Не в реестре недобросовестных",
        }
    except Exception as e:
        logger.warning("rnp check error inn=%s error=%s", inn, e)
        return {"status": "error", "message": str(e)}


async def check_zalog(inn: str) -> dict:
    """Проверка залогов движимого имущества (reestr-zalogov.ru)."""
    url = "https://www.reestr-zalogov.ru/search/index"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, params={
                "notariatType": 2,
                "pledgorInn": inn,
                "page": 0,
            })
        has_zalog = "Ничего не найдено" not in resp.text and str(inn) in resp.text
        return {
            "status": "ok",
            "has_zalog": has_zalog,
            "message": "⚠️ Найдены залоги движимого имущества" if has_zalog else "✅ Залогов не обнаружено",
        }
    except Exception as e:
        logger.warning("zalog check error inn=%s error=%s", inn, e)
        return {"status": "error", "message": str(e)}


async def full_legal_check(inn: str) -> dict:
    """Полная проверка контрагента по ИНН."""
    results = {
        "inn": inn,
        "checked_at": datetime.utcnow().isoformat(),
        "fns": await check_fns(inn),
        "arbitr": await check_arbitr(inn),
        "fssp": await check_fssp(inn),
        "bankrupt": await check_bankrupt(inn),
    }

    risk_score = 0
    risk_factors = []

    fns = results["fns"]
    if fns["status"] == "ok":
        if not fns.get("is_active"):
            risk_score += 50
            risk_factors.append("❌ Компания ликвидирована")
    else:
        risk_score += 30
        risk_factors.append("⚠️ Не найдена в ЕГРЮЛ")

    arbitr = results["arbitr"]
    if arbitr["status"] == "ok":
        defendant_cases = arbitr.get("as_defendant", 0)
        if defendant_cases > 10:
            risk_score += 30
            risk_factors.append(
                f"🔴 Много судов как ответчик ({defendant_cases})"
            )
        elif defendant_cases > 3:
            risk_score += 15
            risk_factors.append(
                f"🟡 Есть суды как ответчик ({defendant_cases})"
            )

    fssp = results["fssp"]
    if fssp["status"] == "ok" and fssp.get("has_debts"):
        risk_score += 25
        risk_factors.append("🔴 Есть исполнительные производства")

    bankrupt = results["bankrupt"]
    if bankrupt["status"] == "ok" and bankrupt.get("is_bankrupt"):
        risk_score += 50
        risk_factors.append("❌ Процедура банкротства")

    results["risk_score"] = min(100, risk_score)
    results["risk_factors"] = risk_factors
    results["risk_level"] = (
        "🟢 Низкий"
        if risk_score < 20
        else "🟡 Средний"
        if risk_score < 50
        else "🔴 Высокий"
    )

    return results


def format_legal_check(result: dict) -> str:
    """Форматирует результат проверки для отправки пользователю."""
    text = "🔍 <b>Проверка контрагента</b>\n"
    text += f"ИНН: {result['inn']}\n\n"

    fns = result.get("fns", {})
    if fns.get("status") == "ok":
        text += f"🏢 <b>{fns.get('name', 'Без названия')}</b>\n"
        addr = fns.get("address", "Адрес не указан")
        suffix = "..." if len(addr) > 50 else ""
        text += f"📍 {addr[:50]}{suffix}\n"
        text += f"👤 Директор: {fns.get('director', 'Не указан')}\n"
        text += f"📅 Регистрация: {fns.get('reg_date', '?')}\n"
        status = "✅ Действующая" if fns.get("is_active") else "❌ Ликвидирована"
        text += f"📊 Статус: {status}\n\n"
    else:
        text += f"⚠️ ФНС: {fns.get('message', 'Ошибка проверки')}\n\n"

    arbitr = result.get("arbitr", {})
    if arbitr.get("status") == "ok":
        text += "⚖️ <b>Арбитражные суды:</b>\n"
        text += f"   Всего дел: {arbitr.get('total_cases', 0)}\n"
        text += f"   Истец: {arbitr.get('as_plaintiff', 0)}\n"
        text += f"   Ответчик: {arbitr.get('as_defendant', 0)}\n\n"

    fssp = result.get("fssp", {})
    if fssp.get("status") == "ok":
        text += f"📋 <b>ФССП:</b> {fssp.get('message', '?')}\n\n"

    bankrupt = result.get("bankrupt", {})
    if bankrupt.get("status") == "ok":
        text += f"💀 <b>Банкротство:</b> {bankrupt.get('message', '?')}\n\n"

    text += "━━━━━━━━━━━━━━━━━━━━\n"
    risk = result.get("risk_score", 0)
    text += f"📊 <b>Риск-скор: {risk}/100</b>\n"
    text += f"🚦 Уровень риска: {result.get('risk_level', '?')}\n"

    if result.get("risk_factors"):
        text += "\n⚠️ <b>Факторы риска:</b>\n"
        for factor in result["risk_factors"]:
            text += f"• {factor}\n"

    return text


# ---------------------------------------------------------------------------
# Pro full check
# ---------------------------------------------------------------------------

async def full_legal_check_pro(inn: str) -> dict:
    """Полная Pro-проверка: базовые источники + DaData + РНП + Залоги."""
    import asyncio
    base, dadata, rnp, zalog = await asyncio.gather(
        full_legal_check(inn),
        check_dadata_pro(inn),
        check_rnp(inn),
        check_zalog(inn),
    )
    result = {**base, "dadata": dadata, "rnp": rnp, "zalog": zalog, "is_pro": True}

    # Extra risk scoring for Pro
    extra_score = 0
    extra_factors = list(base.get("risk_factors", []))

    if rnp.get("status") == "ok" and rnp.get("in_rnp"):
        extra_score += 40
        extra_factors.append(f"⛔ Реестр недобросовестных поставщиков ({rnp['count']} записей)")

    if zalog.get("status") == "ok" and zalog.get("has_zalog"):
        extra_score += 15
        extra_factors.append("⚠️ Залоги движимого имущества")

    if dadata.get("status") == "ok":
        cap = dadata.get("capital_rub")
        if cap and cap < 10_000:
            extra_score += 10
            extra_factors.append(f"⚠️ Минимальный уставный капитал ({cap:,} ₽)")

    total = min(100, base.get("risk_score", 0) + extra_score)
    result["risk_score"] = total
    result["risk_factors"] = extra_factors
    result["risk_level"] = (
        "🟢 Низкий" if total < 20
        else "🟡 Средний" if total < 50
        else "🔴 Высокий"
    )
    return result


def format_legal_check_pro(result: dict) -> str:
    """Форматирует Pro-отчёт проверки."""
    # Base section (reuse base formatter intro)
    text = "🔍 <b>Проверка контрагента — Pro</b>\n"
    text += f"ИНН: <code>{result['inn']}</code>\n\n"

    fns = result.get("fns", {})
    if fns.get("status") == "ok":
        status = "✅ Действующая" if fns.get("is_active") else "❌ Ликвидирована"
        text += f"🏢 <b>{fns.get('name', '—')}</b>\n"
        text += f"📍 {fns.get('address', '—')[:60]}\n"
        text += f"👤 Директор: {fns.get('director', '—')}\n"
        text += f"📅 Регистрация: {fns.get('reg_date', '—')}\n"
        text += f"📊 Статус: {status}\n\n"

    # DaData block
    dd = result.get("dadata", {})
    if dd.get("status") == "ok":
        text += "📋 <b>Расширенные данные (DaData):</b>\n"
        if dd.get("okved"):
            text += f"   ОКВЭД: {dd['okved']} {dd.get('okved_name','')[:40]}\n"
        if dd.get("capital_rub") is not None:
            cap = dd["capital_rub"]
            text += f"   💰 Уставный капитал: {cap:,} ₽\n".replace(",", " ")
        if dd.get("employee_count"):
            text += f"   👥 Сотрудников: {dd['employee_count']}\n"
        if dd.get("founders"):
            text += f"   🏛 Учредители: {', '.join(dd['founders'][:3])}\n"
        if dd.get("managers"):
            text += f"   👔 Руководство: {dd['managers'][0]}\n"
        text += "\n"

    # Arbitr
    arbitr = result.get("arbitr", {})
    if arbitr.get("status") == "ok":
        text += "⚖️ <b>Арбитражные суды:</b>\n"
        text += f"   Всего: {arbitr.get('total_cases', 0)} | "
        text += f"Истец: {arbitr.get('as_plaintiff', 0)} | "
        text += f"Ответчик: {arbitr.get('as_defendant', 0)}\n"
        for case in (arbitr.get("recent_cases") or [])[:2]:
            num = case.get("CaseId", "")
            if num:
                text += f"   📄 {num}\n"
        text += "\n"

    # ФССП
    fssp = result.get("fssp", {})
    if fssp.get("status") == "ok":
        text += f"📋 <b>ФССП:</b> {fssp.get('message','?')}\n"

    # Банкротство
    bankrupt = result.get("bankrupt", {})
    if bankrupt.get("status") == "ok":
        text += f"💀 <b>Банкротство:</b> {bankrupt.get('message','?')}\n"

    # РНП
    rnp = result.get("rnp", {})
    if rnp.get("status") == "ok":
        text += f"🏛 <b>РНП ФАС:</b> {rnp.get('message','?')}\n"

    # Залоги
    zalog = result.get("zalog", {})
    if zalog.get("status") == "ok":
        text += f"🔒 <b>Залоги:</b> {zalog.get('message','?')}\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━\n"
    risk = result.get("risk_score", 0)
    text += f"📊 <b>Риск-скор: {risk}/100</b>\n"
    text += f"🚦 {result.get('risk_level','?')}\n"

    factors = result.get("risk_factors", [])
    if factors:
        text += "\n⚠️ <b>Факторы риска:</b>\n"
        for f in factors:
            text += f"• {f}\n"

    return text
