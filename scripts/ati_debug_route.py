"""
Диагностика ATI.SU API — сессия и форматы фильтра.
Запуск: uv run python scripts/ati_debug_route.py
"""
import json
import os
import httpx

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "ati_state.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://loads.ati.su",
    "Referer": "https://loads.ati.su/",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def load_cookies() -> dict:
    with open(STATE_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return {c["name"]: c["value"] for c in raw}


def try_payload(client, label, payload):
    r = client.post(
        "https://loads.ati.su/webapi/v1.0/loads/search",
        json=payload,
    )
    data = r.json() if r.status_code == 200 else {}
    loads = data.get("loads", [])
    total = data.get("totalItems", "?")
    auth = data.get("isUserAuthorized", "?")
    print(f"\n[{label}] status={r.status_code} total={total} loads={len(loads)} auth={auth}")
    if loads:
        print(json.dumps(loads[0], ensure_ascii=False, indent=2))
    return loads


def main():
    cookies = load_cookies()
    print(f"Cookies: {list(cookies.keys())}")

    # Проверяем профиль
    with httpx.Client(cookies=cookies, timeout=30, headers=HEADERS) as client:
        # 1. Профиль — проверить что сессия живая
        r_profile = client.get("https://d.ati.su/webapi/profile/v1/profile")
        print(f"\nProfile status: {r_profile.status_code}")
        if r_profile.status_code == 200:
            profile = r_profile.json()
            print(f"  firmName: {profile.get('firmName')}")
            print(f"  atiCode: {profile.get('atiCode')}")
            print(f"  login: {profile.get('login')}")

        # 2. Разные форматы фильтра
        payloads = [
            ("no filter + take=5", {
                "paging": {"skip": 0, "take": 5},
            }),
            ("empty filter", {
                "filter": {},
                "paging": {"skip": 0, "take": 5},
            }),
            ("points format", {
                "filter": {
                    "points": [
                        {"types": [2], "ids": [220]},
                        {"types": [2], "ids": [80]},
                    ]
                },
                "paging": {"skip": 0, "take": 5},
            }),
            ("fromGeos/toGeos", {
                "filter": {
                    "fromGeos": [{"id": 220, "type": 2}],
                    "toGeos": [{"id": 80, "type": 2}],
                },
                "paging": {"skip": 0, "take": 5},
            }),
            ("geoFrom/geoTo", {
                "filter": {
                    "geoFrom": [{"id": 220, "type": 2}],
                    "geoTo": [{"id": 80, "type": 2}],
                },
                "paging": {"skip": 0, "take": 5},
            }),
        ]

        for label, payload in payloads:
            loads = try_payload(client, label, payload)
            if loads:
                print("✅ Found loads with this format!")
                break


if __name__ == "__main__":
    main()
