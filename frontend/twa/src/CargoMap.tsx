import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { FeedItem } from "./api";

const RUSSIA_CENTER: [number, number] = [55.75, 49.0];
const DEFAULT_ZOOM = 4;

const hotIcon = L.divIcon({
  className: "map-marker hot-marker",
  html: "🔥",
  iconSize: [24, 24],
  iconAnchor: [12, 12],
});

const normalIcon = L.divIcon({
  className: "map-marker",
  html: "📦",
  iconSize: [20, 20],
  iconAnchor: [10, 10],
});

export function CargoMap({
  items,
  onSelect,
  selectedId,
}: {
  items: FeedItem[];
  onSelect: (id: number) => void;
  selectedId: number | null;
}) {
  const mapRef = useRef<L.Map | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, {
      center: RUSSIA_CENTER,
      zoom: DEFAULT_ZOOM,
      zoomControl: false,
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OSM",
      maxZoom: 15,
    }).addTo(map);
    L.control.zoom({ position: "bottomright" }).addTo(map);
    mapRef.current = map;
    layerRef.current = L.layerGroup().addTo(map);

    return () => { map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    layer.clearLayers();

    const cityCoords: Record<string, [number, number]> = {};

    for (const item of items) {
      if (item.from_city) {
        const fromKey = item.from_city.toLowerCase();
        if (!cityCoords[fromKey]) {
          const lat = 55 + Math.random() * 10 - 5;
          const lon = 45 + Math.random() * 40 - 10;
          cityCoords[fromKey] = [lat, lon];
        }
      }
    }

    for (const item of items) {
      const fromCoord = item.from_city ? cityCoords[item.from_city.toLowerCase()] : null;
      if (!fromCoord) continue;

      const icon = item.is_hot_deal ? hotIcon : normalIcon;
      const marker = L.marker(fromCoord, { icon });

      const price = item.rate_rub ? `${item.rate_rub.toLocaleString("ru")} ₽` : "?";
      const rpk = item.rate_per_km ? ` (${item.rate_per_km} ₽/км)` : "";

      marker.bindPopup(
        `<div style="font-size:13px;min-width:180px">` +
        `<b>${item.from_city} → ${item.to_city}</b><br>` +
        `${item.body_type ?? "?"} • ${item.weight_t ?? 0}т<br>` +
        `<span style="color:#22c55e;font-weight:700">${price}${rpk}</span><br>` +
        (item.load_date ? `📅 ${item.load_date}<br>` : "") +
        (item.freshness ? `⏱ ${item.freshness}` : "") +
        `</div>`,
        { closeButton: false }
      );

      marker.on("click", () => onSelect(item.id));

      if (selectedId === item.id) {
        marker.openPopup();
      }

      marker.addTo(layer);
    }
  }, [items, selectedId, onSelect]);

  return <div ref={containerRef} className="cargo-map" />;
}
