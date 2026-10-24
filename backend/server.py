from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import Response, HTMLResponse
from fastapi.concurrency import run_in_threadpool
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
import os
import io
import csv
import time
import asyncio
import httpx
import math
import random
import logging
import uuid
import re
import requests
import secrets
import string
import qrcode
import qrcode.image.svg
from xml.sax.saxutils import escape as xml_escape
from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Set
from datetime import datetime, timezone, timedelta, date as date_cls, time as time_cls
from zoneinfo import ZoneInfo

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
TZ = ZoneInfo("America/Los_Angeles")  # depot timezone for customer delivery windows
MAX_BOOKING_DAYS = 13
MIN_PACE_HOURS = 0.5  # pace (drops/hr) never divides by less than 30 min, so a fresh route cannot spike it
OPT_BUFFER_MIN = 10  # optimizer aims to arrive at least this long before a window closes

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


def today_str() -> str:
    return datetime.now(TZ).date().isoformat()


def plan_base(service_date: Optional[str]) -> datetime:
    """Planning clock for a route: now for today's routes, 08:00 depot time for future service dates."""
    now = datetime.now(TZ)
    if service_date and service_date > now.date().isoformat():
        return datetime.combine(date_cls.fromisoformat(service_date), time_cls(8, 0), TZ)
    return now


def window_minutes(s: dict, now_local: datetime):
    """Customer time window as (start, end) minutes from the planning clock, or None.
    Windows are HH:MM in the depot timezone on window_date (today when unset)."""
    if not s.get("window_start") or not s.get("window_end"):
        return None
    day = date_cls.fromisoformat(s["window_date"]) if s.get("window_date") else now_local.date()
    def mins(hhmm):
        h, m = map(int, hhmm.split(":"))
        return (datetime.combine(day, time_cls(h, m), TZ) - now_local).total_seconds() / 60
    return mins(s["window_start"]), mins(s["window_end"])


def schedule(start, ordered: list, now_local: Optional[datetime] = None, buffer_min: float = 0.0) -> list:
    """Simulate the run: travel at AVG_SPEED_KMH, wait if early, SERVICE_MIN per drop.
    Returns per stop {arrive_min, late_min} (minutes from now)."""
    now_local = now_local or datetime.now(TZ)
    t, cur, out = 0.0, start, []
    for s in ordered:
        t += haversine_m(cur, s) / 1000 / AVG_SPEED_KMH * 60
        w = window_minutes(s, now_local)
        late = 0.0
        if w:
            if t < w[0]:
                t = w[0]
            late = max(0.0, t - (w[1] - buffer_min))
        out.append({"arrive_min": t, "late_min": late})
        t += SERVICE_MIN
        cur = s
    return out


def route_cost(start, ordered, now_local):
    """(total lateness, finish time incl. waiting, distance) — compared lexicographically."""
    sch = schedule(start, ordered, now_local, buffer_min=OPT_BUFFER_MIN)
    finish = sch[-1]["arrive_min"] if sch else 0.0
    return (round(sum(x["late_min"] for x in sch), 1), round(finish, 1), path_length_m(start, ordered))


def better(c, best) -> bool:
    if c[0] != best[0]:
        return c[0] < best[0] - 0.05
    if c[1] != best[1]:
        return c[1] < best[1] - 0.1
    return c[2] + 0.5 < best[2]


def optimize_sequence(start, stops: list, now_local: Optional[datetime] = None) -> list:
    """Time-window aware: urgency-weighted nearest-neighbour seed, then 2-opt + relocate moves
    minimising (total lateness, distance) lexicographically."""
    now_local = now_local or datetime.now(TZ)
    remaining = stops[:]
    order, cur, t = [], start, 0.0
    while remaining:
        def score(s):
            arr = t + haversine_m(cur, s) / 1000 / AVG_SPEED_KMH * 60
            w = window_minutes(s, now_local)
            if not w:
                return arr + 0.3 * 240
            wait = max(0.0, w[0] - arr)
            return arr + wait * 1.5 + 0.3 * max(0.0, w[1] - arr) + max(0.0, arr - w[1]) * 10
        nxt = min(remaining, key=score)
        t += haversine_m(cur, nxt) / 1000 / AVG_SPEED_KMH * 60
        w = window_minutes(nxt, now_local)
        if w and t < w[0]:
            t = w[0]
        t += SERVICE_MIN
        order.append(nxt)
        remaining.remove(nxt)
        cur = nxt
    best = route_cost(start, order, now_local)
    improved = True
    while improved and len(order) > 2:
        improved = False
        n = len(order)
        cands = [order[:i] + order[i:j][::-1] + order[j:] for i in range(n - 1) for j in range(i + 2, n + 1)]
        for i in range(n):
            rest = order[:i] + order[i + 1:]
            cands += [rest[:k] + [order[i]] + rest[k:] for k in range(n) if k != i]
        for cand in cands:
            c = route_cost(start, cand, now_local)
            if better(c, best):
                order, best, improved = cand, c, True
                break
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
    pod_qr_verified: bool = False
    window_start: Optional[str] = None  # "HH:MM" depot local time
    window_end: Optional[str] = None
    window_date: Optional[str] = None  # "YYYY-MM-DD" depot local; None = today / any day
    rating: Optional[int] = None  # customer rating 1-5 after delivery
    rating_comment: Optional[str] = None
    rated_at: Optional[str] = None
    follow_up_status: Optional[str] = None  # open | resolved (only for 1-2 star ratings)
    follow_up_note: Optional[str] = None
    follow_up_at: Optional[str] = None
    apology_code: Optional[str] = None  # discount code texted after a 1-2 star rating
    apology_sms_status: Optional[str] = None
    share_token: Optional[str] = None  # public proof-of-delivery link token
    share_created_at: Optional[str] = None  # links expire PROOF_LINK_DAYS after creation
    nearby_sms_status: Optional[str] = None  # "driver is 2 stops away" text: sending | sent | skipped_fictional | not_configured | failed
    nearby_sms_at: Optional[str] = None
    merchant_id: Optional[str] = None  # set when the order came from a merchant CSV import
    created_at: Optional[str] = None
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
    resequenced_at: Optional[str] = None  # last manual re-order by dispatch
    resequence_seen: bool = True  # driver acknowledged the latest re-order
    service_date: str = Field(default_factory=lambda: datetime.now(TZ).date().isoformat())
    created_at: str = Field(default_factory=now_iso)


class BatchIn(BaseModel):
    zone: str
    driver_id: str
    service_date: Optional[str] = None


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
    open_ = [s.model_dump() for s in stops if s.status not in ("delivered", "failed")]
    start = {"lat": driver["lat"], "lng": driver["lng"]} if driver and route.status == "active" else DEPOT
    base = plan_base(route.service_date)
    now = base.astimezone(timezone.utc)
    plan = {s["id"]: x for s, x in zip(open_, schedule(start, open_, base))}
    out_stops = []
    for s in stops:
        d = s.model_dump()
        p = plan.get(s.id)
        d["planned_eta"] = (now + timedelta(minutes=round(p["arrive_min"]))).isoformat() if p else None
        d["late_min"] = round(p["late_min"]) if p else 0
        out_stops.append(d)
    return {
        **route.model_dump(),
        "driver": driver,
        "stops": out_stops,
        "total": len(stops),
        "completed": len(done),
        "late_stops": sum(1 for d in out_stops if d["late_min"] > 0),
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
           "eta_minutes": None, "eta_time": None, "late_min": 0, "path": [], "alert": None, "depot": DEPOT}
    if not stop.route_id:
        out["alert"] = f"Scheduled for {nice_date(stop.window_date)}" if stop.window_date and stop.window_date > today_str() else "Order received — awaiting dispatch"
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
    seq_stops = [s.model_dump() for s in open_before] + [stop.model_dump()]
    pts = [{"lat": s["lat"], "lng": s["lng"]} for s in seq_stops]
    base = plan_base(route.service_date)
    target = schedule(start, seq_stops, base)[-1]
    eta = target["arrive_min"]
    out["eta_minutes"] = round(eta) if route.service_date <= today_str() else None
    out["eta_time"] = (base.astimezone(timezone.utc) + timedelta(minutes=round(eta))).isoformat()
    out["late_min"] = round(target["late_min"])
    out["path"] = [start] + pts
    if route.service_date > today_str():
        out["alert"] = f"Scheduled for {nice_date(route.service_date)}"
    elif route.status != "active":
        out["alert"] = "Route planned — driver not yet on the road"
    elif len(open_before) == 0:
        out["alert"] = "You're next! Driver is heading to you"
    elif len(open_before) <= 2:
        out["alert"] = f"Driver is {len(open_before)} stop{'s' if len(open_before) > 1 else ''} away"
    return out


NEARBY_STOPS = 2  # text the customer once when the driver is this many stops away (or closer)


async def send_nearby_sms(stop: Stop, stops_away: int, driver_name: str):
    status = "failed"
    try:
        status = await _send_nearby_sms(stop, stops_away, driver_name)
    finally:  # never leave a stop stuck in "sending"
        await db.stops.update_one({"id": stop.id}, {"$set": {"nearby_sms_status": status, "nearby_sms_at": now_iso()}})
    if hub.rooms.get(f"track:{stop.tracking_code}"):
        await hub.send(f"track:{stop.tracking_code}", await tracking_payload(stop.tracking_code))


_bg_tasks: Set[asyncio.Task] = set()


def spawn(coro):
    """Keep a reference to fire-and-forget tasks so they are not garbage-collected mid-run."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


async def _send_nearby_sms(stop: Stop, stops_away: int, driver_name: str) -> str:
    if FICTIONAL_NUMBER.match(stop.customer_phone):
        return "skipped_fictional"
    if not (os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_PHONE_NUMBER")):
        return "not_configured"
    where = "you're next" if stops_away == 0 else f"{stops_away} stop{'s' if stops_away > 1 else ''} away"
    body = f"MultiDrop: {driver_name.split(' ')[0]} is {where} with your delivery {stop.tracking_code}. Track live in the app."
    try:
        tw = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        res = await run_in_threadpool(lambda: tw.messages.create(to=stop.customer_phone, from_=twilio_number(), body=body))
        await db.contact_logs.insert_one({"id": str(uuid.uuid4()), "stop_id": stop.id, "channel": "sms", "kind": "nearby",
                                          "message": body, "twilio_sid": res.sid, "status": res.status, "created_at": now_iso()})
        return "sent"
    except Exception as e:
        logger.error(f"Nearby SMS failed for {stop.tracking_code}: {e}")
        return "failed"


async def maybe_nearby_sms(route: Route, stops: List[Stop]):
    """Once per stop: text the customer when the driver on an active route is NEARBY_STOPS or fewer stops away."""
    if route.status != "active" or route.service_date != today_str() or not route.driver_id:
        return
    d = await db.drivers.find_one({"id": route.driver_id}, {"_id": 0, "name": 1})
    open_ = [s for s in stops if s.status in ("pending", "arrived")]
    for i, s in enumerate(open_[:NEARBY_STOPS + 1]):
        if s.status != "pending" or s.nearby_sms_status:
            continue
        # claim atomically so concurrent location pings never double-send
        claimed = await db.stops.update_one({"id": s.id, "nearby_sms_status": None}, {"$set": {"nearby_sms_status": "sending"}})
        if claimed.modified_count:
            spawn(send_nearby_sms(s, i, d["name"] if d else "Your driver"))


async def broadcast_route(route_id: str):
    route = Route(**(await db.routes.find_one({"id": route_id}, {"_id": 0})))
    stops = await get_stops(route_id)
    await maybe_nearby_sms(route, stops)
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
    await db.code_redemptions.delete_many({})
    await db.coaching_notes.delete_many({})
    await db.import_jobs.delete_many({})
    await db.import_rows.delete_many({})
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
            stops.append(Stop(id=str(uuid.uuid4()), customer_name=NAMES[n % len(NAMES)], customer_phone=f"+1415555{100 + n:04d}",
                              address=f"{rnd.randint(100, 2900)} {rnd.choice(z['streets'])}, San Francisco", postal_code=z["postal"],
                              zone=zone, lat=lat, lng=lng, tracking_code=f"MD{1001 + n}",
                              notes=rnd.choice(["", "Leave at door", "Ring bell twice", "Fragile", "Call on arrival"])))
            n += 1
    # demo delivery windows relative to the current depot-local hour
    h = datetime.now(TZ).hour
    for idx, (a, b) in {5: (0, 1), 1: (2, 3), 8: (1, 2)}.items():
        if h + b <= 23:
            stops[idx].window_start, stops[idx].window_end = f"{h + a:02d}:00", f"{h + b:02d}:00"
            stops[idx].window_date = today_str()
    await db.stops.insert_many([s.model_dump() for s in stops])
    # one route pre-batched & started for the demo
    await create_batch("Mission", "drv-1", start=True)
    await seed_history(rnd, drivers)


async def seed_history(rnd: random.Random, drivers: List[Driver]):
    """Six past days of completed routes so the weekly scorecard has real data."""
    today = datetime.now(TZ).date()
    n = 0
    redemptions: List[dict] = []
    for back in range(6, 0, -1):
        day = today - timedelta(days=back)
        for drv in drivers:
            if rnd.random() < 0.2:
                continue  # day off
            zone = rnd.choice(list(ZONES))
            z = ZONES[zone]
            t = datetime.combine(day, time_cls(9, rnd.randint(0, 30)), TZ)
            started = t
            count = rnd.randint(5, 9)
            pts = []
            route_id = str(uuid.uuid4())
            docs = []
            for i in range(count):
                lat = z["center"][0] + rnd.uniform(-0.007, 0.007)
                lng = z["center"][1] + rnd.uniform(-0.008, 0.008)
                pts.append({"lat": lat, "lng": lng})
                t += timedelta(minutes=rnd.randint(8, 22))
                failed = rnd.random() < 0.06
                win = None
                if rnd.random() < 0.5:
                    ws = max(9, t.hour - rnd.choice([0, 1, 1, 2]))
                    win = (f"{ws:02d}:00", f"{min(ws + 2, 23):02d}:00")
                docs.append(Stop(id=str(uuid.uuid4()), route_id=route_id, seq=i + 1, customer_name=rnd.choice(NAMES),
                                 customer_phone=f"+1415555{100 + rnd.randint(0, 99):04d}", address=f"{rnd.randint(100, 2900)} {rnd.choice(z['streets'])}, San Francisco",
                                 postal_code=z["postal"], zone=zone, lat=lat, lng=lng, tracking_code=f"MH{2001 + n}",
                                 status="failed" if failed else "delivered", fail_reason="No one home" if failed else None,
                                 arrived_at=t.astimezone(timezone.utc).isoformat(), arrival_method="geofence" if rnd.random() < 0.8 else "manual",
                                 delivered_at=t.astimezone(timezone.utc).isoformat(), pod_signature=None if failed else "M10 10 L60 40",
                                 pod_qr_verified=not failed and rnd.random() < 0.5,
                                 window_date=day.isoformat() if win else None, window_start=win[0] if win else None, window_end=win[1] if win else None,
                                 rating=None if failed or rnd.random() < 0.4 else rnd.choice([1, 2] + [3] * 2 + [4] * 7 + [5] * 9),
                                 ).model_dump())
                if docs[-1]["rating"]:
                    docs[-1]["rated_at"] = t.astimezone(timezone.utc).isoformat()
                    if docs[-1]["rating"] <= 2:
                        docs[-1]["rating_comment"] = rnd.choice(["LATE", "HARD TO FIND PARCEL", "Parcel left in the rain", "Driver never rang the bell", "LATE, HARD TO FIND PARCEL"])
                        docs[-1]["follow_up_status"] = "resolved" if back > 3 else "open"
                        docs[-1]["apology_code"] = "SORRY-" + "".join(rnd.choice(string.ascii_uppercase + string.digits) for _ in range(5))
                        docs[-1]["apology_sms_status"] = "skipped_fictional"
                        if rnd.random() < 0.5:
                            value = round(rnd.uniform(18, 90), 2)
                            redemptions.append({"id": str(uuid.uuid4()), "code": docs[-1]["apology_code"], "stop_id": docs[-1]["id"],
                                                "order_value": value, "discount": round(value * APOLOGY_DISCOUNT_PCT / 100, 2), "order_ref": f"ORD-{rnd.randint(10000, 99999)}",
                                                "redeemed_at": min(t + timedelta(days=rnd.randint(1, back)), datetime.now(TZ)).astimezone(timezone.utc).isoformat()})
                n += 1
            planned = path_length_m(DEPOT, pts)
            await db.stops.insert_many(docs)
            if redemptions:
                await db.code_redemptions.insert_many(redemptions)
                redemptions = []
            await db.routes.insert_one(Route(id=route_id, name=f"H-{day.strftime('%m%d')} {zone}", zone=zone, driver_id=drv.id, status="completed",
                                             planned_distance_m=round(planned), actual_distance_m=round(planned * rnd.uniform(1.0, 1.18)),
                                             started_at=started.astimezone(timezone.utc).isoformat(), completed_at=(t + timedelta(minutes=15)).astimezone(timezone.utc).isoformat(),
                                             service_date=day.isoformat()).model_dump())


def eligible_query(service_date: str) -> dict:
    """Unassigned orders deliverable on service_date: undated ones plus those booked for that day."""
    return {"route_id": None, "status": "pending", "$or": [{"window_date": None}, {"window_date": service_date}]}


ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_service_date(d: Optional[str]) -> str:
    d = d or today_str()
    try:
        if not ISO_DATE.match(d):
            raise ValueError
        date_cls.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "Date must be YYYY-MM-DD")
    if d < today_str():
        raise HTTPException(400, "Date is in the past")
    if d > (datetime.now(TZ).date() + timedelta(days=MAX_BOOKING_DAYS)).isoformat():
        raise HTTPException(400, f"Bookings open up to {MAX_BOOKING_DAYS} days ahead")
    return d


def nice_date(d: str) -> str:
    return date_cls.fromisoformat(d).strftime("%a %d %b")


async def ensure_stop_actionable_today(stop: "Stop"):
    """Drivers can only work stops on routes scheduled for today (or earlier)."""
    if stop.route_id:
        r = await db.routes.find_one({"id": stop.route_id}, {"_id": 0, "service_date": 1})
        if r and r.get("service_date", today_str()) > today_str():
            raise HTTPException(400, f"Stop is scheduled for {nice_date(r['service_date'])}")


async def create_batch(zone: str, driver_id: str, start: bool = False, service_date: Optional[str] = None) -> Route:
    service_date = check_service_date(service_date)
    docs = await db.stops.find({"zone": zone, **eligible_query(service_date)}, {"_id": 0}).to_list(500)
    if not docs:
        raise HTTPException(400, "No unassigned orders in this zone for that day")
    stops = [Stop(**d) for d in docs]
    ordered = optimize_sequence(DEPOT, [s.model_dump() for s in stops], plan_base(service_date))
    count = await db.routes.count_documents({})
    route = Route(id=str(uuid.uuid4()), name=f"R-{101 + count} {zone}", zone=zone, driver_id=driver_id,
                  service_date=service_date, planned_distance_m=round(path_length_m(DEPOT, ordered)))
    if start:
        route.status = "active"
        route.started_at = now_iso()
    await db.routes.insert_one(route.model_dump())
    for i, s in enumerate(ordered):
        await db.stops.update_one({"id": s["id"]}, {"$set": {"route_id": route.id, "seq": i + 1}})
    return route


@app.on_event("startup")
async def startup():
    await db.code_redemptions.create_index("code", unique=True)  # a code can only ever be redeemed once
    await db.geocodes.create_index("key", unique=True)
    await db.stops.update_many({"nearby_sms_status": "sending"}, {"$set": {"nearby_sms_status": None}})  # interrupted by a restart: retry
    await seed()
    for m in MERCHANTS:
        await db.merchants.update_one({"id": m["id"]}, {"$setOnInsert": m}, upsert=True)
    # imports interrupted by a restart: send unfinished rows back to the queue
    for j in await db.import_jobs.find({"status": "running"}, {"_id": 0, "id": 1}).to_list(100):
        spawn(process_import(j["id"]))
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
    # active route first, then the soonest planned route from today on, then today's finished route
    doc = await db.routes.find_one({"driver_id": driver_id, "status": "active"}, {"_id": 0})
    if not doc:
        nxt = await db.routes.find({"driver_id": driver_id, "status": "planned", "service_date": {"$gte": today_str()}}, {"_id": 0}).sort("service_date", 1).to_list(1)
        doc = nxt[0] if nxt else None
    if not doc:
        done = await db.routes.find({"driver_id": driver_id, "status": "completed", "service_date": today_str()}, {"_id": 0}).sort("completed_at", -1).to_list(1)
        doc = done[0] if done else None
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
async def zones(date: Optional[str] = None):
    docs = await db.stops.find(eligible_query(check_service_date(date)), {"_id": 0}).to_list(1000)
    groups: Dict[str, dict] = {}
    for d in docs:
        g = groups.setdefault(d["zone"], {"zone": d["zone"], "postal_code": d["postal_code"], "count": 0, "orders": []})
        g["count"] += 1
        g["orders"].append(Stop(**d).model_dump())
    return sorted(groups.values(), key=lambda g: -g["count"])


@api_router.post("/routes/batch")
async def batch(body: BatchIn):
    await get_driver_or_404(body.driver_id)
    route = await create_batch(body.zone, body.driver_id, service_date=body.service_date)
    return await route_detail(route)


@api_router.get("/routes")
async def list_routes():
    docs = await db.routes.find({"service_date": {"$gte": today_str()}}, {"_id": 0}).sort("created_at", -1).to_list(200)
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
    ordered = optimize_sequence(start, [s.model_dump() for s in open_], plan_base(route.service_date))
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
    if route.status == "completed":
        raise HTTPException(400, "Route is already completed")
    done = [s.id for s in stops if s.status in ("delivered", "failed")]
    if body.stop_ids[:len(done)] != done:
        raise HTTPException(400, "Finished stops stay where they are — only reorder stops not done yet")
    if body.stop_ids == [s.id for s in stops]:
        return await route_detail(route)  # nothing changed
    await apply_sequence(route_id, body.stop_ids)
    upd = {"resequenced_at": now_iso(), "resequence_seen": False}
    if route.status == "planned":
        by_id = {s.id: s.model_dump() for s in stops}
        upd["planned_distance_m"] = round(path_length_m(DEPOT, [by_id[i] for i in body.stop_ids]))
    await db.routes.update_one({"id": route_id}, {"$set": upd})
    await broadcast_route(route_id)
    return await route_detail(await get_route_or_404(route_id))


@api_router.post("/routes/{route_id}/update-seen")
async def route_update_seen(route_id: str):
    """Driver acknowledged the dispatcher's latest re-order."""
    await get_route_or_404(route_id)
    await db.routes.update_one({"id": route_id}, {"$set": {"resequence_seen": True}})
    return await route_detail(await get_route_or_404(route_id))


@api_router.post("/routes/{route_id}/start")
async def start_route(route_id: str):
    route = await get_route_or_404(route_id)
    if route.status != "planned":
        raise HTTPException(400, "Route already started")
    if route.service_date > today_str():
        raise HTTPException(400, f"Route is scheduled for {nice_date(route.service_date)}")
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


class QrIn(BaseModel):
    code: str


@api_router.post("/stops/{stop_id}/verify-qr")
async def verify_qr(stop_id: str, body: QrIn):
    """Parcel label QR encodes the tracking code; scanning the right parcel at the door is proof of delivery."""
    stop = await get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    await ensure_stop_actionable_today(stop)
    scanned = body.code.strip().upper()
    if scanned.startswith("MULTIDROP:"):
        scanned = scanned.split(":", 1)[1]
    if scanned != stop.tracking_code:
        raise HTTPException(400, f"Wrong parcel — scanned {scanned[:20]}, expected {stop.tracking_code}")
    await db.stops.update_one({"id": stop_id}, {"$set": {"pod_qr_verified": True}})
    return (await get_stop_or_404(stop_id)).model_dump()


HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class WindowIn(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    date: Optional[str] = None  # YYYY-MM-DD depot local, defaults to today




FICTIONAL_NUMBER = re.compile(r"^\+1\d{3}55501\d{2}$")  # 555-0100..0199 are reserved fictional numbers


async def send_booking_sms(stop: "Stop", kind: str) -> str:
    """Text the customer when their delivery day/window is booked, changed or cleared.
    Never blocks the booking: returns sent | skipped_fictional | not_configured | failed."""
    if FICTIONAL_NUMBER.match(stop.customer_phone):
        return "skipped_fictional"
    if not (os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_PHONE_NUMBER")):
        return "not_configured"
    if stop.window_end:
        when = f"{nice_date(stop.window_date or today_str())}, {stop.window_start}-{stop.window_end}"
        verb = "is booked for" if kind == "booked" else "has moved to"
        body = f"MultiDrop: your delivery {stop.tracking_code} {verb} {when} (SF time). Reply or track your order in the app."
    else:
        body = f"MultiDrop: the delivery window for {stop.tracking_code} was cleared. We'll deliver at any time on your route day."
    try:
        tw = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        res = await run_in_threadpool(lambda: tw.messages.create(to=stop.customer_phone, from_=twilio_number(), body=body))
        await db.contact_logs.insert_one({"id": str(uuid.uuid4()), "stop_id": stop.id, "channel": "sms", "kind": f"booking_{kind}",
                                          "message": body, "twilio_sid": res.sid, "status": res.status, "created_at": now_iso()})
        return "sent"
    except Exception as e:
        logger.error(f"Booking SMS failed for {stop.tracking_code}: {e}")
        return "failed"


@api_router.put("/stops/{stop_id}/window")
async def set_window(stop_id: str, body: WindowIn):
    """Set (or clear with nulls) the customer's requested delivery window, HH:MM depot local time."""
    stop = await get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    day = None
    if body.start or body.end:
        if not (body.start and body.end and HHMM.match(body.start) and HHMM.match(body.end)) or body.start >= body.end:
            raise HTTPException(400, "Window needs start and end in HH:MM with start before end")
        day = check_service_date(body.date)
        if day == today_str() and body.end <= datetime.now(TZ).strftime("%H:%M"):
            raise HTTPException(400, "That window has already passed today")
    moved_off = None
    if stop.route_id and day:
        r = await db.routes.find_one({"id": stop.route_id}, {"_id": 0})
        if r and r.get("service_date", today_str()) != day:
            if stop.status != "pending":
                raise HTTPException(400, "Driver has already arrived — the day can't be changed now")
            moved_off = r["name"]
    upd = {"window_start": body.start or None, "window_end": body.end or None, "window_date": day}
    if moved_off:
        upd.update({"route_id": None, "seq": 0})
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if stop.route_id:
        if moved_off:
            rest = await get_stops(stop.route_id)
            await apply_sequence(stop.route_id, [x.id for x in rest])
            r = await db.routes.find_one({"id": stop.route_id}, {"_id": 0})
            if not rest and r["status"] == "planned":
                await db.routes.delete_one({"id": stop.route_id})
            else:
                await maybe_complete_route(stop.route_id)
                await broadcast_route(stop.route_id)
        else:
            await broadcast_route(stop.route_id)
    if hub.rooms.get(f"track:{stop.tracking_code}"):
        await hub.send(f"track:{stop.tracking_code}", await tracking_payload(stop.tracking_code))
    updated = await get_stop_or_404(stop_id)
    kind = "cleared" if not updated.window_end else ("booked" if not stop.window_end else "changed")
    unchanged = (stop.window_start, stop.window_end, stop.window_date) == (updated.window_start, updated.window_end, updated.window_date)
    sms = "unchanged" if unchanged else await send_booking_sms(updated, kind)
    return {**updated.model_dump(), "moved_off_route": moved_off, "sms_status": sms}


class RatingIn(BaseModel):
    stars: int
    comment: Optional[str] = None


@api_router.post("/stops/{stop_id}/rating")
async def rate_delivery(stop_id: str, body: RatingIn):
    """Customer rates a delivered order once (1-5 stars, optional comment)."""
    stop = await get_stop_or_404(stop_id)
    if stop.status != "delivered":
        raise HTTPException(400, "Only delivered orders can be rated")
    if stop.rating:
        raise HTTPException(400, "This delivery has already been rated")
    if not 1 <= body.stars <= 5:
        raise HTTPException(400, "Stars must be between 1 and 5")
    comment = (body.comment or "").strip()[:300] or None
    upd = {"rating": body.stars, "rating_comment": comment, "rated_at": now_iso()}
    if body.stars <= LOW_RATING:
        upd["follow_up_status"] = "open"
        upd["apology_code"] = "SORRY-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    rated = await get_stop_or_404(stop_id)
    if body.stars <= LOW_RATING:
        status = await send_apology_sms(rated)
        await db.stops.update_one({"id": stop_id}, {"$set": {"apology_sms_status": status}})
        rated = await get_stop_or_404(stop_id)
    if body.stars <= LOW_RATING and hub.rooms.get("admin"):
        await hub.send("admin", {"type": "low_rating", "alert": await low_rating_row(rated)})
    return rated.model_dump()


LOW_RATING = 2
PROOF_LINK_DAYS = 30
POSITIVE_TAGS = {"ON TIME", "FRIENDLY DRIVER", "CAREFUL WITH PARCEL", "EASY TO FOLLOW"}  # never counted as complaint reasons
APOLOGY_DISCOUNT_PCT = 15


async def send_apology_sms(stop: "Stop") -> str:
    """Text a sorry note + discount code. Never blocks the rating: sent | skipped_fictional | not_configured | failed."""
    if FICTIONAL_NUMBER.match(stop.customer_phone):
        return "skipped_fictional"
    if not (os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_PHONE_NUMBER")):
        return "not_configured"
    first = stop.customer_name.split(" ")[0]
    body = (f"MultiDrop: Sorry {first}, delivery {stop.tracking_code} wasn't up to standard. "
            f"Here's {APOLOGY_DISCOUNT_PCT}% off your next delivery with code {stop.apology_code}. Our team will follow up.")
    try:
        tw = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        res = await run_in_threadpool(lambda: tw.messages.create(to=stop.customer_phone, from_=twilio_number(), body=body))
        await db.contact_logs.insert_one({"id": str(uuid.uuid4()), "stop_id": stop.id, "channel": "sms", "kind": "apology",
                                          "message": body, "twilio_sid": res.sid, "status": res.status, "created_at": now_iso()})
        return "sent"
    except Exception as e:
        logger.error(f"Apology SMS failed for {stop.tracking_code}: {e}")
        return "failed"


@api_router.get("/alerts/trends")
async def alert_trends(days: int = 7):
    """Low-rating hot spots over the last N days, by driver and by zone, with the most common reasons."""
    days = max(1, min(days, 31))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    docs = [Stop(**d) for d in await db.stops.find({"rating": {"$ne": None}, "rated_at": {"$gte": since}}, {"_id": 0}).to_list(5000)]
    route_driver = {r["id"]: r.get("driver_id") for r in await db.routes.find({}, {"_id": 0, "id": 1, "driver_id": 1}).to_list(5000)}
    names = {d["id"]: d["name"] for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)}

    def group(key_fn, label_fn):
        g: Dict[str, dict] = {}
        for st in docs:
            k = key_fn(st)
            if not k:
                continue
            row = g.setdefault(k, {"key": k, "label": label_fn(k), "ratings": 0, "low": 0, "reasons": {}})
            row["ratings"] += 1
            if st.rating <= LOW_RATING:
                row["low"] += 1
                for r in (st.rating_comment or "").split(","):
                    r = r.strip().upper()
                    if r and r not in POSITIVE_TAGS:
                        row["reasons"][r] = row["reasons"].get(r, 0) + 1
        out = []
        for row in g.values():
            reasons = sorted(row.pop("reasons").items(), key=lambda kv: -kv[1])
            out.append({**row, "low_pct": round(row["low"] / row["ratings"] * 100) if row["ratings"] else 0,
                        "top_reasons": [{"reason": r, "count": c} for r, c in reasons[:3]]})
        return sorted(out, key=lambda x: (-x["low"], -x["low_pct"]))

    today = datetime.now(TZ).date()
    daily = []
    for back in range(days - 1, -1, -1):
        d = (today - timedelta(days=back)).isoformat()
        daily.append({"date": d, "low": sum(1 for st in docs if st.rating <= LOW_RATING and datetime.fromisoformat(st.rated_at).astimezone(TZ).date().isoformat() == d)})
    return {
        "days": days, "total_ratings": len(docs), "total_low": sum(1 for st in docs if st.rating <= LOW_RATING),
        "by_driver": group(lambda st: route_driver.get(st.route_id), lambda k: names.get(k, k)),
        "by_zone": group(lambda st: st.zone, lambda k: k),
        "daily": daily,
    }


class RedeemIn(BaseModel):
    code: str
    order_value: float
    order_ref: Optional[str] = None


@api_router.post("/codes/redeem")
async def redeem_code(body: RedeemIn):
    """Record an apology code being used on a new order (called by checkout, or entered by an admin)."""
    code = body.code.strip().upper()
    stop = await db.stops.find_one({"apology_code": code}, {"_id": 0, "id": 1})
    if not stop:
        raise HTTPException(404, "Unknown code")
    if await db.code_redemptions.find_one({"code": code}):
        raise HTTPException(400, "Code already used")
    if not 0 < body.order_value <= 10000:
        raise HTTPException(400, "Order value must be between 0 and 10,000")
    rec = {"id": str(uuid.uuid4()), "code": code, "stop_id": stop["id"], "order_value": round(body.order_value, 2),
           "discount": round(body.order_value * APOLOGY_DISCOUNT_PCT / 100, 2), "order_ref": (body.order_ref or "").strip()[:40] or None,
           "redeemed_at": now_iso()}
    try:
        await db.code_redemptions.insert_one({**rec})
    except DuplicateKeyError:
        raise HTTPException(400, "Code already used")
    return rec


@api_router.get("/codes/apology")
async def apology_codes():
    """Every apology code issued, whether it was used, and what those customers spent afterwards."""
    stops = [Stop(**d) for d in await db.stops.find({"apology_code": {"$ne": None}}, {"_id": 0}).sort("rated_at", -1).to_list(1000)]
    red = {r["code"]: r for r in await db.code_redemptions.find({}, {"_id": 0}).to_list(5000)}
    rows = [{"code": st.apology_code, "stop_id": st.id, "tracking_code": st.tracking_code, "customer_name": st.customer_name,
             "rating": st.rating, "issued_at": st.rated_at, "sms_status": st.apology_sms_status, "redemption": red.get(st.apology_code)} for st in stops]
    used = [r["redemption"] for r in rows if r["redemption"]]
    revenue = round(sum(r["order_value"] for r in used), 2)
    discount = round(sum(r["discount"] for r in used), 2)
    return {
        "discount_pct": APOLOGY_DISCOUNT_PCT, "issued": len(rows), "redeemed": len(used),
        "redemption_rate": round(len(used) / len(rows) * 100) if rows else 0,
        "revenue": revenue, "discount_given": discount, "net_revenue": round(revenue - discount, 2),
        "avg_order": round(revenue / len(used), 2) if used else 0, "codes": rows,
    }


class CoachingIn(BaseModel):
    note: str
    reason: Optional[str] = None


@api_router.post("/drivers/{driver_id}/coaching")
async def send_coaching(driver_id: str, body: CoachingIn):
    await get_driver_or_404(driver_id)
    note = body.note.strip()
    if not note:
        raise HTTPException(400, "Write a note first")
    rec = {"id": str(uuid.uuid4()), "driver_id": driver_id, "note": note[:600], "reason": (body.reason or "").strip()[:80] or None,
           "created_at": now_iso(), "read_at": None}
    await db.coaching_notes.insert_one({**rec})
    return rec


@api_router.get("/drivers/{driver_id}/coaching")
async def list_coaching(driver_id: str, unread: bool = False):
    await get_driver_or_404(driver_id)
    q = {"driver_id": driver_id, **({"read_at": None} if unread else {})}
    return await db.coaching_notes.find(q, {"_id": 0}).sort("created_at", -1).to_list(100)


@api_router.post("/coaching/{note_id}/read")
async def read_coaching(note_id: str):
    res = await db.coaching_notes.update_one({"id": note_id, "read_at": None}, {"$set": {"read_at": now_iso()}})
    if not res.matched_count and not await db.coaching_notes.find_one({"id": note_id}):
        raise HTTPException(404, "Note not found")
    return {"ok": True}


# ---- public proof-of-delivery share link
@api_router.post("/stops/{stop_id}/share")
async def share_proof(stop_id: str):
    stop = await get_stop_or_404(stop_id)
    if stop.status != "delivered":
        raise HTTPException(400, "Proof can be shared after delivery")
    created = datetime.fromisoformat(stop.share_created_at) if stop.share_created_at else None
    expired = not created or datetime.now(timezone.utc) - created >= timedelta(days=PROOF_LINK_DAYS)
    token = stop.share_token
    if not token or expired:  # new link (old one stops working) when missing or expired
        token, created = secrets.token_urlsafe(16), datetime.now(timezone.utc)
        await db.stops.update_one({"id": stop_id}, {"$set": {"share_token": token, "share_created_at": created.isoformat()}})
    return {"token": token, "path": f"/api/proof/{token}", "expires_at": (created + timedelta(days=PROOF_LINK_DAYS)).isoformat(), "valid_days": PROOF_LINK_DAYS}


@api_router.get("/proof/{token}", response_class=HTMLResponse)
async def proof_page(token: str):
    doc = await db.stops.find_one({"share_token": token}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Link not found")
    st = Stop(**doc)
    if not st.share_created_at or datetime.now(timezone.utc) - datetime.fromisoformat(st.share_created_at) >= timedelta(days=PROOF_LINK_DAYS):
        return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Link expired</title></head><body style="font-family:'Courier New',monospace;padding:24px">
<h1 style="font-family:Arial,sans-serif">LINK EXPIRED</h1><p>Proof-of-delivery links last {PROOF_LINK_DAYS} days to protect your privacy.
Open your order in the MultiDrop app to share a fresh link.</p></body></html>""", status_code=410)
    esc = xml_escape
    when = datetime.fromisoformat(st.delivered_at).astimezone(TZ).strftime("%a %d %b %Y, %H:%M") if st.delivered_at else ""
    photo = f'<img src="/api/files/{esc(st.pod_photo_path)}" alt="Delivery photo">' if st.pod_photo_path else ""
    sig = (f'<svg viewBox="0 0 360 200" preserveAspectRatio="xMidYMid meet"><path d="{esc(st.pod_signature)}" stroke="#0A0A0A" stroke-width="4" '
           f'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>') if st.pod_signature else ""
    qr = '<div class="ok">&#10003; PARCEL QR SCANNED AT THE DOOR</div>' if st.pod_qr_verified else ""
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proof of delivery {esc(st.tracking_code)}</title><meta name="robots" content="noindex">
<style>body{{font-family:'Courier New',monospace;margin:0;color:#0A0A0A;background:#fff}}
header{{background:#0A0A0A;color:#fff;padding:20px}}h1{{font-family:Arial,sans-serif;margin:4px 0 0;font-size:28px}}
.k{{color:#E60000;font-weight:bold;letter-spacing:2px;font-size:12px}}main{{padding:20px;max-width:560px}}
.box{{border:2px solid #0A0A0A;margin:12px 0;padding:12px}}img{{width:100%;display:block}}svg{{width:100%;height:180px}}
.ok{{background:#008000;color:#fff;padding:12px;font-weight:bold}}.m{{font-size:13px;margin:4px 0}}</style></head><body>
<header><div class="k">MULTIDROP · PROOF OF DELIVERY</div><h1>{esc(st.tracking_code)}</h1></header><main>
<p class="m"><b>{esc(st.customer_name)}</b></p><p class="m">{esc(st.address)}</p><p class="m">DELIVERED {esc(when)} (SF TIME)</p>
{f'<div class="box">{photo}</div>' if photo else ''}{f'<div class="box"><div class="m">SIGNATURE</div>{sig}</div>' if sig else ''}{qr}
</main></body></html>"""
    return HTMLResponse(html)


async def low_rating_row(st: "Stop") -> dict:
    driver, route_name = None, None
    if st.route_id:
        r = await db.routes.find_one({"id": st.route_id}, {"_id": 0})
        if r:
            route_name = r["name"]
            if r.get("driver_id"):
                d = await db.drivers.find_one({"id": r["driver_id"]}, {"_id": 0, "id": 1, "name": 1})
                driver = d
    return {"stop": st.model_dump(), "driver": driver, "route_name": route_name}


@api_router.get("/alerts/low-ratings")
async def low_ratings(status: str = "open"):
    """1-2 star deliveries for admin follow-up. status: open | resolved | all."""
    q = {"rating": {"$lte": LOW_RATING, "$gte": 1}}
    if status not in ("open", "resolved", "all"):
        raise HTTPException(400, "status must be open, resolved or all")
    if status in ("open", "resolved"):
        q["follow_up_status"] = status
    docs = await db.stops.find(q, {"_id": 0}).sort("rated_at", -1).to_list(200)
    return [await low_rating_row(Stop(**d)) for d in docs]


class FollowUpIn(BaseModel):
    note: Optional[str] = None
    resolved: bool = True


@api_router.post("/stops/{stop_id}/follow-up")
async def follow_up(stop_id: str, body: FollowUpIn):
    stop = await get_stop_or_404(stop_id)
    if not stop.rating or stop.rating > LOW_RATING:
        raise HTTPException(400, "Only 1-2 star deliveries need follow-up")
    note = (body.note or "").strip()[:500] or stop.follow_up_note
    await db.stops.update_one({"id": stop_id}, {"$set": {"follow_up_status": "resolved" if body.resolved else "open",
                                                         "follow_up_note": note, "follow_up_at": now_iso()}})
    return await low_rating_row(await get_stop_or_404(stop_id))


@api_router.websocket("/ws/admin")
async def ws_admin(ws: WebSocket):
    await hub.join("admin", ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave("admin", ws)


@api_router.post("/stops/{stop_id}/arrive")
async def arrive(stop_id: str):
    stop = await get_stop_or_404(stop_id)
    if stop.status != "pending":
        raise HTTPException(400, f"Stop is {stop.status}")
    await ensure_stop_actionable_today(stop)
    await db.stops.update_one({"id": stop_id}, {"$set": {"status": "arrived", "arrived_at": now_iso(), "arrival_method": "manual"}})
    if stop.route_id:
        await broadcast_route(stop.route_id)
    return (await get_stop_or_404(stop_id)).model_dump()


@api_router.post("/stops/{stop_id}/deliver")
async def deliver(stop_id: str, body: DeliverIn):
    stop = await get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    await ensure_stop_actionable_today(stop)
    if not body.photo_path and not body.signature and not stop.pod_qr_verified:
        raise HTTPException(400, "Proof of delivery requires a photo, signature or parcel QR scan")
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
    await ensure_stop_actionable_today(stop)
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
    stop = await get_stop_or_404(stop_id)
    upd = {"customer_phone": phone}
    if phone != stop.customer_phone and stop.nearby_sms_status in ("skipped_fictional", "not_configured", "failed"):
        upd["nearby_sms_status"] = None  # new number: allow the "2 stops away" text again
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if stop.route_id and "nearby_sms_status" in upd:
        await broadcast_route(stop.route_id)
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
    live = [r["id"] for r in await db.routes.find({"service_date": {"$gte": today_str()}}, {"_id": 0, "id": 1}).to_list(200)]
    docs = await db.stops.find({"route_id": {"$in": live}}, {"_id": 0}).sort("seq", 1).to_list(200)
    return [{"tracking_code": d["tracking_code"], "customer_name": d["customer_name"], "status": d["status"], "seq": d["seq"]} for d in docs]


def delivered_on_time(st: Stop) -> Optional[bool]:
    """True/False for windowed deliveries, None when the stop had no window."""
    if not st.delivered_at or not st.window_end:
        return None
    local = datetime.fromisoformat(st.delivered_at).astimezone(TZ)
    day = date_cls.fromisoformat(st.window_date) if st.window_date else local.date()
    h, m = map(int, st.window_end.split(":"))
    return local <= datetime.combine(day, time_cls(h, m), TZ)


@api_router.get("/drivers/{driver_id}/summary")
async def driver_summary(driver_id: str, date: Optional[str] = None):
    """End-of-day scorecard for a driver: drops, on-time rate, km driven, pace."""
    driver = await get_driver_or_404(driver_id)
    return await day_stats(driver, date or today_str())


def avg(xs):
    return round(sum(xs) / len(xs), 1) if xs else None


async def day_stats(driver: Driver, day: str) -> dict:
    routes = [Route(**d) for d in await db.routes.find({"driver_id": driver.id, "service_date": day, "started_at": {"$ne": None}}, {"_id": 0}).to_list(50)]
    stops: List[Stop] = []
    hours = 0.0
    for r in routes:
        stops += await get_stops(r.id)
        end = datetime.fromisoformat(r.completed_at) if r.completed_at else datetime.now(timezone.utc)
        hours += max((end - datetime.fromisoformat(r.started_at)).total_seconds() / 3600, MIN_PACE_HOURS)
    delivered = [x for x in stops if x.status == "delivered"]
    failed = [x for x in stops if x.status == "failed"]
    windowed = [x for x in delivered if x.window_end]
    on_time = sum(1 for x in delivered if delivered_on_time(x) is not False)
    km = sum(r.actual_distance_m for r in routes) / 1000
    planned_km = sum(r.planned_distance_m for r in routes) / 1000
    return {
        "driver": driver.model_dump(), "date": day,
        "routes": [{"id": r.id, "name": r.name, "status": r.status} for r in routes],
        "all_completed": bool(routes) and all(r.status == "completed" for r in routes),
        "total_stops": len(stops), "delivered": len(delivered), "failed": len(failed),
        "remaining": len(stops) - len(delivered) - len(failed),
        "on_time": on_time,
        # on-time rate counts only deliveries that had a customer window
        "on_time_rate": round(sum(1 for x in windowed if delivered_on_time(x)) / len(windowed) * 100) if windowed else None,
        "windowed_delivered": len(windowed), "windowed_on_time": sum(1 for x in windowed if delivered_on_time(x)),
        "km_driven": round(km, 2), "planned_km": round(planned_km, 2),
        "hours": round(hours, 2), "drops_per_hour": round(len(delivered) / hours, 1) if hours else 0,
        "geofence_arrivals": sum(1 for x in stops if x.arrival_method == "geofence"),
        "avg_rating": avg([x.rating for x in delivered if x.rating]), "ratings": sum(1 for x in delivered if x.rating),
        "pod": {"photo": sum(1 for x in delivered if x.pod_photo_path), "signature": sum(1 for x in delivered if x.pod_signature),
                "qr": sum(1 for x in delivered if x.pod_qr_verified)},
        "stops": [{"seq": x.seq, "route_id": x.route_id, "customer_name": x.customer_name, "tracking_code": x.tracking_code, "status": x.status,
                   "delivered_at": x.delivered_at, "window": f"{x.window_start}-{x.window_end}" if x.window_end else None,
                   "on_time": delivered_on_time(x), "fail_reason": x.fail_reason} for x in stops],
    }


@api_router.get("/drivers/{driver_id}/scorecard")
async def driver_scorecard(driver_id: str, days: int = 7):
    """Rolling weekly scorecard: one row per day (oldest first), week totals and this week's badges."""
    sc = await scorecard_data(driver_id, days)
    board = await all_scorecards(days)
    sc["badges"] = next((c["badges"] for c in board if c["driver"]["id"] == driver_id), [])
    sc["rank"] = next((i + 1 for i, c in enumerate(board) if c["driver"]["id"] == driver_id), None)
    return sc


async def scorecard_data(driver_id: str, days: int = 7) -> dict:
    driver = await get_driver_or_404(driver_id)
    days = max(1, min(days, 31))
    today = datetime.now(TZ).date()
    rows = []
    for back in range(days - 1, -1, -1):
        d = await day_stats(driver, (today - timedelta(days=back)).isoformat())
        rows.append({k: d[k] for k in ("date", "delivered", "failed", "on_time_rate", "windowed_delivered", "windowed_on_time",
                                        "drops_per_hour", "km_driven", "hours", "avg_rating", "ratings")} | {"routes": len(d["routes"])})
    delivered = sum(r["delivered"] for r in rows)
    hours = sum(r["hours"] for r in rows)
    windowed = sum(r["windowed_delivered"] for r in rows)
    win_ok = sum(r["windowed_on_time"] for r in rows)
    rated = [(r["avg_rating"], r["ratings"]) for r in rows if r["ratings"]]
    return {
        "driver": driver.model_dump(), "from": rows[0]["date"], "to": rows[-1]["date"], "days": rows,
        "totals": {
            "delivered": delivered, "failed": sum(r["failed"] for r in rows), "days_worked": sum(1 for r in rows if r["routes"]),
            "on_time_rate": round(win_ok / windowed * 100) if windowed else None, "windowed_delivered": windowed,
            "ratings": sum(n for _, n in rated),
            "drops_per_hour": round(delivered / hours, 1) if hours else 0, "hours": round(hours, 2),
            "km_driven": round(sum(r["km_driven"] for r in rows), 1),
            "avg_rating": round(sum(a * n for a, n in rated) / sum(n for _, n in rated), 1) if rated else None,
        },
    }


@api_router.get("/drivers/{driver_id}/badges/new")
async def new_badges(driver_id: str):
    """Badges the driver currently holds but hasn't celebrated yet."""
    doc = await db.drivers.find_one({"id": driver_id}, {"_id": 0, "seen_badges": 1})
    if doc is None:
        raise HTTPException(404, "Driver not found")
    seen = set(doc.get("seen_badges") or [])
    board = await all_scorecards(7)
    held = next((c["badges"] for c in board if c["driver"]["id"] == driver_id), [])
    lost = seen - {b["key"] for b in held}
    if lost:  # badge lost this week: forget it so winning it back is celebrated again
        await db.drivers.update_one({"id": driver_id}, {"$pull": {"seen_badges": {"$in": list(lost)}}})
    return {"badges": [b for b in held if b["key"] not in seen]}


class SeenIn(BaseModel):
    keys: List[str]


@api_router.post("/drivers/{driver_id}/badges/seen")
async def mark_badges_seen(driver_id: str, body: SeenIn):
    await get_driver_or_404(driver_id)
    await db.drivers.update_one({"id": driver_id}, {"$addToSet": {"seen_badges": {"$each": body.keys}}})
    return {"ok": True}


@api_router.get("/scorecards")
async def all_scorecards(days: int = 7):
    """Week totals for every driver, best on-time rate first (admin dashboard)."""
    out = []
    for d in await db.drivers.find({}, {"_id": 0}).to_list(100):
        sc = await scorecard_data(d["id"], days)
        out.append({"driver": sc["driver"], **sc["totals"], "trend": [r["delivered"] for r in sc["days"]]})
    award_badges(out)
    return sorted(out, key=lambda x: (-(x["on_time_rate"] if x["on_time_rate"] is not None else -1), -x["drops_per_hour"]))


BADGES = [
    # key, label, metric, minimum sample (field, n)
    ("on_time", "ON-TIME CHAMP", "on_time_rate", ("windowed_delivered", 3)),
    ("top_rated", "TOP RATED", "avg_rating", ("delivered", 3)),
    ("speed", "FASTEST PACE", "drops_per_hour", ("delivered", 5)),
    ("volume", "MOST DROPS", "delivered", ("delivered", 1)),
]


def award_badges(cards: list):
    """Weekly badges: best driver per metric (ties share the badge), minimum sample required."""
    for c in cards:
        c["badges"] = []
    for key, label, metric, (field, n) in BADGES:
        eligible = [c for c in cards if c.get(metric) is not None and c.get(field, 0) >= n]
        if not eligible:
            continue
        best = max(c[metric] for c in eligible)
        for c in eligible:
            if c[metric] == best:
                c["badges"].append({"key": key, "label": label, "value": best})


def qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=1)
    return img.to_string(encoding="unicode")


@api_router.get("/routes/{route_id}/labels.html", response_class=HTMLResponse)
async def route_labels(route_id: str, autoprint: int = 0):
    """Printable A4 sheet: one QR parcel label per open stop, 8 per page, in LOADING order
    (reverse drop order) so the first delivery is loaded last and sits by the door."""
    route = await get_route_or_404(route_id)
    stops = [x for x in await get_stops(route_id) if x.status not in ("delivered", "failed")][::-1]
    esc = xml_escape
    cards = []
    for load_no, x in enumerate(stops, 1):
        win = f"{x.window_date or ''} {x.window_start}-{x.window_end}".strip() if x.window_end else "ANY TIME"
        note = f'<div class="note">{esc(x.notes)}</div>' if x.notes else ''
        cards.append(f"""<div class="label"><div class="qr">{qr_svg('MULTIDROP:' + x.tracking_code)}</div>
<div class="info"><div class="tags"><span class="load">LOAD {load_no:02d}</span><span class="seq">DROP #{x.seq:02d}</span></div><div class="code">{esc(x.tracking_code)}</div>
<div class="name">{esc(x.customer_name)}</div><div class="addr">{esc(x.address)}</div>
<div class="meta">{esc(x.postal_code)} &middot; {esc(x.zone.upper())}</div><div class="win">WINDOW {esc(win)}</div>{note}</div></div>""")
    script = '<script>window.onload = function () { setTimeout(function () { window.print(); }, 300); };</script>' if autoprint else ''
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(route.name)} labels</title>
<style>
@page {{ size: A4; margin: 8mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Courier New', monospace; margin: 0; color: #0A0A0A; }}
h1 {{ font-family: Arial, sans-serif; font-size: 16px; margin: 0 0 6px; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }}
.label {{ border: 2px solid #0A0A0A; height: 64mm; padding: 3mm; display: flex; gap: 3mm; page-break-inside: avoid; break-inside: avoid; }}
.qr svg {{ width: 34mm; height: 34mm; }}
.info {{ flex: 1; display: flex; flex-direction: column; gap: 1mm; overflow: hidden; }}
.tags {{ display: flex; gap: 2mm; }}
.seq {{ background: #0A0A0A; color: #fff; font-weight: bold; padding: 1mm 2mm; font-size: 12px; }}
.load {{ border: 2px solid #E60000; color: #E60000; font-weight: bold; padding: 0.5mm 2mm; font-size: 12px; }}
.hint {{ font-size: 11px; margin: 0 0 6px; }}
.code {{ font-family: Arial, sans-serif; font-weight: 900; font-size: 22px; }}
.name {{ font-weight: bold; font-size: 13px; }}
.addr, .meta {{ font-size: 11px; }}
.win {{ font-weight: bold; font-size: 11px; border-top: 2px solid #0A0A0A; padding-top: 1mm; }}
.note {{ font-size: 10px; background: #FFD700; padding: 1mm; }}
</style></head><body>
<h1>{esc(route.name)} &middot; {esc(route.service_date)} &middot; {len(stops)} PARCELS</h1>
<p class="hint">LOAD IN LABEL ORDER (LOAD 01 FIRST, DEEPEST IN THE VAN). THE FIRST DROP IS LOADED LAST, NEAREST THE DOOR.</p>
<div class="grid">{''.join(cards) or '<p>No open parcels on this route.</p>'}</div>
{script}
</body></html>"""
    return HTMLResponse(html)


@api_router.get("/analytics")
async def analytics():
    """Today's operations (history routes are excluded; see /scorecards for the week)."""
    routes = [Route(**d) for d in await db.routes.find({"service_date": today_str()}, {"_id": 0}).to_list(500)]
    ids = [r.id for r in routes]
    scope = {"$or": [{"route_id": {"$in": ids}}, {"route_id": None}]}
    delivered = await db.stops.count_documents({"route_id": {"$in": ids}, "status": "delivered"})
    failed = await db.stops.count_documents({"route_id": {"$in": ids}, "status": "failed"})
    total = await db.stops.count_documents(scope)
    unassigned = await db.stops.count_documents({"route_id": None})
    geofenced = await db.stops.count_documents({"route_id": {"$in": ids}, "arrival_method": "geofence"})
    arrivals = await db.stops.count_documents({"route_id": {"$in": ids}, "arrived_at": {"$ne": None}})
    rated = [d["rating"] for d in await db.stops.find({"route_id": {"$in": ids}, "rating": {"$ne": None}}, {"_id": 0, "rating": 1}).to_list(1000)]
    hours, drops, devs, per_route = 0.0, 0, [], []
    for r in routes:
        if not r.started_at:
            continue
        end = datetime.fromisoformat(r.completed_at) if r.completed_at else datetime.now(timezone.utc)
        h = max((end - datetime.fromisoformat(r.started_at)).total_seconds() / 3600, MIN_PACE_HOURS)
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
        "avg_rating": round(sum(rated) / len(rated), 1) if rated else None,
        "routes": per_route,
    }


# ---------------------------------------------------------------- merchants & CSV order import
MERCHANTS = [{"id": "mer-1", "name": "Mission Bakery Co.", "contact": "orders@missionbakery.example"},
             {"id": "mer-2", "name": "Bay Books", "contact": "hello@baybooks.example"}]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = os.environ.get("NOMINATIM_USER_AGENT", "multidrop-merchant-import/1.0 (ops@multidrop.app)")
SF_VIEWBOX = "-122.53,37.83,-122.35,37.70"  # lookups are bounded to San Francisco
IMPORT_MAX_ROWS = 200
FLAGGED = ("invalid", "not_found", "lookup_failed")
COLUMNS = {
    "customer_name": {"customer_name", "name", "customer", "recipient", "full_name"},
    "phone": {"phone", "customer_phone", "phone_number", "mobile", "tel"},
    "address": {"address", "street", "street_address", "delivery_address", "address_line"},
    "notes": {"notes", "note", "instructions", "delivery_notes"},
    "window_date": {"window_date", "date", "delivery_date"},
    "window_start": {"window_start", "from", "start", "window_from"},
    "window_end": {"window_end", "to", "end", "window_to"},
}
TEMPLATE_CSV = ("customer_name,phone,address,notes,window_date,window_start,window_end\n"
                "Jane Doe,415 555 0123,1 Market St,Leave at reception,,,\n"
                "Sam Lee,+14155550188,450 Golden Gate Ave,Ring twice,,14:00,16:00\n")
_geo_lock = asyncio.Lock()
_geo_last = [0.0]


async def geocode(address: str) -> Optional[dict]:
    """OpenStreetMap Nominatim lookup (cached, max 1 request/second). Raises on network/HTTP errors."""
    key = re.sub(r"\s+", " ", address.strip().lower())
    cached = await db.geocodes.find_one({"key": key}, {"_id": 0})
    if cached:
        return cached["result"]
    async with _geo_lock:
        wait = 1.05 - (time.monotonic() - _geo_last[0])
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": NOMINATIM_UA}) as c:
                r = await c.get(NOMINATIM_URL, params={"q": f"{address}, San Francisco, CA, USA", "format": "jsonv2", "limit": 1,
                                                       "addressdetails": 1, "countrycodes": "us", "viewbox": SF_VIEWBOX, "bounded": 1})
        finally:
            _geo_last[0] = time.monotonic()
    r.raise_for_status()
    rows = r.json()
    result = None
    if rows:
        a = rows[0].get("address", {})
        result = {"lat": float(rows[0]["lat"]), "lng": float(rows[0]["lon"]), "postcode": a.get("postcode"),
                  "display_name": rows[0].get("display_name")}
    await db.geocodes.update_one({"key": key}, {"$set": {"key": key, "result": result, "cached_at": now_iso()}}, upsert=True)
    return result


def normalize_phone(p: str) -> Optional[str]:
    p = p.strip()
    digits = re.sub(r"\D", "", p)
    if p.startswith("+") and 8 <= len(digits) <= 15:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def validate_row(d: dict) -> Optional[str]:
    """Returns an error message, or None when the row is usable. Normalises phone in place."""
    missing = [k.replace("customer_", "") for k in ("customer_name", "phone", "address") if not d.get(k)]
    if missing:
        return f"Missing {', '.join(missing)}"
    phone = normalize_phone(d["phone"])
    if not phone:
        return f"Invalid phone number '{d['phone'][:20]}'"
    d["phone"] = phone
    ws, we, wd = d.get("window_start"), d.get("window_end"), d.get("window_date")
    if bool(ws) != bool(we):
        return "Give both window_start and window_end, or neither"
    if ws and (not HHMM.match(ws) or not HHMM.match(we) or ws >= we):
        return "Window must be HH:MM to HH:MM, start before end"
    if wd:
        if not ISO_DATE.match(wd):
            return "window_date must be YYYY-MM-DD"
        if wd < today_str():
            return "window_date is in the past"
    return None


async def new_tracking_code() -> str:
    while True:
        code = f"MC{secrets.randbelow(90000) + 10000}"
        if not await db.stops.find_one({"tracking_code": code}, {"_id": 1}):
            return code


async def process_row(row: dict) -> dict:
    d = row["data"]
    upd = {"error": None, "stop_id": None, "tracking_code": None}
    err = validate_row(d)
    if err:
        upd.update(status="invalid", error=err)
    else:
        try:
            geo = await geocode(d["address"])
        except Exception as e:
            logger.error(f"Geocode failed for import row {row['id']}: {e}")
            geo, upd["status"], upd["error"] = None, "lookup_failed", "Address lookup is unavailable right now — retry shortly"
        if geo is None and "status" not in upd:
            upd.update(status="not_found", error="Address not found in San Francisco — check the street and number")
        elif geo:
            zone = min(ZONES, key=lambda z: haversine_m({"lat": geo["lat"], "lng": geo["lng"]}, {"lat": ZONES[z]["center"][0], "lng": ZONES[z]["center"][1]}))
            stop = Stop(id=str(uuid.uuid4()), customer_name=d["customer_name"], customer_phone=d["phone"],
                        address=d["address"] if "san francisco" in d["address"].lower() else f"{d['address']}, San Francisco",
                        postal_code=geo.get("postcode") or ZONES[zone]["postal"], zone=zone, lat=geo["lat"], lng=geo["lng"],
                        tracking_code=await new_tracking_code(), notes=d.get("notes") or "",
                        window_start=d.get("window_start") or None, window_end=d.get("window_end") or None,
                        window_date=d.get("window_date") or None, merchant_id=row["merchant_id"], created_at=now_iso())
            await db.stops.insert_one(stop.model_dump())
            upd.update(status="created", stop_id=stop.id, tracking_code=stop.tracking_code)
    await db.import_rows.update_one({"id": row["id"]}, {"$set": upd})
    return {**row, **upd}


async def refresh_job(job_id: str, finished: bool = False):
    rows = await db.import_rows.find({"job_id": job_id}, {"_id": 0, "status": 1}).to_list(IMPORT_MAX_ROWS + 1)
    upd = {"processed": sum(1 for r in rows if r["status"] != "queued"),
           "created": sum(1 for r in rows if r["status"] == "created"),
           "flagged": sum(1 for r in rows if r["status"] in FLAGGED)}
    if finished:
        upd.update(status="done", finished_at=now_iso())
    await db.import_jobs.update_one({"id": job_id}, {"$set": upd})


async def process_import(job_id: str):
    try:
        for row in await db.import_rows.find({"job_id": job_id, "status": "queued"}, {"_id": 0}).sort("line", 1).to_list(IMPORT_MAX_ROWS + 1):
            await process_row(row)
            await refresh_job(job_id)
    except Exception as e:
        logger.error(f"Import {job_id} crashed: {e}")
    await refresh_job(job_id, finished=True)


async def get_merchant_or_404(merchant_id: str) -> dict:
    m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0})
    if not m:
        raise HTTPException(404, "Merchant not found")
    return m


@api_router.get("/merchant-template.csv")
async def merchant_template():
    return Response(TEMPLATE_CSV, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="multidrop-orders-template.csv"'})


@api_router.get("/merchants")
async def list_merchants():
    out = []
    for m in await db.merchants.find({}, {"_id": 0}).sort("id", 1).to_list(100):
        out.append({**m, "orders": await db.stops.count_documents({"merchant_id": m["id"]}),
                    "delivered": await db.stops.count_documents({"merchant_id": m["id"], "status": "delivered"}),
                    "flagged": await db.import_rows.count_documents({"merchant_id": m["id"], "status": {"$in": list(FLAGGED)}})})
    return out


@api_router.get("/merchants/{merchant_id}")
async def merchant_detail(merchant_id: str):
    m = await get_merchant_or_404(merchant_id)
    orders = await db.stops.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).to_list(500)
    flagged = await db.import_rows.find({"merchant_id": merchant_id, "status": {"$in": list(FLAGGED)}}, {"_id": 0}).sort([("created_at", -1), ("line", 1)]).to_list(500)
    jobs = await db.import_jobs.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).to_list(5)
    return {**m, "orders": [Stop(**o).model_dump() for o in orders], "flagged": flagged, "jobs": jobs,
            "counts": {"orders": len(orders), "pending": sum(1 for o in orders if o["status"] in ("pending", "arrived")),
                       "delivered": sum(1 for o in orders if o["status"] == "delivered"), "failed": sum(1 for o in orders if o["status"] == "failed"),
                       "flagged": len(flagged)}}


@api_router.post("/merchants/{merchant_id}/import")
async def import_orders(merchant_id: str, file: UploadFile = File(...)):
    """Upload a CSV of orders. Rows are validated now and geocoded in the background (about 1 per second)."""
    await get_merchant_or_404(merchant_id)
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "CSV is larger than 2 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(400, "Could not read the file — save it as CSV (UTF-8)")
    try:
        reader = csv.DictReader(io.StringIO(text))
        headers = {h: re.sub(r"[^a-z_]", "", (h or "").strip().lower().replace(" ", "_")) for h in (reader.fieldnames or [])}
        mapping = {}
        for h, norm in headers.items():
            for k, aliases in COLUMNS.items():
                if norm in aliases and k not in mapping.values():
                    mapping[h] = k
        need = [k for k in ("customer_name", "phone", "address") if k not in mapping.values()]
        if need:
            raise HTTPException(400, f"CSV needs columns: customer_name, phone, address (missing {', '.join(need)}). Download the template for the format.")
        rows = []
        for r in reader:
            d = {k: "" for k in COLUMNS}
            for h, k in mapping.items():
                d[k] = (r.get(h) or "").strip()
            if any(d.values()):
                rows.append(d)
    except csv.Error as e:
        raise HTTPException(400, f"Could not read the CSV: {e}")
    if not rows:
        raise HTTPException(400, "The CSV has no order rows")
    if len(rows) > IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Up to {IMPORT_MAX_ROWS} orders per file — split it into smaller files")
    job = {"id": str(uuid.uuid4()), "merchant_id": merchant_id, "filename": file.filename or "orders.csv", "total": len(rows),
           "processed": 0, "created": 0, "flagged": 0, "status": "running", "created_at": now_iso(), "finished_at": None}
    await db.import_jobs.insert_one(dict(job))
    await db.import_rows.insert_many([{"id": str(uuid.uuid4()), "job_id": job["id"], "merchant_id": merchant_id, "line": i + 2, "data": d,
                                       "status": "queued", "error": None, "stop_id": None, "tracking_code": None, "created_at": job["created_at"]}
                                      for i, d in enumerate(rows)])
    spawn(process_import(job["id"]))
    return job


@api_router.get("/imports/{job_id}")
async def import_status(job_id: str):
    job = await db.import_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Import not found")
    return job


class RowFixIn(BaseModel):
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    window_date: Optional[str] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None


@api_router.put("/import-rows/{row_id}")
async def fix_import_row(row_id: str, body: RowFixIn):
    """Edit a flagged row and try it again straight away."""
    row = await db.import_rows.find_one({"id": row_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Row not found")
    if row["status"] not in FLAGGED:
        raise HTTPException(400, f"Row is already {row['status']}")
    data = {**row["data"], **{k: v.strip() for k, v in body.model_dump().items() if v is not None}}
    await db.import_rows.update_one({"id": row_id}, {"$set": {"data": data}})
    out = await process_row({**row, "data": data})
    await refresh_job(row["job_id"], finished=(await db.import_jobs.find_one({"id": row["job_id"]}, {"_id": 0, "status": 1}))["status"] == "done")
    return out


@api_router.delete("/import-rows/{row_id}")
async def dismiss_import_row(row_id: str):
    row = await db.import_rows.find_one({"id": row_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Row not found")
    if row["status"] not in FLAGGED:
        raise HTTPException(400, f"Row is already {row['status']}")
    await db.import_rows.update_one({"id": row_id}, {"$set": {"status": "dismissed"}})
    await refresh_job(row["job_id"])
    return {"ok": True}


@api_router.get("/digest")
async def daily_digest(date: Optional[str] = None):
    """End-of-day ops digest for one depot day: pace, deliveries by hour, late drops, failures and complaints."""
    day = date or today_str()
    try:
        date_cls.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    routes = [Route(**d) for d in await db.routes.find({"service_date": day, "started_at": {"$ne": None}}, {"_id": 0}).to_list(500)]
    drivers = {d["id"]: d["name"] for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)}
    hours, all_stops, per_driver = 0.0, [], {}
    for r in routes:
        end = datetime.fromisoformat(r.completed_at) if r.completed_at else datetime.now(timezone.utc)
        h = max((end - datetime.fromisoformat(r.started_at)).total_seconds() / 3600, MIN_PACE_HOURS)
        hours += h
        stops = await get_stops(r.id)
        name = drivers.get(r.driver_id, "Unassigned")
        pd = per_driver.setdefault(r.driver_id or "-", {"driver_id": r.driver_id, "name": name, "delivered": 0, "failed": 0, "late": 0, "complaints": 0, "hours": 0.0})
        pd["hours"] += h
        for s in stops:
            all_stops.append((s, name, pd))

    def late_min(s: Stop) -> int:
        local = datetime.fromisoformat(s.delivered_at).astimezone(TZ)
        hh, mm = map(int, s.window_end.split(":"))
        wday = date_cls.fromisoformat(s.window_date) if s.window_date else local.date()
        return max(1, round((local - datetime.combine(wday, time_cls(hh, mm), TZ)).total_seconds() / 60))

    by_hour: Dict[int, int] = {}
    late, failed, complaints, ratings = [], [], [], []
    delivered = 0
    for s, name, pd in all_stops:
        if s.status == "delivered":
            delivered += 1
            pd["delivered"] += 1
            hr = datetime.fromisoformat(s.delivered_at).astimezone(TZ).hour
            by_hour[hr] = by_hour.get(hr, 0) + 1
            if delivered_on_time(s) is False:
                pd["late"] += 1
                late.append({"stop_id": s.id, "tracking_code": s.tracking_code, "customer_name": s.customer_name, "driver": name,
                             "window": f"{s.window_start}-{s.window_end}", "delivered_at": s.delivered_at, "late_min": late_min(s)})
            if s.rating:
                ratings.append(s.rating)
                if s.rating <= 2:
                    pd["complaints"] += 1
                    complaints.append({"stop_id": s.id, "tracking_code": s.tracking_code, "customer_name": s.customer_name, "driver": name,
                                       "rating": s.rating, "comment": s.rating_comment, "follow_up_status": s.follow_up_status})
        elif s.status == "failed":
            pd["failed"] += 1
            failed.append({"stop_id": s.id, "tracking_code": s.tracking_code, "customer_name": s.customer_name, "driver": name, "reason": s.fail_reason})
    hourly = [{"hour": h, "count": by_hour.get(h, 0)} for h in range(min(by_hour), max(by_hour) + 1)] if by_hour else []
    windowed = sum(1 for s, _, _ in all_stops if s.status == "delivered" and s.window_end)
    drivers_out = sorted(
        [{**p, "hours": round(p["hours"], 2), "drops_per_hour": round(p["delivered"] / p["hours"], 1) if p["hours"] else 0} for p in per_driver.values()],
        key=lambda p: -p["drops_per_hour"])
    return {
        "date": day, "routes": len(routes), "total_stops": len(all_stops), "delivered": delivered, "failed": len(failed),
        "remaining": len(all_stops) - delivered - len(failed), "hours": round(hours, 2),
        "drops_per_hour": round(delivered / hours, 1) if hours else 0,
        "late_count": len(late), "windowed_delivered": windowed,
        "on_time_rate": round((windowed - len(late)) / windowed * 100) if windowed else None,
        "complaint_count": len(complaints), "avg_rating": avg(ratings), "ratings": len(ratings),
        "hourly": hourly, "late": sorted(late, key=lambda x: -x["late_min"]), "failed_stops": failed,
        "complaints": sorted(complaints, key=lambda x: x["rating"]), "drivers": drivers_out,
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
