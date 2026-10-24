from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import Response
from fastapi.concurrency import run_in_threadpool
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import math
import random
import logging
import uuid
import re
import requests
from xml.sax.saxutils import escape as xml_escape
from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Set
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("twilio.http_client").setLevel(logging.WARNING)

app = FastAPI()


@app.get("/health", include_in_schema=False)
@app.head("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


api_router = APIRouter(prefix="/api")

# ---------------------------------------------------------------- constants
GEOFENCE_M = 50
AVG_SPEED_KMH = 25.0
SERVICE_MIN = 4.0
DEPOT = {"lat": 37.7749, "lng": -122.4194, "name": "SF Central Depot"}

# ---------------------------------------------------------------- object storage
STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
STORAGE_URL = STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
APP_NAME = "multidrop-delivery"
storage_key = None


def init_storage():
    global storage_key
    if storage_key:
        return storage_key
    resp = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY}, timeout=30)
    resp.raise_for_status()
    storage_key = resp.json()["storage_key"]
    return storage_key


def put_object(path: str, data: bytes, content_type: str) -> dict:
    key = init_storage()
    resp = requests.put(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key, "Content-Type": content_type}, data=data, timeout=120)
    resp.raise_for_status()
    return resp.json()


def get_object(path: str):
    key = init_storage()
    resp = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key}, timeout=60)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


# ---------------------------------------------------------------- geo + routing
def haversine_m(a, b) -> float:
    R = 6371000.0
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat = lat2 - lat1
    dlng = math.radians(b["lng"] - a["lng"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def path_length_m(start, pts) -> float:
    total, cur = 0.0, start
    for p in pts:
        total += haversine_m(cur, p)
        cur = p
    return total


def optimize_sequence(start, stops: list) -> list:
    """Nearest-neighbour seed + 2-opt improvement on haversine distance."""
    remaining = stops[:]
    order, cur = [], start
    while remaining:
        nxt = min(remaining, key=lambda s: haversine_m(cur, s))
        order.append(nxt)
        remaining.remove(nxt)
        cur = nxt
    improved = True
    while improved and len(order) > 3:
        improved = False
        for i in range(0, len(order) - 1):
            for j in range(i + 2, len(order) + 1):
                cand = order[:i] + order[i:j][::-1] + order[j:]
                if path_length_m(start, cand) + 0.5 < path_length_m(start, order):
                    order = cand
                    improved = True
    return order


# ---------------------------------------------------------------- models
def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Driver(BaseModel):
    id: str
    name: str
    phone: str
    vehicle: str
    lat: float
    lng: float
    updated_at: Optional[str] = None


class Stop(BaseModel):
    id: str
    route_id: Optional[str] = None
    seq: int = 0
    customer_name: str
    customer_phone: str
    address: str
    postal_code: str
    zone: str
    lat: float
    lng: float
    status: str = "pending"  # pending | arrived | delivered | failed
    tracking_code: str
    notes: str = ""
    arrived_at: Optional[str] = None
    arrival_method: Optional[str] = None  # geofence | manual
    delivered_at: Optional[str] = None
    pod_photo_path: Optional[str] = None
    pod_signature: Optional[str] = None
    fail_reason: Optional[str] = None


class Route(BaseModel):
    id: str
    name: str
    zone: str
    driver_id: Optional[str] = None
    status: str = "planned"  # planned | active | completed
    planned_distance_m: float = 0
    actual_distance_m: float = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class BatchIn(BaseModel):
    zone: str
    driver_id: str


class SequenceIn(BaseModel):
    stop_ids: List[str]


class LocationIn(BaseModel):
    lat: float
    lng: float


class DeliverIn(BaseModel):
    photo_path: Optional[str] = None
    signature: Optional[str] = None


class FailIn(BaseModel):
    reason: str


class ContactIn(BaseModel):
    channel: str  # call | sms
    message: Optional[str] = None


async def get_stops(route_id: str) -> List[Stop]:
    docs = await db.stops.find({"route_id": route_id}, {"_id": 0}).sort("seq", 1).to_list(500)
    return [Stop(**d) for d in docs]


async def get_route_or_404(route_id: str) -> Route:
    doc = await db.routes.find_one({"id": route_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Route not found")
    return Route(**doc)


async def get_driver_or_404(driver_id: str) -> Driver:
    doc = await db.drivers.find_one({"id": driver_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Driver not found")
    return Driver(**doc)


async def apply_sequence(route_id: str, ordered: List[str]):
    for i, sid in enumerate(ordered):
        await db.stops.update_one({"id": sid, "route_id": route_id}, {"$set": {"seq": i + 1}})


async def route_detail(route: Route) -> dict:
    stops = await get_stops(route.id)
    driver = None
    if route.driver_id:
        d = await db.drivers.find_one({"id": route.driver_id}, {"_id": 0})
        driver = Driver(**d).model_dump() if d else None
    done = [s for s in stops if s.status in ("delivered", "failed")]
    return {
        **route.model_dump(),
        "driver": driver,
        "stops": [s.model_dump() for s in stops],
        "total": len(stops),
        "completed": len(done),
        "next_stop_id": next((s.id for s in stops if s.status in ("pending", "arrived")), None),
        "depot": DEPOT,
    }


# ---------------------------------------------------------------- websocket hub
class Hub:
    def __init__(self):
        self.rooms: Dict[str, Set[WebSocket]] = {}

    async def join(self, room: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(room, set()).add(ws)

    def leave(self, room: str, ws: WebSocket):
        self.rooms.get(room, set()).discard(ws)

    async def send(self, room: str, payload: dict):
        for ws in list(self.rooms.get(room, set())):
            try:
                await ws.send_json(payload)
            except Exception:
                self.leave(room, ws)


hub = Hub()


async def tracking_payload(code: str) -> dict:
    sdoc = await db.stops.find_one({"tracking_code": code.upper()}, {"_id": 0})
    if not sdoc:
        raise HTTPException(404, "Tracking code not found")
    stop = Stop(**sdoc)
    out = {"stop": stop.model_dump(), "route_status": None, "driver": None, "stops_away": None,
           "eta_minutes": None, "eta_time": None, "path": [], "alert": None, "depot": DEPOT}
    if not stop.route_id:
        out["alert"] = "Order received — awaiting dispatch"
        return out
    route = Route(**(await db.routes.find_one({"id": stop.route_id}, {"_id": 0})))
    out["route_status"] = route.status
    driver = None
    if route.driver_id:
        d = await db.drivers.find_one({"id": route.driver_id}, {"_id": 0})
        driver = Driver(**d) if d else None
        out["driver"] = {"name": driver.name, "vehicle": driver.vehicle, "lat": driver.lat, "lng": driver.lng} if driver else None
    if stop.status == "delivered":
        out["alert"] = "Delivered"
        return out
    if stop.status == "arrived":
        out["alert"] = "Driver has arrived"
        out["stops_away"] = 0
        out["eta_minutes"] = 0
        return out
    stops = await get_stops(route.id)
    open_before = [s for s in stops if s.seq < stop.seq and s.status in ("pending", "arrived")]
    out["stops_away"] = len(open_before)
    start = {"lat": driver.lat, "lng": driver.lng} if driver else DEPOT
    pts = [{"lat": s.lat, "lng": s.lng} for s in open_before] + [{"lat": stop.lat, "lng": stop.lng}]
    dist = path_length_m(start, pts)
    eta = dist / 1000 / AVG_SPEED_KMH * 60 + SERVICE_MIN * len(open_before)
    out["eta_minutes"] = round(eta)
    out["eta_time"] = (datetime.now(timezone.utc) + timedelta(minutes=eta)).isoformat()
    out["path"] = [start] + pts
    if route.status != "active":
        out["alert"] = "Route planned — driver not yet on the road"
    elif len(open_before) == 0:
        out["alert"] = "You're next! Driver is heading to you"
    elif len(open_before) <= 2:
        out["alert"] = f"Driver is {len(open_before)} stop{'s' if len(open_before) > 1 else ''} away"
    return out


async def broadcast_route(route_id: str):
    route = Route(**(await db.routes.find_one({"id": route_id}, {"_id": 0})))
    stops = await get_stops(route_id)
    for s in stops:
        if hub.rooms.get(f"track:{s.tracking_code}"):
            await hub.send(f"track:{s.tracking_code}", await tracking_payload(s.tracking_code))
    if hub.rooms.get(f"route:{route_id}"):
        await hub.send(f"route:{route_id}", await route_detail(route))


async def maybe_complete_route(route_id: str):
    stops = await get_stops(route_id)
    if stops and all(s.status in ("delivered", "failed") for s in stops):
        await db.routes.update_one({"id": route_id}, {"$set": {"status": "completed", "completed_at": now_iso()}})


# ---------------------------------------------------------------- seed
ZONES = {
    "Mission": {"postal": "94110", "center": (37.7599, -122.4148), "streets": ["Valencia St", "Mission St", "Guerrero St", "24th St", "Folsom St"]},
    "SoMa": {"postal": "94103", "center": (37.7785, -122.4056), "streets": ["Howard St", "Folsom St", "Harrison St", "7th St", "Brannan St"]},
    "Sunset": {"postal": "94122", "center": (37.7590, -122.4850), "streets": ["Irving St", "Judah St", "19th Ave", "Noriega St", "Lincoln Way"]},
    "Marina": {"postal": "94123", "center": (37.8030, -122.4370), "streets": ["Chestnut St", "Lombard St", "Union St", "Fillmore St", "Bay St"]},
}
NAMES = ["Ava Chen", "Liam Patel", "Noah Garcia", "Mia Johnson", "Ethan Kim", "Zoe Martinez", "Lucas Brown", "Ivy Nguyen",
         "Omar Haddad", "Sara Lopez", "Ben Carter", "Nina Rossi", "Leo Park", "Emma Wilson", "Kai Tanaka", "Ruby Singh",
         "Jack Moore", "Lila Ahmed", "Owen Diaz", "Chloe Evans", "Max Fischer", "Aria Shah", "Theo Clark", "Maya Reed"]


async def seed(force: bool = False):
    if not force and await db.drivers.count_documents({}) > 0:
        return
    await db.drivers.delete_many({})
    await db.stops.delete_many({})
    await db.routes.delete_many({})
    rnd = random.Random(42)
    drivers = [
        Driver(id="drv-1", name="Marcus Reid", phone="+14155550101", vehicle="VAN-07", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso()),
        Driver(id="drv-2", name="Priya Nair", phone="+14155550102", vehicle="VAN-12", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso()),
        Driver(id="drv-3", name="Diego Santos", phone="+14155550103", vehicle="BIKE-03", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso()),
    ]
    await db.drivers.insert_many([d.model_dump() for d in drivers])
    stops, n = [], 0
    for zone, z in ZONES.items():
        for _ in range(6):
            lat = z["center"][0] + rnd.uniform(-0.007, 0.007)
            lng = z["center"][1] + rnd.uniform(-0.008, 0.008)
            stops.append(Stop(id=str(uuid.uuid4()), customer_name=NAMES[n % len(NAMES)], customer_phone=f"+1415555{1000 + n}",
                              address=f"{rnd.randint(100, 2900)} {rnd.choice(z['streets'])}, San Francisco", postal_code=z["postal"],
                              zone=zone, lat=lat, lng=lng, tracking_code=f"MD{1001 + n}",
                              notes=rnd.choice(["", "Leave at door", "Ring bell twice", "Fragile", "Call on arrival"])))
            n += 1
    await db.stops.insert_many([s.model_dump() for s in stops])
    # one route pre-batched & started for the demo
    await create_batch("Mission", "drv-1", start=True)


async def create_batch(zone: str, driver_id: str, start: bool = False) -> Route:
    docs = await db.stops.find({"zone": zone, "route_id": None}, {"_id": 0}).to_list(500)
    if not docs:
        raise HTTPException(400, "No unassigned orders in this zone")
    stops = [Stop(**d) for d in docs]
    ordered = optimize_sequence(DEPOT, [s.model_dump() for s in stops])
    count = await db.routes.count_documents({})
    route = Route(id=str(uuid.uuid4()), name=f"R-{101 + count} {zone}", zone=zone, driver_id=driver_id,
                  planned_distance_m=round(path_length_m(DEPOT, ordered)))
    if start:
        route.status = "active"
        route.started_at = now_iso()
    await db.routes.insert_one(route.model_dump())
    for i, s in enumerate(ordered):
        await db.stops.update_one({"id": s["id"]}, {"$set": {"route_id": route.id, "seq": i + 1}})
    return route


@app.on_event("startup")
async def startup():
    await seed()
    try:
        await run_in_threadpool(init_storage)
    except Exception as e:
        logger.error(f"Storage init failed: {e}")


# ---------------------------------------------------------------- endpoints
@api_router.get("/")
async def root():
    return {"message": "MultiDrop API"}


@api_router.post("/admin/reset")
async def reset():
    await seed(force=True)
    return {"ok": True}


@api_router.get("/drivers")
async def list_drivers():
    docs = await db.drivers.find({}, {"_id": 0}).to_list(100)
    out = []
    for d in docs:
        r = await db.routes.find_one({"driver_id": d["id"], "status": {"$in": ["active", "planned"]}}, {"_id": 0, "id": 1, "name": 1, "status": 1})
        out.append({**Driver(**d).model_dump(), "route": r})
    return out


@api_router.get("/drivers/{driver_id}/route")
async def driver_route(driver_id: str):
    await get_driver_or_404(driver_id)
    doc = await db.routes.find_one({"driver_id": driver_id, "status": "active"}, {"_id": 0}) or \
        await db.routes.find_one({"driver_id": driver_id, "status": "planned"}, {"_id": 0})
    if not doc:
        return {"route": None}
    return {"route": await route_detail(Route(**doc))}


async def update_location(driver_id: str, lat: float, lng: float) -> dict:
    driver = await get_driver_or_404(driver_id)
    new = {"lat": lat, "lng": lng}
    moved = haversine_m({"lat": driver.lat, "lng": driver.lng}, new)
    await db.drivers.update_one({"id": driver_id}, {"$set": {"lat": lat, "lng": lng, "updated_at": now_iso()}})
    rdoc = await db.routes.find_one({"driver_id": driver_id, "status": "active"}, {"_id": 0})
    geofenced = None
    if rdoc:
        if moved < 5000:
            await db.routes.update_one({"id": rdoc["id"]}, {"$inc": {"actual_distance_m": moved}})
        stops = await get_stops(rdoc["id"])
        nxt = next((s for s in stops if s.status in ("pending", "arrived")), None)
        if nxt and nxt.status == "pending" and haversine_m(new, {"lat": nxt.lat, "lng": nxt.lng}) <= GEOFENCE_M:
            await db.stops.update_one({"id": nxt.id}, {"$set": {"status": "arrived", "arrived_at": now_iso(), "arrival_method": "geofence"}})
            geofenced = nxt.id
        await broadcast_route(rdoc["id"])
    return {"ok": True, "geofence_arrived_stop_id": geofenced}


@api_router.post("/drivers/{driver_id}/location")
async def post_location(driver_id: str, body: LocationIn):
    return await update_location(driver_id, body.lat, body.lng)


@api_router.post("/drivers/{driver_id}/simulate-step")
async def simulate_step(driver_id: str, meters: float = 180):
    """Demo helper: move the driver toward the next open stop (GPS stand-in for web preview)."""
    driver = await get_driver_or_404(driver_id)
    rdoc = await db.routes.find_one({"driver_id": driver_id, "status": "active"}, {"_id": 0})
    if not rdoc:
        raise HTTPException(400, "No active route")
    stops = await get_stops(rdoc["id"])
    nxt = next((s for s in stops if s.status in ("pending", "arrived")), None)
    if not nxt or nxt.status == "arrived":
        return {"ok": True, "waiting": True, "geofence_arrived_stop_id": None}
    cur = {"lat": driver.lat, "lng": driver.lng}
    target = {"lat": nxt.lat, "lng": nxt.lng}
    d = haversine_m(cur, target)
    if d <= meters:
        lat, lng = target["lat"] + 0.0001, target["lng"]
    else:
        f = meters / d
        lat, lng = cur["lat"] + (target["lat"] - cur["lat"]) * f, cur["lng"] + (target["lng"] - cur["lng"]) * f
    res = await update_location(driver_id, lat, lng)
    return {**res, "waiting": False}


@api_router.get("/zones")
async def zones():
    docs = await db.stops.find({"route_id": None}, {"_id": 0}).to_list(1000)
    groups: Dict[str, dict] = {}
    for d in docs:
        g = groups.setdefault(d["zone"], {"zone": d["zone"], "postal_code": d["postal_code"], "count": 0, "orders": []})
        g["count"] += 1
        g["orders"].append(Stop(**d).model_dump())
    return sorted(groups.values(), key=lambda g: -g["count"])


@api_router.post("/routes/batch")
async def batch(body: BatchIn):
    await get_driver_or_404(body.driver_id)
    route = await create_batch(body.zone, body.driver_id)
    return await route_detail(route)


@api_router.get("/routes")
async def list_routes():
    docs = await db.routes.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)
    out = []
    for d in docs:
        det = await route_detail(Route(**d))
        det.pop("stops")
        out.append(det)
    return out


@api_router.get("/routes/{route_id}")
async def get_route(route_id: str):
    return await route_detail(await get_route_or_404(route_id))


@api_router.post("/routes/{route_id}/optimize")
async def optimize(route_id: str):
    route = await get_route_or_404(route_id)
    stops = await get_stops(route_id)
    done = [s for s in stops if s.status in ("delivered", "failed")]
    open_ = [s for s in stops if s.status not in ("delivered", "failed")]
    start = DEPOT
    if route.status == "active" and route.driver_id:
        d = await get_driver_or_404(route.driver_id)
        start = {"lat": d.lat, "lng": d.lng}
    ordered = optimize_sequence(start, [s.model_dump() for s in open_])
    await apply_sequence(route_id, [s.id for s in done] + [s["id"] for s in ordered])
    if route.status == "planned":
        await db.routes.update_one({"id": route_id}, {"$set": {"planned_distance_m": round(path_length_m(DEPOT, ordered))}})
    await broadcast_route(route_id)
    return await route_detail(await get_route_or_404(route_id))


@api_router.put("/routes/{route_id}/sequence")
async def resequence(route_id: str, body: SequenceIn):
    route = await get_route_or_404(route_id)
    stops = await get_stops(route_id)
    if set(body.stop_ids) != {s.id for s in stops}:
        raise HTTPException(400, "stop_ids must contain every stop of the route exactly once")
    await apply_sequence(route_id, body.stop_ids)
    if route.status == "planned":
        by_id = {s.id: s.model_dump() for s in stops}
        await db.routes.update_one({"id": route_id}, {"$set": {"planned_distance_m": round(path_length_m(DEPOT, [by_id[i] for i in body.stop_ids]))}})
    await broadcast_route(route_id)
    return await route_detail(await get_route_or_404(route_id))


@api_router.post("/routes/{route_id}/start")
async def start_route(route_id: str):
    route = await get_route_or_404(route_id)
    if route.status != "planned":
        raise HTTPException(400, "Route already started")
    if route.driver_id and await db.routes.find_one({"driver_id": route.driver_id, "status": "active"}):
        raise HTTPException(400, "Driver already has an active route")
    await db.routes.update_one({"id": route_id}, {"$set": {"status": "active", "started_at": now_iso(), "actual_distance_m": 0}})
    if route.driver_id:
        await db.drivers.update_one({"id": route.driver_id}, {"$set": {"lat": DEPOT["lat"], "lng": DEPOT["lng"]}})
    await broadcast_route(route_id)
    return await route_detail(await get_route_or_404(route_id))


async def get_stop_or_404(stop_id: str) -> Stop:
    doc = await db.stops.find_one({"id": stop_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Stop not found")
    return Stop(**doc)


@api_router.get("/stops/{stop_id}")
async def get_stop(stop_id: str):
    return (await get_stop_or_404(stop_id)).model_dump()


@api_router.post("/stops/{stop_id}/arrive")
async def arrive(stop_id: str):
    stop = await get_stop_or_404(stop_id)
    if stop.status != "pending":
        raise HTTPException(400, f"Stop is {stop.status}")
    await db.stops.update_one({"id": stop_id}, {"$set": {"status": "arrived", "arrived_at": now_iso(), "arrival_method": "manual"}})
    if stop.route_id:
        await broadcast_route(stop.route_id)
    return (await get_stop_or_404(stop_id)).model_dump()


@api_router.post("/stops/{stop_id}/deliver")
async def deliver(stop_id: str, body: DeliverIn):
    stop = await get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    if not body.photo_path and not body.signature:
        raise HTTPException(400, "Proof of delivery requires a photo or signature")
    upd = {"status": "delivered", "delivered_at": now_iso(), "pod_photo_path": body.photo_path, "pod_signature": body.signature}
    if not stop.arrived_at:
        upd.update({"arrived_at": now_iso(), "arrival_method": "manual"})
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if stop.route_id:
        await maybe_complete_route(stop.route_id)
        await broadcast_route(stop.route_id)
    return (await get_stop_or_404(stop_id)).model_dump()


@api_router.post("/stops/{stop_id}/fail")
async def fail(stop_id: str, body: FailIn):
    stop = await get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    await db.stops.update_one({"id": stop_id}, {"$set": {"status": "failed", "fail_reason": body.reason, "delivered_at": now_iso()}})
    if stop.route_id:
        await maybe_complete_route(stop.route_id)
        await broadcast_route(stop.route_id)
    return (await get_stop_or_404(stop_id)).model_dump()


def twilio_client() -> TwilioClient:
    sid, token = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
    if not sid or not token or not os.environ.get("TWILIO_PHONE_NUMBER"):
        raise HTTPException(503, "Twilio is not configured")
    return TwilioClient(sid, token)


E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def twilio_number() -> str:
    raw = os.environ["TWILIO_PHONE_NUMBER"].strip()
    digits = re.sub(r"\D", "", raw)
    return "+" + (digits if raw.startswith("+") or len(digits) > 10 else "1" + digits)


@api_router.post("/stops/{stop_id}/contact")
async def contact(stop_id: str, body: ContactIn):
    """Masked communication via Twilio: both parties only ever see the Twilio number.
    call -> Twilio rings the driver, then bridges to the customer with the Twilio number as caller ID.
    sms  -> SMS sent to the customer from the Twilio number."""
    if body.channel not in ("call", "sms"):
        raise HTTPException(400, "channel must be call or sms")
    stop = await get_stop_or_404(stop_id)
    if not E164.match(stop.customer_phone):
        raise HTTPException(400, "Customer phone must be in +countrycode format")
    tw = twilio_client()
    proxy = twilio_number()
    driver = None
    if stop.route_id:
        r = await db.routes.find_one({"id": stop.route_id}, {"_id": 0})
        if r and r.get("driver_id"):
            driver = await get_driver_or_404(r["driver_id"])
    try:
        if body.channel == "call":
            if not driver or not E164.match(driver.phone):
                raise HTTPException(400, "Driver phone must be set in +countrycode format")
            twiml = (f"<Response><Say>Connecting you to {xml_escape(stop.customer_name)}.</Say>"
                     f"<Dial callerId=\"{proxy}\">{stop.customer_phone}</Dial></Response>")
            res = await run_in_threadpool(lambda: tw.calls.create(to=driver.phone, from_=proxy, twiml=twiml))
        else:
            text = (body.message or "").strip() or f"MultiDrop: your driver {driver.name if driver else ''} is on the way with order {stop.tracking_code}."
            res = await run_in_threadpool(lambda: tw.messages.create(to=stop.customer_phone, from_=proxy, body=text[:600]))
    except TwilioRestException as e:
        logger.error(f"Twilio error: {e}")
        raise HTTPException(502, f"Twilio: {e.msg}")
    log = {"id": str(uuid.uuid4()), "stop_id": stop_id, "channel": body.channel, "message": body.message,
           "proxy_number": proxy, "twilio_sid": res.sid, "status": res.status, "created_at": now_iso()}
    await db.contact_logs.insert_one({**log})
    log.pop("_id", None)
    return log


class PhoneIn(BaseModel):
    phone: str


@api_router.put("/stops/{stop_id}/phone")
async def set_customer_phone(stop_id: str, body: PhoneIn):
    phone = re.sub(r"[\s\-()]", "", body.phone)
    if not E164.match(phone):
        raise HTTPException(400, "Use +countrycode format, e.g. +14155550123")
    await get_stop_or_404(stop_id)
    await db.stops.update_one({"id": stop_id}, {"$set": {"customer_phone": phone}})
    return (await get_stop_or_404(stop_id)).model_dump()


@api_router.put("/drivers/{driver_id}/phone")
async def set_driver_phone(driver_id: str, body: PhoneIn):
    phone = re.sub(r"[\s\-()]", "", body.phone)
    if not E164.match(phone):
        raise HTTPException(400, "Use +countrycode format, e.g. +14155550123")
    await get_driver_or_404(driver_id)
    await db.drivers.update_one({"id": driver_id}, {"$set": {"phone": phone}})
    return (await get_driver_or_404(driver_id)).model_dump()


@api_router.post("/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "File too large (10MB max)")
    ext = (file.filename or "photo.jpg").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "jpg"
    path = f"{APP_NAME}/uploads/pod/{uuid.uuid4()}.{ext}"
    try:
        res = await run_in_threadpool(put_object, path, data, file.content_type or "image/jpeg")
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 500
        raise HTTPException(402 if code == 402 else 502, "Storage out of credits" if code == 402 else "Upload failed")
    await db.files.insert_one({"id": str(uuid.uuid4()), "storage_path": res["path"], "original_filename": file.filename,
                               "content_type": file.content_type, "size": res.get("size"), "created_at": now_iso()})
    return {"path": res["path"]}


@api_router.get("/files/{path:path}")
async def download(path: str):
    rec = await db.files.find_one({"storage_path": path}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "File not found")
    data, ct = await run_in_threadpool(get_object, path)
    return Response(content=data, media_type=rec.get("content_type") or ct, headers={"Cache-Control": "max-age=86400"})


@api_router.get("/track/{code}")
async def track(code: str):
    return await tracking_payload(code)


@api_router.get("/track-codes")
async def track_codes():
    """Demo helper listing tracking codes on active/planned routes."""
    docs = await db.stops.find({"route_id": {"$ne": None}}, {"_id": 0}).sort("seq", 1).to_list(200)
    return [{"tracking_code": d["tracking_code"], "customer_name": d["customer_name"], "status": d["status"], "seq": d["seq"]} for d in docs]


@api_router.get("/analytics")
async def analytics():
    routes = [Route(**d) for d in await db.routes.find({}, {"_id": 0}).to_list(500)]
    delivered = await db.stops.count_documents({"status": "delivered"})
    failed = await db.stops.count_documents({"status": "failed"})
    total = await db.stops.count_documents({})
    unassigned = await db.stops.count_documents({"route_id": None})
    geofenced = await db.stops.count_documents({"arrival_method": "geofence"})
    arrivals = await db.stops.count_documents({"arrived_at": {"$ne": None}})
    hours, drops, devs, per_route = 0.0, 0, [], []
    for r in routes:
        if not r.started_at:
            continue
        end = datetime.fromisoformat(r.completed_at) if r.completed_at else datetime.now(timezone.utc)
        h = max((end - datetime.fromisoformat(r.started_at)).total_seconds() / 3600, 1 / 60)
        rd = await db.stops.count_documents({"route_id": r.id, "status": "delivered"})
        hours += h
        drops += rd
        dev = None
        # compare driven distance with the planned distance for the portion of the route reached so far
        reached = [s.model_dump() for s in await get_stops(r.id) if s.status != "pending"]
        planned_so_far = path_length_m(DEPOT, reached) if reached else 0
        if planned_so_far > 0 and r.actual_distance_m > 0:
            dev = round((r.actual_distance_m - planned_so_far) / planned_so_far * 100, 1)
            devs.append(abs(dev))
        per_route.append({"id": r.id, "name": r.name, "status": r.status, "drops": rd, "drops_per_hour": round(rd / h, 1),
                          "deviation_pct": dev, "planned_km": round(r.planned_distance_m / 1000, 2), "actual_km": round(r.actual_distance_m / 1000, 2)})
    return {
        "drops_per_hour": round(drops / hours, 1) if hours else 0,
        "avg_deviation_pct": round(sum(devs) / len(devs), 1) if devs else 0,
        "delivered": delivered, "failed": failed, "total_orders": total, "unassigned": unassigned,
        "success_rate": round(delivered / (delivered + failed) * 100, 1) if delivered + failed else 100,
        "geofence_arrival_pct": round(geofenced / arrivals * 100) if arrivals else 0,
        "active_routes": sum(1 for r in routes if r.status == "active"),
        "routes": per_route,
    }


@api_router.websocket("/ws/track/{code}")
async def ws_track(ws: WebSocket, code: str):
    room = f"track:{code.upper()}"
    await hub.join(room, ws)
    try:
        await ws.send_json(await tracking_payload(code))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, HTTPException):
        pass
    finally:
        hub.leave(room, ws)


@api_router.websocket("/ws/route/{route_id}")
async def ws_route(ws: WebSocket, route_id: str):
    room = f"route:{route_id}"
    await hub.join(room, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(room, ws)


app.include_router(api_router)

app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
