"""INN lookup via DaData proxy."""
import httpx
from fastapi import APIRouter, HTTPException, Query
from app.core.config import settings

router = APIRouter()

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


@router.get("/inn-lookup")
async def inn_lookup(inn: str = Query(..., min_length=10, max_length=12)):
    """Lookup company/IP info by INN via DaData."""
    inn = inn.strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        raise HTTPException(status_code=422, detail="ИНН должен содержать 10 или 12 цифр")

    token = settings.DADATA_API_TOKEN
    if not token:
        raise HTTPException(status_code=503, detail="DaData не настроен")

    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.post(
            DADATA_URL,
            json={"query": inn, "count": 1},
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Ошибка запроса к DaData")

    data = resp.json()
    suggestions = data.get("suggestions", [])
    if not suggestions:
        raise HTTPException(status_code=404, detail="Компания не найдена")

    s = suggestions[0]
    d = s.get("data", {})

    # Extract fields
    name = s.get("value") or d.get("name", {}).get("full_with_opf", "")
    ogrn = d.get("ogrn") or ""
    address = (d.get("address") or {}).get("value") or ""
    phone = (d.get("phones") or [{}])[0].get("value") if d.get("phones") else ""
    email = (d.get("emails") or [{}])[0].get("value") if d.get("emails") else ""

    # Director/manager
    director = ""
    management = d.get("management") or {}
    if management:
        director = management.get("name") or ""

    return {
        "inn": inn,
        "name": name,
        "ogrn": ogrn,
        "address": address,
        "phone": phone or "",
        "email": email or "",
        "director": director,
        "kpp": d.get("kpp") or "",
        "okved": d.get("okved") or "",
        "status": (d.get("state") or {}).get("status") or "",
    }


# ── Address suggestions (DaData proxy) ─────────────────────────────────────
@router.get("/address-suggest")
async def address_suggest(q: str, count: int = 7):
    """Подсказки адресов через DaData."""
    token = settings.DADATA_API_TOKEN
    if not token or not q.strip():
        return {"suggestions": []}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(
                "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
                headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
                json={"query": q, "count": count, "locations": [{"country": "*"}]},
            )
        if r.status_code == 200:
            data = r.json()
            return {"suggestions": [
                {"value": s["value"], "data": {
                    "city": s["data"].get("city") or s["data"].get("settlement") or "",
                    "street": s["data"].get("street") or "",
                    "house": s["data"].get("house") or "",
                    "lat": s["data"].get("geo_lat"),
                    "lon": s["data"].get("geo_lon"),
                }}
                for s in data.get("suggestions", [])
            ]}
    except Exception as e:
        pass
    return {"suggestions": []}
