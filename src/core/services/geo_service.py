from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import httpx

from src.core.config import settings
from src.core.geo import CITY_COORDS, _normalize_city_key, city_coords, haversine_km

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CityData:
    name: str
    full_name: str
    lat: float
    lon: float
    source: str = "local"


@dataclass(slots=True)
class RouteGeo:
    origin: CityData
    destination: CityData
    distance_km: int


class GeoService:
    def __init__(self) -> None:
        self.geo_url = settings.geo_nominatim_url.rstrip("/")
        self.osrm_url = settings.geo_osrm_url.rstrip("/")
        self.headers = {
            "User-Agent": settings.geo_user_agent,
            "Accept-Language": "ru,en",
        }
        self.timeout = max(2, int(settings.geo_http_timeout_sec))
        self._city_cache: dict[str, CityData | None] = {}
        self._distance_cache: dict[tuple[str, str], int] = {}

    def _local_city(self, city_name: str) -> CityData | None:
        key = _normalize_city_key(city_name)
        coords = city_coords(city_name)
        if not key or not coords:
            return None
        return CityData(
            name=key.title(),
            full_name=key.title(),
            lat=float(coords[0]),
            lon=float(coords[1]),
            source="local",
        )

    async def get_city_data(self, city_name: str) -> CityData | None:
        key = _normalize_city_key(city_name)
        if not key:
            return None
        if key in self._city_cache:
            return self._city_cache[key]

        local = self._local_city(city_name)
        if local:
            self._city_cache[key] = local
            return local

        params = {
            "q": city_name.strip(),
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.geo_url, params=params, headers=self.headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("geo geocode failed city=%s error=%s", city_name, str(exc)[:160])
            self._city_cache[key] = None
            return None

        if not isinstance(payload, list) or not payload:
            self._city_cache[key] = None
            return None

        row = payload[0]
        try:
            full_name = str(row.get("display_name") or city_name).strip()
            name = full_name.split(",")[0].strip() or city_name.strip()
            city = CityData(
                name=name,
                full_name=full_name,
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                source="nominatim",
            )
        except Exception:
            self._city_cache[key] = None
            return None

        self._city_cache[key] = city
        return city

    async def get_real_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> int | None:
        cache_key = (f"{lat1:.4f},{lon1:.4f}", f"{lat2:.4f},{lon2:.4f}")
        if cache_key in self._distance_cache:
            return self._distance_cache[cache_key]

        url = f"{self.osrm_url}/{lon1},{lat1};{lon2},{lat2}"
        params = {"overview": "false"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
                payload = response.json()
            if isinstance(payload, dict) and payload.get("code") == "Ok":
                routes = payload.get("routes") or []
                if routes:
                    meters = float(routes[0].get("distance") or 0.0)
                    if meters > 0:
                        distance_km = max(1, round(meters / 1000))
                        self._distance_cache[cache_key] = distance_km
                        return distance_km
        except Exception as exc:
            logger.warning("geo route failed error=%s", str(exc)[:160])

        fallback = max(1, round(haversine_km(lat1, lon1, lat2, lon2)))
        self._distance_cache[cache_key] = fallback
        return fallback

    async def resolve_route(self, origin_city: str, destination_city: str) -> RouteGeo | None:
        origin = await self.get_city_data(origin_city)
        destination = await self.get_city_data(destination_city)
        if not origin or not destination:
            return None

        distance_km = await self.get_real_distance(origin.lat, origin.lon, destination.lat, destination.lon)
        if distance_km is None:
            return None

        return RouteGeo(
            origin=origin,
            destination=destination,
            distance_km=distance_km,
        )

    async def suggest_cities(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if len(q) < 2:
            return []

        key = _normalize_city_key(q)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        for city_key in CITY_COORDS:
            if not city_key.startswith(key):
                continue
            lat, lon = CITY_COORDS[city_key]
            pretty = city_key.title()
            results.append({
                "name": pretty,
                "full_name": pretty,
                "lat": float(lat),
                "lon": float(lon),
                "source": "local",
            })
            seen.add(_normalize_city_key(pretty))
            if len(results) >= limit:
                return results

        params = {
            "q": q,
            "format": "json",
            "limit": max(1, min(limit, 10)),
            "addressdetails": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.geo_url, params=params, headers=self.headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("geo suggest failed query=%s error=%s", q, str(exc)[:160])
            return results

        if not isinstance(payload, list):
            return results

        for row in payload:
            try:
                full_name = str(row.get("display_name") or "").strip()
                if not full_name:
                    continue
                name = full_name.split(",")[0].strip()
                normalized = _normalize_city_key(name)
                if not normalized or normalized in seen:
                    continue
                results.append({
                    "name": name,
                    "full_name": full_name,
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "source": "nominatim",
                })
                seen.add(normalized)
                if len(results) >= limit:
                    break
            except Exception:
                continue

        return results


_geo_service: GeoService | None = None


def get_geo_service() -> GeoService:
    global _geo_service
    if _geo_service is None:
        _geo_service = GeoService()
    return _geo_service
