"""
src/api/live_tracking.py
Public tracking page + JSON API for live location.
"""
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from src.core.redis import get_redis

router = APIRouter(tags=["tracking"])

TRACK_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GoTruck — Отслеживание груза</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  body { margin: 0; font-family: sans-serif; background: #1a1a2e; color: #eee; }
  #header { padding: 12px 16px; background: #16213e; display: flex; align-items: center; gap: 12px; }
  #header h2 { margin: 0; font-size: 16px; }
  #status { font-size: 13px; color: #a0aec0; }
  #map { height: calc(100vh - 60px); }
  #info { position: absolute; bottom: 80px; left: 10px; z-index: 1000; background: rgba(22,33,62,0.9); padding: 10px 14px; border-radius: 8px; font-size: 13px; min-width: 180px; }
</style>
</head>
<body>
<div id="header">
  <span style="font-size:24px">&#x1F69A;</span>
  <div><h2>GoTruck — Отслеживание</h2><div id="status">Загрузка...</div></div>
</div>
<div id="map"></div>
<div id="info">&#x1F4CD; Ожидание данных...</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const TOKEN = "__TOKEN__";
const map = L.map("map").setView([55.75, 37.61], 8);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap"
}).addTo(map);

const truckIcon = L.divIcon({
  html: "&#x1F69A;",
  iconSize: [32, 32],
  className: "truck-icon"
});

let marker = null;
let polyline = L.polyline([], {color: "#4299e1", weight: 3}).addTo(map);
let coords = [];
let firstLoad = true;

async function update() {
  try {
    const r = await fetch(`/api/track/${TOKEN}`);
    if (!r.ok) { document.getElementById("status").textContent = "Трекинг завершён"; return; }
    const d = await r.json();
    const lat = d.lat, lng = d.lng;
    const ts = new Date(d.ts * 1000).toLocaleTimeString("ru-RU");

    if (!marker) {
      marker = L.marker([lat, lng], {icon: truckIcon}).addTo(map);
    } else {
      marker.setLatLng([lat, lng]);
    }
    if (firstLoad) { map.setView([lat, lng], 11); firstLoad = false; }

    coords.push([lat, lng]);
    if (coords.length > 200) coords.shift();
    polyline.setLatLngs(coords);

    const speed = d.speed ? `${Math.round(d.speed * 3.6)} км/ч` : "—";
    document.getElementById("info").innerHTML =
      `&#x1F4CD; ${lat.toFixed(5)}, ${lng.toFixed(5)}<br>&#x1F550; ${ts}<br>&#x1F697; ${speed}<br>&#x1F5FA; ${d.route || ""}`;
    document.getElementById("status").textContent = `Обновлено в ${ts}`;
  } catch(e) {}
}

update();
setInterval(update, 5000);
</script>
</body>
</html>"""


@router.get("/track/{token}", response_class=HTMLResponse)
async def tracking_page(token: str):
    redis = await get_redis()
    exists = await redis.exists(f"live_loc:{token}")
    if not exists:
        raise HTTPException(status_code=404, detail="Трекинг не найден или завершён")
    return HTMLResponse(TRACK_HTML.replace("__TOKEN__", token))


@router.get("/api/track/{token}")
async def tracking_api(token: str):
    redis = await get_redis()
    raw = await redis.get(f"live_loc:{token}")
    if not raw:
        raise HTTPException(status_code=404, detail="not found")
    return json.loads(raw)

@router.get("/api/webapp/live-trucks")
async def live_trucks_api():
    """Список активных live-трекингов для PWA карты."""
    redis = await get_redis()
    keys = await redis.keys("live_loc:*")
    result = []
    for key in keys:
        raw = await redis.get(key)
        if raw:
            try:
                import json as _json
                d = _json.loads(raw)
                token = key.replace("live_loc:", "")
                result.append({
                    "token": token,
                    "lat": d.get("lat"),
                    "lng": d.get("lng"),
                    "heading": d.get("heading"),
                    "speed": d.get("speed"),
                    "ts": d.get("ts"),
                    "route": d.get("route", ""),
                    "cargo_id": d.get("cargo_id"),
                })
            except Exception:
                pass
    return {"trucks": result}
