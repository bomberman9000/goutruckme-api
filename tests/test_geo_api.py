from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import geo as geo_api
from src.core.geo import city_coords
from src.core.services.geo_service import GeoService


class _FakeGeoService:
    async def suggest_cities(self, query: str, *, limit: int = 5):
        assert query == "Екат"
        return [
            {
                "name": "Екатеринбург",
                "full_name": "Екатеринбург, Свердловская область, Россия",
                "lat": 56.84,
                "lon": 60.61,
                "source": "stub",
            }
        ][:limit]


def test_geo_cities_returns_suggestions(monkeypatch):
    app = FastAPI()
    app.include_router(geo_api.router)
    monkeypatch.setattr(geo_api, "get_geo_service", lambda: _FakeGeoService())
    client = TestClient(app)

    response = client.get("/api/v1/geo/cities?q=Екат&limit=3")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["name"] == "Екатеринбург"
    assert body["items"][0]["source"] == "stub"


def test_geo_cities_short_query_returns_empty():
    app = FastAPI()
    app.include_router(geo_api.router)
    client = TestClient(app)

    response = client.get("/api/v1/geo/cities?q=Е")

    assert response.status_code == 422


def test_geo_city_directory_uses_shared_catalog():
    app = FastAPI()
    app.include_router(geo_api.router)
    client = TestClient(app)

    response = client.get("/api/v1/geo/cities/directory?q=Таш&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["items"]
    assert any(item["name"] == "Ташкент" for item in body["items"])
    assert all(item["source"] == "directory" for item in body["items"])


def test_geo_city_directory_can_list_without_query():
    app = FastAPI()
    app.include_router(geo_api.router)
    client = TestClient(app)

    response = client.get("/api/v1/geo/cities/directory?limit=3")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3


def test_geo_city_resolve_returns_canonical_city():
    app = FastAPI()
    app.include_router(geo_api.router)
    client = TestClient(app)

    response = client.get("/api/v1/geo/cities/resolve?name=город Бишкек")

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] == "Бишкек"


def test_geo_service_rejects_non_city_nominatim_rows():
    service = GeoService()

    payload = [
        {
            "display_name": 'УК "Социум строй", Москва',
            "class": "building",
            "type": "commercial",
        }
    ]

    assert service._pick_city_candidate(payload) is None


def test_city_coords_contains_common_cis_cities():
    assert city_coords("Ташкент") is not None
    assert city_coords("Гомель") is not None
    assert city_coords("Брест") is not None
    assert city_coords("Бишкек") is not None
