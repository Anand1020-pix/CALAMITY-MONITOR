"""
Realtime Calamities Monitor — FastAPI Backend
Polls USGS, GDACS, and NewsData.io every 5 minutes.
Serves normalized events + WebSocket Flash Alerts.
"""

import asyncio
import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Any
import httpx
import xmltodict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from geopy.geocoders import Nominatim
from geopy.distance import geodesic

app = FastAPI(title="Calamity Monitor API", version="1.0.0")


def _parse_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


FRONTEND_ORIGINS = _parse_origins(os.getenv("FRONTEND_ORIGINS", "*"))
ALLOW_ALL_ORIGINS = "*" in FRONTEND_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if ALLOW_ALL_ORIGINS else FRONTEND_ORIGINS,
    allow_credentials=not ALLOW_ALL_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-Memory Cache ──────────────────────────────────────────────────────────
cache: dict[str, Any] = {
    "events": [],
    "last_updated": None,
    "news": [],
}
CACHE_TTL = 300  # 5 minutes in seconds
_last_fetch_time = 0.0

# ─── WebSocket Connection Manager ─────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)

manager = ConnectionManager()

# ─── Data Normalization ────────────────────────────────────────────────────────
def make_id(source: str, raw_id: str) -> str:
    return hashlib.md5(f"{source}:{raw_id}".encode()).hexdigest()[:12]

def to_utc_iso(ts) -> str:
    """Accept epoch (int/float) or ISO string, return UTC ISO string."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()

def severity_from_magnitude(mag: float) -> str:
    if mag >= 7.0:
        return "red"
    if mag >= 5.5:
        return "orange"
    if mag >= 4.5:
        return "yellow"
    return "green"

# ─── USGS Fetcher ─────────────────────────────────────────────────────────────
async def fetch_usgs(client: httpx.AsyncClient) -> list[dict]:
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query"
        "?format=geojson&minmagnitude=4.5&limit=100&orderby=time"
    )
    try:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
        features = r.json().get("features", [])
        events = []
        for f in features:
            props = f["properties"]
            coords = f["geometry"]["coordinates"]
            mag = float(props.get("mag") or 0)
            events.append({
                "id": make_id("usgs", f["id"]),
                "type": "earthquake",
                "severity": severity_from_magnitude(mag),
                "lat": coords[1],
                "lng": coords[0],
                "magnitude": mag,
                "description": props.get("place", "Unknown location"),
                "news_links": [props["url"]] if props.get("url") else [],
                "timestamp": to_utc_iso(props["time"] / 1000),
                "source": "USGS",
            })
        return events
    except Exception as e:
        print(f"[USGS] fetch failed: {e}")
        return []

# ─── GDACS Fetcher ────────────────────────────────────────────────────────────
GDACS_ALERT_MAP = {"Red": "red", "Orange": "orange", "Green": "green"}
GDACS_TYPE_MAP = {"EQ": "earthquake", "TC": "cyclone", "FL": "flood", "VO": "volcano", "DR": "drought", "WF": "wildfire"}

async def fetch_gdacs(client: httpx.AsyncClient) -> list[dict]:
    url = "https://www.gdacs.org/xml/rss.xml"
    try:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
        feed = xmltodict.parse(r.text)
        items = feed.get("rss", {}).get("channel", {}).get("item", [])
        if isinstance(items, dict):
            items = [items]
        events = []
        for item in items:
            geo = item.get("geo:Point", {})
            lat = float(geo.get("geo:lat", 0) or 0)
            lng = float(geo.get("geo:long", 0) or 0)
            alert_level = item.get("gdacs:alertlevel", "Green")
            event_type = item.get("gdacs:eventtype", "")
            events.append({
                "id": make_id("gdacs", item.get("guid", {}).get("#text", str(time.time()))),
                "type": GDACS_TYPE_MAP.get(event_type, "disaster"),
                "severity": GDACS_ALERT_MAP.get(alert_level, "green"),
                "lat": lat,
                "lng": lng,
                "magnitude": float(item.get("gdacs:severity", {}).get("#text", 0) or 0),
                "description": item.get("title", "Unknown event"),
                "news_links": [item["link"]] if item.get("link") else [],
                "timestamp": to_utc_iso(item.get("pubDate", "")),
                "source": "GDACS",
                "population": item.get("gdacs:population", {}).get("#text"),
                "country": item.get("gdacs:country"),
            })
        return events
    except Exception as e:
        print(f"[GDACS] fetch failed: {e}")
        return []

# ─── NewsData.io Fetcher ───────────────────────────────────────────────────────
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

async def fetch_news(client: httpx.AsyncClient) -> list[dict]:
    if not NEWS_API_KEY:
        return []
    url = (
        f"https://newsdata.io/api/1/news"
        f"?apikey={NEWS_API_KEY}&q=earthquake+flood+cyclone+volcano&language=en&size=10"
    )
    try:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
        articles = r.json().get("results", [])
        return [
            {
                "title": a.get("title"),
                "url": a.get("link"),
                "image": a.get("image_url"),
                "source": a.get("source_id"),
                "published": to_utc_iso(a.get("pubDate", "")),
                "keywords": a.get("keywords", []),
            }
            for a in articles
        ]
    except Exception as e:
        print(f"[News] fetch failed: {e}")
        return []

# ─── Aggregator ───────────────────────────────────────────────────────────────
async def aggregate_all():
    global _last_fetch_time
    now = time.time()
    if now - _last_fetch_time < CACHE_TTL and cache["events"]:
        return  # still fresh

    async with httpx.AsyncClient() as client:
        usgs_events, gdacs_events, news = await asyncio.gather(
            fetch_usgs(client),
            fetch_gdacs(client),
            fetch_news(client),
        )

    all_events = usgs_events + gdacs_events
    all_events.sort(key=lambda e: e["timestamp"], reverse=True)

    # Find new high-severity events to flash-alert
    old_ids = {e["id"] for e in cache["events"]}
    new_critical = [
        e for e in all_events
        if e["id"] not in old_ids and e["severity"] in ("red", "orange")
    ]

    cache["events"] = all_events
    cache["news"] = news
    cache["last_updated"] = datetime.now(timezone.utc).isoformat()
    _last_fetch_time = now

    for event in new_critical:
        await manager.broadcast({"type": "flash_alert", "event": event})
        print(f"[ALERT] Flash alert sent: {event['description']}")

# ─── Background Poller ────────────────────────────────────────────────────────
async def background_poller():
    while True:
        print("[Poller] Aggregating data...")
        await aggregate_all()
        await asyncio.sleep(CACHE_TTL)

@app.on_event("startup")
async def startup():
    asyncio.create_task(background_poller())

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "events_cached": len(cache["events"]),
        "last_updated": cache["last_updated"],
        "ws_connections": len(manager.active),
    }

@app.get("/events")
async def get_events(
    severity: str | None = None,
    type: str | None = None,
    limit: int = 200,
):
    await aggregate_all()
    events = cache["events"]
    if severity:
        events = [e for e in events if e["severity"] == severity]
    if type:
        events = [e for e in events if e["type"] == type]
    return {"events": events[:limit], "total": len(events), "last_updated": cache["last_updated"]}

@app.get("/news")
async def get_news():
    await aggregate_all()
    return {"news": cache["news"]}

@app.get("/search")
async def search(q: str, radius_km: float = 500):
    """Geocode a location name and return nearby calamity events."""
    await aggregate_all()
    geolocator = Nominatim(user_agent="calamity-monitor")
    try:
        location = geolocator.geocode(q, timeout=10)
        if not location:
            return JSONResponse({"error": "Location not found"}, status_code=404)

        center = (location.latitude, location.longitude)
        nearby = []
        for e in cache["events"]:
            dist = geodesic(center, (e["lat"], e["lng"])).km
            if dist <= radius_km:
                nearby.append({**e, "distance_km": round(dist, 1)})

        nearby.sort(key=lambda x: x["distance_km"])
        return {
            "location": {"name": location.address, "lat": location.latitude, "lng": location.longitude},
            "events": nearby,
        }
    except Exception as ex:
        return JSONResponse({"error": str(ex)}, status_code=500)

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Send current state on connect
        await ws.send_json({
            "type": "initial_state",
            "events": cache["events"][:200],
            "last_updated": cache["last_updated"],
        })
        while True:
            # Keep connection alive; server pushes alerts proactively
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
