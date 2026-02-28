import { useCallback, useEffect, useMemo, useRef } from "react";
import { Map, Placemark, YMaps, ZoomControl } from "@pbe/react-yandex-maps";

import type { FeedItem, VehicleMapItem } from "./api";

const RUSSIA_CENTER: [number, number] = [55.75, 49.0];
const DEFAULT_ZOOM = 4;

type CargoMapProps = {
  items: FeedItem[];
  vehicles: VehicleMapItem[];
  onSelect: (id: number) => void;
  selectedId: number | null;
  onSelectVehicle: (id: number) => void;
  selectedVehicleId: number | null;
};

export function CargoMap({
  items,
  vehicles,
  onSelect,
  selectedId,
  onSelectVehicle,
  selectedVehicleId,
}: CargoMapProps) {
  const shellRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const routeRef = useRef<any>(null);

  const selectedCargo = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );
  const selectedCargoPoint = useMemo(() => {
    if (
      selectedCargo?.from_lat == null
      || selectedCargo?.from_lon == null
    ) {
      return null;
    }
    return [selectedCargo.from_lat, selectedCargo.from_lon] as [number, number];
  }, [selectedCargo]);

  const selectedVehicle = useMemo(
    () => vehicles.find((vehicle) => vehicle.id === selectedVehicleId) ?? null,
    [vehicles, selectedVehicleId],
  );

  const buildRouteToCargo = useCallback(async (vehicleId: number) => {
    const ymapsApi = (window as any)?.ymaps;
    if (!ymapsApi || !mapRef.current || !selectedCargoPoint) {
      return;
    }
    const vehicle = vehicles.find((row) => row.id === vehicleId);
    if (!vehicle) {
      return;
    }
    try {
      if (routeRef.current) {
        mapRef.current.geoObjects.remove(routeRef.current);
        routeRef.current = null;
      }
      const route = await ymapsApi.route(
        [
          [vehicle.lat, vehicle.lon],
          selectedCargoPoint,
        ],
        { mapStateAutoApply: true },
      );
      route.getPaths?.().options?.set({
        strokeColor: "#60a5fa",
        opacity: 0.85,
        strokeWidth: 4,
      });
      route.getWayPoints?.().options?.set("visible", false);
      route.getViaPoints?.().options?.set("visible", false);
      mapRef.current.geoObjects.add(route);
      routeRef.current = route;
      onSelectVehicle(vehicleId);
    } catch {
      // keep the map usable even if the Yandex routing API fails
    }
  }, [onSelectVehicle, selectedCargoPoint, vehicles]);

  useEffect(() => {
    const node = shellRef.current;
    if (!node) {
      return;
    }

    const onClick = (event: Event) => {
      const target = (event.target as HTMLElement | null)?.closest<HTMLButtonElement>(".ymap-route-btn");
      if (!target) {
        return;
      }
      const value = Number(target.dataset.vehicleId);
      if (!Number.isFinite(value)) {
        return;
      }
      event.preventDefault();
      void buildRouteToCargo(value);
    };

    node.addEventListener("click", onClick);
    return () => node.removeEventListener("click", onClick);
  }, [buildRouteToCargo]);

  useEffect(() => {
    if (!mapRef.current) {
      return;
    }
    const center =
      selectedCargoPoint
      ?? (selectedVehicle ? [selectedVehicle.lat, selectedVehicle.lon] as [number, number] : null)
      ?? (vehicles[0] ? [vehicles[0].lat, vehicles[0].lon] as [number, number] : null)
      ?? RUSSIA_CENTER;
    mapRef.current.setCenter(center, selectedCargoPoint || selectedVehicle ? 8 : DEFAULT_ZOOM, {
      duration: 200,
    });
  }, [selectedCargoPoint, selectedVehicle, vehicles]);

  return (
    <div ref={shellRef} className="cargo-map-shell">
      <YMaps query={{ lang: "ru_RU", load: "package.full" }}>
        <Map
          className="cargo-map"
          defaultState={{ center: RUSSIA_CENTER, zoom: DEFAULT_ZOOM }}
          options={{ suppressMapOpenBlock: true }}
          modules={["geoObject.addon.balloon"]}
          instanceRef={(value: any) => { mapRef.current = value; }}
        >
          <ZoomControl options={{ position: { right: 12, bottom: 16 } }} />

          {selectedCargoPoint && (
            <Placemark
              geometry={selectedCargoPoint}
              properties={{
                balloonContentHeader: "📦 Точка погрузки",
                balloonContentBody: `
                  <div class="ymap-balloon">
                    <strong>${selectedCargo?.from_city ?? "Груз"}</strong><br/>
                    ${selectedCargo?.to_city ? `→ ${selectedCargo.to_city}<br/>` : ""}
                    ${selectedCargo?.rate_rub ? `💰 ${selectedCargo.rate_rub.toLocaleString("ru-RU")} ₽` : ""}
                  </div>
                `,
              }}
              options={{ preset: "islands#blueCircleDotIcon" }}
              onClick={() => selectedCargo && onSelect(selectedCargo.id)}
            />
          )}

          {vehicles.map((vehicle) => {
            const canBuildRoute = Boolean(selectedCargoPoint);
            const isSelected = vehicle.id === selectedVehicleId;
            return (
              <Placemark
                key={vehicle.id}
                geometry={[vehicle.lat, vehicle.lon]}
                properties={{
                  balloonContentHeader: `${vehicle.status === "available" ? "🟢" : "🔴"} ${vehicle.location_city}`,
                  balloonContentBody: `
                    <div class="ymap-balloon">
                      <strong>${vehicle.body_type}</strong> • ${vehicle.capacity_tons}т<br/>
                      ${vehicle.plate_number ? `<span class="ymap-muted">${vehicle.plate_number}</span><br/>` : ""}
                      <span class="ymap-muted">${vehicle.status === "available" ? "Свободен" : "В работе"}</span><br/>
                      ${
                        canBuildRoute
                          ? `<button type="button" class="ymap-route-btn" data-vehicle-id="${vehicle.id}">Построить маршрут</button>`
                          : `<span class="ymap-muted">Выберите груз справа, чтобы построить маршрут.</span>`
                      }
                    </div>
                  `,
                }}
                options={{
                  preset: vehicle.status === "available" ? "islands#greenCircleDotIcon" : "islands#redCircleDotIcon",
                  zIndex: isSelected ? 1200 : 800,
                }}
                onClick={() => onSelectVehicle(vehicle.id)}
              />
            );
          })}
        </Map>
      </YMaps>

      <div className="cargo-map-legend">
        <span><i className="dot free" /> Свободен</span>
        <span><i className="dot busy" /> В работе</span>
        <span><i className="dot cargo" /> Точка груза</span>
      </div>
    </div>
  );
}
