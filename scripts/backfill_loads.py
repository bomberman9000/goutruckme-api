from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.database import SessionLocal, init_db
from app.models.models import City, Load
from app.services.geo import canonicalize_city_name, is_city_like_name, normalize_city_name
from app.services.route_calc import CITY_COORDS_FALLBACK, CityPoint, calculate_route_distance_km


def _find_city(db, raw_name: str | None) -> City | None:
    if not is_city_like_name(raw_name):
        return None
    norm = normalize_city_name(canonicalize_city_name(raw_name))
    if not norm:
        return None
    return db.query(City).filter(City.name_norm == norm).first()


def _city_point(city: City | None) -> CityPoint | None:
    if city is None:
        return None
    lat = float(city.lat) if city.lat is not None else None
    lon = float(city.lon) if city.lon is not None else None
    if lat is None or lon is None:
        fallback = CITY_COORDS_FALLBACK.get(normalize_city_name(city.name))
        if fallback is None:
            return None
        lat = float(fallback[0])
        lon = float(fallback[1])
        city.lat = lat
        city.lon = lon
    return CityPoint(
        city_id=int(city.id),
        city_name=city.name,
        lat=lat,
        lon=lon,
        source="cities",
    )


def backfill_loads(*, apply_changes: bool, limit: int | None) -> dict[str, int]:
    init_db()
    db = SessionLocal()
    try:
        query = db.query(Load).order_by(Load.id.asc())
        if limit:
            query = query.limit(int(limit))
        loads = query.all()

        stats = {
            "scanned": 0,
            "updated": 0,
            "city_names": 0,
            "city_refs": 0,
            "coordinates": 0,
            "distance": 0,
            "pricing": 0,
        }

        for load in loads:
            stats["scanned"] += 1
            changed = False

            raw_from = load.from_city
            raw_to = load.to_city
            canon_from = canonicalize_city_name(raw_from)
            canon_to = canonicalize_city_name(raw_to)

            if canon_from and canon_from != load.from_city:
                load.from_city = canon_from
                stats["city_names"] += 1
                changed = True
            if canon_to and canon_to != load.to_city:
                load.to_city = canon_to
                stats["city_names"] += 1
                changed = True

            from_city = _find_city(db, load.from_city)
            to_city = _find_city(db, load.to_city)

            if from_city and load.from_city_id != from_city.id:
                load.from_city_id = int(from_city.id)
                stats["city_refs"] += 1
                changed = True
            if to_city and load.to_city_id != to_city.id:
                load.to_city_id = int(to_city.id)
                stats["city_refs"] += 1
                changed = True

            if from_city and from_city.lat is not None and from_city.lon is not None:
                if load.pickup_lat != float(from_city.lat) or load.pickup_lon != float(from_city.lon):
                    load.pickup_lat = float(from_city.lat)
                    load.pickup_lon = float(from_city.lon)
                    stats["coordinates"] += 1
                    changed = True
            if to_city and to_city.lat is not None and to_city.lon is not None:
                if load.delivery_lat != float(to_city.lat) or load.delivery_lon != float(to_city.lon):
                    load.delivery_lat = float(to_city.lat)
                    load.delivery_lon = float(to_city.lon)
                    stats["coordinates"] += 1
                    changed = True

            from_point = _city_point(from_city)
            to_point = _city_point(to_city)
            if from_point and to_point:
                recalculated_distance = float(calculate_route_distance_km(from_point=from_point, to_point=to_point))
                should_update_distance = (
                    load.distance_km is None
                    or float(load.distance_km) <= 0
                    or abs(float(load.distance_km) - 500.0) < 0.001
                    or changed
                )
                if should_update_distance and float(load.distance_km or 0) != recalculated_distance:
                    load.distance_km = recalculated_distance
                    stats["distance"] += 1
                    changed = True

            effective_total = float(load.total_price) if load.total_price is not None else float(load.price or 0)
            if load.total_price is None and effective_total > 0:
                load.total_price = effective_total
                stats["pricing"] += 1
                changed = True

            if load.distance_km and float(load.distance_km) > 0 and effective_total > 0:
                recalculated_rate = round(effective_total / float(load.distance_km), 1)
                if load.rate_per_km != recalculated_rate:
                    load.rate_per_km = recalculated_rate
                    stats["pricing"] += 1
                    changed = True

            if changed:
                stats["updated"] += 1
                db.add(load)

        if apply_changes:
            db.commit()
        else:
            db.rollback()

        return stats
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill canonical cities and route metrics in loads")
    parser.add_argument("--apply", action="store_true", help="Persist changes. Without this flag, dry run only.")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N loads")
    args = parser.parse_args()

    stats = backfill_loads(apply_changes=bool(args.apply), limit=args.limit)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[{mode}] scanned={stats['scanned']} updated={stats['updated']} "
        f"city_names={stats['city_names']} city_refs={stats['city_refs']} "
        f"coords={stats['coordinates']} distance={stats['distance']} pricing={stats['pricing']}"
    )


if __name__ == "__main__":
    main()
