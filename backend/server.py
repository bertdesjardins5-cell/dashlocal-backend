from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Query, Depends, Request
import bcrypt
import jwt as pyjwt
from fastapi.responses import Response, HTMLResponse
from fastapi.concurrency import run_in_threadpool
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
import os
import ipaddress
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
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


async def require_admin(request: Request):
    """Route dependency: dispatch admin sign-in (functions resolve at call time)."""
    return await admin_user(await current_user(request))


ADMIN_ONLY = [Depends(require_admin)]


async def require_driver_self(driver_id: str, request: Request):
    """The signed-in driver themself (or the dispatch admin)."""
    u = await current_user(request)
    if u["role"] == "admin" or (u["role"] == "driver" and u.get("driver_id") == driver_id):
        return u
    raise HTTPException(403, "You can only open your own manifest")


async def require_route_driver(route_id: str, request: Request):
    """The driver assigned to this route (or the dispatch admin)."""
    u = await current_user(request)
    if u["role"] == "admin":
        return u
    if u["role"] == "driver":
        r = await db.routes.find_one({"id": route_id}, {"_id": 0, "driver_id": 1})
        if not r:
            raise HTTPException(404, "Route not found")
        if r.get("driver_id") == u.get("driver_id"):
            return u
    raise HTTPException(403, "This route belongs to another driver")


async def require_stop_driver(stop_id: str, request: Request):
    """The driver whose route holds this stop (or the dispatch admin)."""
    u = await current_user(request)
    if u["role"] == "admin":
        return u
    if u["role"] == "driver":
        s = await db.stops.find_one({"id": stop_id}, {"_id": 0, "route_id": 1})
        if not s:
            raise HTTPException(404, "Stop not found")
        r = await db.routes.find_one({"id": s.get("route_id")}, {"_id": 0, "driver_id": 1}) if s.get("route_id") else None
        if r and r.get("driver_id") == u.get("driver_id"):
            return u
    raise HTTPException(403, "This stop is on another driver's route")


async def require_any_driver(request: Request):
    u = await current_user(request)
    if u["role"] in ("admin", "driver"):
        return u
    raise HTTPException(403, "Driver sign-in required")


ORDER_UNLOCK_AFTER_DELIVERY_DAYS = 7


async def check_order_token(tok: str, stop_id: str):
    """Valid order token for this stop; it stops working 7 days after the order is delivered."""
    if decode_token(tok, "order")["sub"] != stop_id:
        raise HTTPException(403, "This check is for a different order")
    s = await db.stops.find_one({"id": stop_id}, {"_id": 0, "delivered_at": 1})
    if s and s.get("delivered_at") and datetime.now(timezone.utc) - datetime.fromisoformat(s["delivered_at"]) > timedelta(days=ORDER_UNLOCK_AFTER_DELIVERY_DAYS):
        raise HTTPException(401, "This order unlock has expired — enter the last 4 digits again")


async def require_order_access(stop_id: str, request: Request):
    """Customer holding an order token for this stop (from the last-4-digits check), the stop's driver, or the admin."""
    tok = request.headers.get("x-order-token", "").strip()
    if tok:
        await check_order_token(tok, stop_id)
        return {"role": "customer", "stop_id": stop_id}
    if not request.headers.get("authorization"):
        raise HTTPException(401, "Enter the last 4 digits of your phone to manage this order")
    return await require_stop_driver(stop_id, request)


ORDER_ACCESS = [Depends(require_order_access)]
DRIVER_SELF = [Depends(require_driver_self)]
ROUTE_DRIVER = [Depends(require_route_driver)]
STOP_DRIVER = [Depends(require_stop_driver)]
ANY_DRIVER = [Depends(require_any_driver)]

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
    """Priority (surcharged) drops are always served first, then the rest; each group is optimised."""
    pri = [s for s in stops if s.get("priority")]
    if not pri or len(pri) == len(stops):
        return _optimize_core(start, stops, now_local)
    first = _optimize_core(start, pri, now_local)
    return first + _optimize_core(first[-1], [s for s in stops if not s.get("priority")], now_local)


def _optimize_core(start, stops: list, now_local: Optional[datetime] = None) -> list:
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
    compass_zone: Optional[str] = None  # North / South / East / West the driver covers for auto dispatch


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
    compass_zone: Optional[str] = None  # North / South / East / West of the depot (set by auto dispatch)
    priority: bool = False  # priority delivery: served first on the route, carries a surcharge
    fee: Optional[dict] = None  # price snapshot when the order was created / re-priced (merchant orders)
    merchant_late_alert_at: Optional[str] = None  # "running late" email sent to the merchant (once)
    merchant_fail_alert_at: Optional[str] = None  # "failed drop" email sent to the merchant (once)
    created_at: Optional[str] = None
    fail_reason: Optional[str] = None
    customer_note: Optional[str] = None  # delivery note from the verified customer (gate code, "leave at back door")
    customer_note_at: Optional[str] = None
    customer_note_seen: bool = True  # False until the driver taps GOT IT on the manifest alert
    customer_note_read_at: Optional[str] = None  # read receipt shown to the customer
    customer_note_source: Optional[str] = None  # customer | saved (merchant's saved note) | last (customer's previous note)


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


PUBLIC_STOP_FIELDS = ("id", "tracking_code", "status", "arrived_at", "delivered_at", "pod_qr_verified",
                      "window_start", "window_end", "window_date", "rating", "nearby_sms_status")


def public_stop(stop: "Stop") -> dict:
    """What anyone holding the tracking code may see: no name, phone, address, notes, location or proof files."""
    d = {k: getattr(stop, k) for k in PUBLIC_STOP_FIELDS}
    d.update({"has_photo": bool(stop.pod_photo_path), "has_signature": bool(stop.pod_signature),
              "customer_note_read": bool(stop.customer_note and stop.customer_note_read_at)})
    return d


def file_url(path: str, minutes: int = 120) -> str:
    """Short-lived signed link to one stored proof file."""
    return f"/api/files/{path}?t={issue_token(path, 'file', purpose='file', minutes=minutes)}"


def order_private(stop: "Stop") -> dict:
    """Extra order details for a customer who proved the order is theirs (last 4 phone digits)."""
    return {"customer_name": stop.customer_name, "address": stop.address, "lat": stop.lat, "lng": stop.lng,
            "pod_photo_url": file_url(stop.pod_photo_path) if stop.pod_photo_path else None,
            "pod_signature": stop.pod_signature, "apology_code": stop.apology_code, "customer_note": stop.customer_note,
            "customer_note_source": stop.customer_note_source}


async def tracking_payload(code: str) -> dict:
    sdoc = await db.stops.find_one({"tracking_code": code.upper()}, {"_id": 0})
    if not sdoc:
        raise HTTPException(404, "Tracking code not found")
    stop = Stop(**sdoc)
    out = {"stop": public_stop(stop), "route_status": None, "driver": None, "stops_away": None,
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
        out["driver"] = {"name": driver.name.split(" ")[0], "vehicle": driver.vehicle, "lat": driver.lat, "lng": driver.lng} if driver else None
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
    base = plan_base(route.service_date)
    target = schedule(start, seq_stops, base)[-1]
    eta = target["arrive_min"]
    out["eta_minutes"] = round(eta) if route.service_date <= today_str() else None
    out["eta_time"] = (base.astimezone(timezone.utc) + timedelta(minutes=round(eta))).isoformat()
    out["late_min"] = round(target["late_min"])  # path stays empty: other customers' drops are never shown
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
    body = f"DashLocal: {driver_name.split(' ')[0]} is {where} with your delivery {stop.tracking_code}. Track live in the app."
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
    await maybe_late_alerts(route, stops)
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
    await db.merchant_alerts.delete_many({})
    await db.bill_payments.delete_many({})
    await db.invoices.delete_many({})
    await db.counters.delete_many({"key": "invoice"})
    await db.report_emails.delete_many({})
    await db.stops.delete_many({})
    await db.routes.delete_many({})
    rnd = random.Random(42)
    drivers = [
        Driver(id="drv-1", name="Marcus Reid", phone="+14155550101", vehicle="VAN-07", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso(), compass_zone="North"),
        Driver(id="drv-2", name="Priya Nair", phone="+14155550102", vehicle="VAN-12", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso(), compass_zone="South"),
        Driver(id="drv-3", name="Diego Santos", phone="+14155550103", vehicle="BIKE-03", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso(), compass_zone="East"),
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
    # rename DashLocal -> DashLocal: move demo/admin logins to the new domain (passwords unchanged except admin, from .env)
    for u in await db.users.find({"email": {"$regex": r"@multidrop\.app$"}}, {"_id": 0, "id": 1, "email": 1}).to_list(100):
        new = u["email"].replace("@dashlocal.app", "@dashlocal.app")
        if not await db.users.find_one({"email": new}):
            await db.users.update_one({"id": u["id"]}, {"$set": {"email": new}})
        else:
            await db.users.delete_one({"id": u["id"]})
    for m in MERCHANTS:
        await db.merchants.update_one({"id": m["id"]}, {"$setOnInsert": m}, upsert=True)
    await seed_users()
    for d in await db.stops.find({"merchant_id": {"$ne": None}, "fee": None}, {"_id": 0}).to_list(5000):
        await db.stops.update_one({"id": d["id"]}, {"$set": {"fee": compute_fee(d, await get_pricing(d["merchant_id"]))}})  # orders from before pricing existed
    for did, z in (("drv-1", "North"), ("drv-2", "South"), ("drv-3", "East")):  # demo coverage for auto dispatch
        await db.drivers.update_one({"id": did, "compass_zone": {"$exists": False}}, {"$set": {"compass_zone": z}})
    spawn(weekly_scheduler())
    spawn(late_alert_watcher())
    spawn(review_reminder_watcher())
    spawn(backfill_invoices())
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
    return {"message": "DashLocal API"}


@api_router.post("/admin/reset")
async def reset(request: Request):
    await admin_user(await current_user(request))  # wipes all data incl. merchant orders: admin only
    await seed(force=True)
    return {"ok": True}


@api_router.get("/drivers", dependencies=ADMIN_ONLY)
async def list_drivers():
    docs = await db.drivers.find({}, {"_id": 0}).to_list(100)
    out = []
    for d in docs:
        r = await db.routes.find_one({"driver_id": d["id"], "status": {"$in": ["active", "planned"]}}, {"_id": 0, "id": 1, "name": 1, "status": 1})
        out.append({**Driver(**d).model_dump(), "route": r})
    return out


@api_router.get("/drivers/{driver_id}/route", dependencies=DRIVER_SELF)
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


@api_router.post("/drivers/{driver_id}/location", dependencies=DRIVER_SELF)
async def post_location(driver_id: str, body: LocationIn):
    return await update_location(driver_id, body.lat, body.lng)


@api_router.post("/drivers/{driver_id}/simulate-step", dependencies=DRIVER_SELF)
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


@api_router.get("/zones", dependencies=ADMIN_ONLY)
async def zones(date: Optional[str] = None):
    docs = await db.stops.find(eligible_query(check_service_date(date)), {"_id": 0}).to_list(1000)
    groups: Dict[str, dict] = {}
    for d in docs:
        g = groups.setdefault(d["zone"], {"zone": d["zone"], "postal_code": d["postal_code"], "count": 0, "orders": []})
        g["count"] += 1
        g["orders"].append(Stop(**d).model_dump())
    return sorted(groups.values(), key=lambda g: -g["count"])


@api_router.post("/routes/batch", dependencies=ADMIN_ONLY)
async def batch(body: BatchIn):
    await get_driver_or_404(body.driver_id)
    async with _dispatch_lock:
        route = await create_batch(body.zone, body.driver_id, service_date=body.service_date)
    return await route_detail(route)


# ---------------------------------------------------------------- zone-based auto dispatch (N / S / E / W of the depot)
COMPASS = ("North", "South", "East", "West")


def compass_of(p: dict) -> str:
    """Wedge around the depot (fixed city-centre point): whichever of N/S/E/W the drop points to most."""
    dy = p["lat"] - DEPOT["lat"]
    dx = (p["lng"] - DEPOT["lng"]) * math.cos(math.radians(DEPOT["lat"]))
    if abs(dy) >= abs(dx):
        return "North" if dy >= 0 else "South"
    return "East" if dx >= 0 else "West"


class AutoDispatchIn(BaseModel):
    service_date: Optional[str] = None
    balance: bool = True  # split a busy zone with the least-loaded driver


BALANCE_MIN_STOPS = 6  # zones smaller than this are never split
BALANCE_RATIO = 2.0  # split when the zone driver would carry >= 2x the lightest driver's stops


def bearing(p: dict) -> float:
    return math.atan2(p["lat"] - DEPOT["lat"], (p["lng"] - DEPOT["lng"]) * math.cos(math.radians(DEPOT["lat"])))


ZONE_CENTRE = {"North": math.pi / 2, "East": 0.0, "South": -math.pi / 2, "West": math.pi}


def wedge_offset(p: dict, zone: str) -> float:
    """Angle from the zone's centre line, wrapped to (-π, π] — contiguous across the West wrap-around."""
    d = bearing(p) - ZONE_CENTRE[zone]
    return math.atan2(math.sin(d), math.cos(d))


def balance_plan(plan: Dict[str, Dict[str, list]], load: Dict[str, int]) -> list:
    """Move the far side of a busy zone (a contiguous slice of its wedge) to the least-loaded driver until loads even out."""
    splits = []
    for _ in range(len(COMPASS)):
        heavy = max(load, key=lambda d: load[d])
        light = min((d for d in load if d != heavy), key=lambda d: load[d])
        zones = plan.get(heavy, {})
        if not zones or load[heavy] < BALANCE_RATIO * max(1, load[light]):
            break
        z = max(zones, key=lambda k: len(zones[k]))
        stops = zones[z]
        k = min((load[heavy] - load[light]) // 2, len(stops) - 1)
        if len(stops) < BALANCE_MIN_STOPS or k < 1:
            break
        ordered = sorted(stops, key=lambda p: wedge_offset(p, z))
        zones[z], moved = ordered[:-k], ordered[-k:]
        plan.setdefault(light, {}).setdefault(z, []).extend(moved)
        load[heavy] -= k
        load[light] += k
        splits.append({"zone": z, "from_id": heavy, "helper_id": light, "moved": k})
    return splits


class CompassZoneIn(BaseModel):
    zone: Optional[str] = None


@api_router.put("/admin/drivers/{driver_id}/compass-zone", dependencies=ADMIN_ONLY)
async def set_compass_zone(driver_id: str, body: CompassZoneIn):
    if body.zone is not None and body.zone not in COMPASS:
        raise HTTPException(400, f"zone must be one of {', '.join(COMPASS)} or empty")
    await get_driver_or_404(driver_id)
    await db.drivers.update_one({"id": driver_id}, {"$set": {"compass_zone": body.zone}})
    return (await get_driver_or_404(driver_id)).model_dump()


@api_router.get("/dispatch/preview", dependencies=ADMIN_ONLY)
async def dispatch_preview(date: Optional[str] = None):
    """Unassigned orders for the day split by N/S/E/W, with the driver each zone would go to."""
    service_date = check_service_date(date)
    docs = await db.stops.find(eligible_query(service_date), {"_id": 0, "lat": 1, "lng": 1}).to_list(5000)
    counts = {z: 0 for z in COMPASS}
    for d in docs:
        counts[compass_of(d)] += 1
    drivers = [Driver(**d) for d in await db.drivers.find({}, {"_id": 0}).sort("id", 1).to_list(100)]
    return {"service_date": service_date, "total": len(docs),
            "zones": [{"zone": z, "count": counts[z], "driver": next(({"id": d.id, "name": d.name} for d in drivers if d.compass_zone == z), None)} for z in COMPASS],
            "drivers": [{"id": d.id, "name": d.name, "vehicle": d.vehicle, "compass_zone": d.compass_zone} for d in drivers]}


@api_router.post("/routes/auto-dispatch", dependencies=ADMIN_ONLY)
async def auto_dispatch(body: AutoDispatchIn):
    async with _dispatch_lock:  # two taps at once must not create duplicate / empty routes
        return await _auto_dispatch(body)


_dispatch_lock = asyncio.Lock()


async def _auto_dispatch(body: AutoDispatchIn):
    """One tap: split every unassigned order for the day into North/South/East/West of the depot and give each
    zone's stops to the driver covering it. A zone nobody covers goes to the driver with the fewest stops.
    Each driver gets one route for the day (stops are added to their existing open route, which is re-optimised)."""
    service_date = check_service_date(body.service_date)
    docs = await db.stops.find(eligible_query(service_date), {"_id": 0}).to_list(5000)
    if not docs:
        raise HTTPException(400, "No unassigned orders for that day")
    drivers = [Driver(**d) for d in await db.drivers.find({}, {"_id": 0}).sort("id", 1).to_list(100)]
    if not drivers:
        raise HTTPException(400, "No drivers")
    by_zone: Dict[str, list] = {z: [] for z in COMPASS}
    for d in docs:
        by_zone[compass_of(d)].append(d)
    open_routes = {r["driver_id"]: Route(**r) for r in await db.routes.find(
        {"service_date": service_date, "status": {"$in": ["planned", "active"]}, "driver_id": {"$ne": None}}, {"_id": 0}).to_list(200)}
    load = {d.id: 0 for d in drivers}
    for did, r in open_routes.items():
        if did in load:
            load[did] = await db.stops.count_documents({"route_id": r.id, "status": {"$in": ["pending", "arrived"]}})
    plan: Dict[str, Dict[str, list]] = {}  # driver_id -> zone -> stops
    covered = [z for z in COMPASS if by_zone[z] and any(d.compass_zone == z for d in drivers)]
    uncovered = [z for z in COMPASS if by_zone[z] and z not in covered]
    for z in covered + sorted(uncovered, key=lambda z: -len(by_zone[z])):  # covered first so fallback sees real loads
        d = next((d for d in drivers if d.compass_zone == z), None) or min(drivers, key=lambda d: (load[d.id], d.id))
        plan.setdefault(d.id, {})[z] = by_zone[z]
        load[d.id] += len(by_zone[z])
    splits = balance_plan(plan, load) if body.balance and len(drivers) > 1 else []
    out = []
    for did, zones in plan.items():
        stops = [s for z in COMPASS for s in zones.get(z, [])]
        label = "+".join(z for z in COMPASS if z in zones)
        for s in stops:
            await db.stops.update_one({"id": s["id"]}, {"$set": {"compass_zone": compass_of(s)}})
        route = open_routes.get(did)
        if route:
            for s in stops:
                await db.stops.update_one({"id": s["id"]}, {"$set": {"route_id": route.id, "seq": 999}})
            zone_names = sorted(set(route.zone.split("+")) | set(zones), key=lambda z: COMPASS.index(z) if z in COMPASS else 9)
            await db.routes.update_one({"id": route.id}, {"$set": {"zone": "+".join(zone_names)}})
            await optimize(route.id)
            if route.status == "active":
                await db.routes.update_one({"id": route.id}, {"$set": {"resequenced_at": now_iso(), "resequence_seen": False}})
            await broadcast_route(route.id)
            created = False
        else:
            ordered = optimize_sequence(DEPOT, stops, plan_base(service_date))
            count = await db.routes.count_documents({})
            route = Route(id=str(uuid.uuid4()), name=f"R-{101 + count} {label}", zone=label, driver_id=did,
                          service_date=service_date, planned_distance_m=round(path_length_m(DEPOT, ordered)))
            await db.routes.insert_one(route.model_dump())
            for i, s in enumerate(ordered):
                await db.stops.update_one({"id": s["id"]}, {"$set": {"route_id": route.id, "seq": i + 1}})
            created = True
        drv = next(d for d in drivers if d.id == did)
        helping = [x["zone"] for x in splits if x["helper_id"] == did]
        out.append({"route_id": route.id, "route_name": route.name, "driver_id": did, "driver_name": drv.name, "zones": list(zones),
                    "stops": len(stops), "created": created, "fallback": [z for z in zones if drv.compass_zone != z and z not in helping],
                    "helping": helping})
    return {"service_date": service_date, "dispatched": len(docs), "routes": out, "splits": splits}


@api_router.get("/routes", dependencies=ADMIN_ONLY)
async def list_routes():
    docs = await db.routes.find({"service_date": {"$gte": today_str()}}, {"_id": 0}).sort("created_at", -1).to_list(200)
    out = []
    for d in docs:
        det = await route_detail(Route(**d))
        det.pop("stops")
        out.append(det)
    return out


@api_router.get("/routes/{route_id}", dependencies=ROUTE_DRIVER)
async def get_route(route_id: str):
    return await route_detail(await get_route_or_404(route_id))


@api_router.post("/routes/{route_id}/optimize", dependencies=ADMIN_ONLY)
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


@api_router.put("/routes/{route_id}/sequence", dependencies=ADMIN_ONLY)
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


@api_router.post("/routes/{route_id}/update-seen", dependencies=ROUTE_DRIVER)
async def route_update_seen(route_id: str):
    """Driver acknowledged the dispatcher's latest re-order."""
    await get_route_or_404(route_id)
    await db.routes.update_one({"id": route_id}, {"$set": {"resequence_seen": True}})
    return await route_detail(await get_route_or_404(route_id))


@api_router.post("/routes/{route_id}/start", dependencies=ROUTE_DRIVER)
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


@api_router.get("/stops/{stop_id}", dependencies=STOP_DRIVER)
async def get_stop(stop_id: str):
    """Stop for its driver/admin, plus a doorstep hint: the latest proof photo from an earlier drop to the same customer."""
    stop = await get_stop_or_404(stop_id)
    prev = await db.stops.find_one({"id": {"$ne": stop.id}, "customer_phone": stop.customer_phone, "address": stop.address,
                                    "merchant_id": stop.merchant_id, "status": "delivered", "pod_photo_path": {"$ne": None}},
                                   {"_id": 0, "pod_photo_path": 1, "delivered_at": 1}, sort=[("delivered_at", -1)])
    hint = {"photo_url": file_url(prev["pod_photo_path"]), "delivered_at": prev.get("delivered_at")} if prev else None
    return {**stop.model_dump(), "doorstep_hint": hint}


class QrIn(BaseModel):
    code: str


@api_router.post("/stops/{stop_id}/verify-qr", dependencies=STOP_DRIVER)
async def verify_qr(stop_id: str, body: QrIn):
    """Parcel label QR encodes the tracking code; scanning the right parcel at the door is proof of delivery."""
    stop = await get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    await ensure_stop_actionable_today(stop)
    scanned = body.code.strip().upper()
    if scanned.startswith(("DASHLOCAL:", "MULTIDROP:")):  # labels printed before the rename still scan
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
        body = f"DashLocal: your delivery {stop.tracking_code} {verb} {when} (SF time). Reply or track your order in the app."
    else:
        body = f"DashLocal: the delivery window for {stop.tracking_code} was cleared. We'll deliver at any time on your route day."
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
async def set_window(stop_id: str, body: WindowIn, who: dict = Depends(require_order_access)):
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
    base = public_stop(updated) if who["role"] == "customer" else updated.model_dump()
    return {**base, "moved_off_route": moved_off, "sms_status": sms}


class RatingIn(BaseModel):
    stars: int
    comment: Optional[str] = None


@api_router.post("/stops/{stop_id}/rating")
async def rate_delivery(stop_id: str, body: RatingIn, who: dict = Depends(require_order_access)):
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
    if who["role"] == "customer":
        return {**public_stop(rated), "apology_code": rated.apology_code, "apology_sms_status": rated.apology_sms_status}
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
    body = (f"DashLocal: Sorry {first}, delivery {stop.tracking_code} wasn't up to standard. "
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


@api_router.get("/alerts/trends", dependencies=ADMIN_ONLY)
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


@api_router.post("/codes/redeem", dependencies=ADMIN_ONLY)
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


@api_router.get("/codes/apology", dependencies=ADMIN_ONLY)
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


@api_router.post("/drivers/{driver_id}/coaching", dependencies=ADMIN_ONLY)
async def send_coaching(driver_id: str, body: CoachingIn):
    await get_driver_or_404(driver_id)
    note = body.note.strip()
    if not note:
        raise HTTPException(400, "Write a note first")
    rec = {"id": str(uuid.uuid4()), "driver_id": driver_id, "note": note[:600], "reason": (body.reason or "").strip()[:80] or None,
           "created_at": now_iso(), "read_at": None}
    await db.coaching_notes.insert_one({**rec})
    return rec


@api_router.get("/drivers/{driver_id}/coaching", dependencies=DRIVER_SELF)
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
@api_router.post("/stops/{stop_id}/share", dependencies=ORDER_ACCESS)
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
Open your order in the DashLocal app to share a fresh link.</p></body></html>""", status_code=410)
    esc = xml_escape
    when = datetime.fromisoformat(st.delivered_at).astimezone(TZ).strftime("%a %d %b %Y, %H:%M") if st.delivered_at else ""
    photo = f'<img src="{esc(file_url(st.pod_photo_path))}" alt="Delivery photo">' if st.pod_photo_path else ""
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
<header><div class="k">DASHLOCAL · PROOF OF DELIVERY</div><h1>{esc(st.tracking_code)}</h1></header><main>
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


@api_router.get("/alerts/low-ratings", dependencies=ADMIN_ONLY)
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


@api_router.post("/stops/{stop_id}/follow-up", dependencies=ADMIN_ONLY)
async def follow_up(stop_id: str, body: FollowUpIn):
    stop = await get_stop_or_404(stop_id)
    if not stop.rating or stop.rating > LOW_RATING:
        raise HTTPException(400, "Only 1-2 star deliveries need follow-up")
    note = (body.note or "").strip()[:500] or stop.follow_up_note
    await db.stops.update_one({"id": stop_id}, {"$set": {"follow_up_status": "resolved" if body.resolved else "open",
                                                         "follow_up_note": note, "follow_up_at": now_iso()}})
    return await low_rating_row(await get_stop_or_404(stop_id))


@api_router.websocket("/ws/admin")
async def ws_admin(ws: WebSocket, token: str = ""):
    try:
        p = decode_token(token, "session")
        u = await db.users.find_one({"id": p["sub"], "role": "admin", "disabled": {"$ne": True}}, {"_id": 0, "id": 1})
    except HTTPException:
        u = None
    if not u:
        await ws.close(code=4401)
        return
    await hub.join("admin", ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave("admin", ws)


@api_router.post("/stops/{stop_id}/arrive", dependencies=STOP_DRIVER)
async def arrive(stop_id: str):
    stop = await get_stop_or_404(stop_id)
    if stop.status != "pending":
        raise HTTPException(400, f"Stop is {stop.status}")
    await ensure_stop_actionable_today(stop)
    await db.stops.update_one({"id": stop_id}, {"$set": {"status": "arrived", "arrived_at": now_iso(), "arrival_method": "manual"}})
    if stop.route_id:
        await broadcast_route(stop.route_id)
    return (await get_stop_or_404(stop_id)).model_dump()


@api_router.post("/stops/{stop_id}/deliver", dependencies=STOP_DRIVER)
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
    if stop.merchant_id and not stop.merchant_late_alert_at:
        done = await get_stop_or_404(stop_id)
        if delivered_on_time(done) is False and (await db.stops.update_one(
                {"id": stop_id, "merchant_late_alert_at": None}, {"$set": {"merchant_late_alert_at": now_iso()}})).modified_count:
            spawn(send_merchant_alert(done, "delivered_late"))
    if stop.merchant_id and (await db.merchants.update_one(
            {"id": stop.merchant_id, "approved_via_signup": True, "first_delivery_email_at": None},
            {"$set": {"first_delivery_email_at": now_iso()}})).modified_count:
        spawn(send_first_delivery_email(stop.merchant_id))
    if stop.merchant_id:
        await create_invoice(stop_id)
    if stop.route_id:
        await maybe_complete_route(stop.route_id)
        await broadcast_route(stop.route_id)
    return (await get_stop_or_404(stop_id)).model_dump()


@api_router.post("/stops/{stop_id}/fail", dependencies=STOP_DRIVER)
async def fail(stop_id: str, body: FailIn):
    stop = await get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    await ensure_stop_actionable_today(stop)
    await db.stops.update_one({"id": stop_id}, {"$set": {"status": "failed", "fail_reason": body.reason, "delivered_at": now_iso()}})
    if stop.merchant_id and (await db.stops.update_one(
            {"id": stop_id, "merchant_fail_alert_at": None}, {"$set": {"merchant_fail_alert_at": now_iso()}})).modified_count:
        spawn(send_merchant_alert(await get_stop_or_404(stop_id), "failed"))
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


@api_router.post("/stops/{stop_id}/contact", dependencies=STOP_DRIVER)
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
            text = (body.message or "").strip() or f"DashLocal: your driver {driver.name if driver else ''} is on the way with order {stop.tracking_code}."
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


@api_router.put("/stops/{stop_id}/phone", dependencies=STOP_DRIVER)
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


@api_router.put("/drivers/{driver_id}/phone", dependencies=DRIVER_SELF)
async def set_driver_phone(driver_id: str, body: PhoneIn):
    phone = re.sub(r"[\s\-()]", "", body.phone)
    if not E164.match(phone):
        raise HTTPException(400, "Use +countrycode format, e.g. +14155550123")
    await get_driver_or_404(driver_id)
    await db.drivers.update_one({"id": driver_id}, {"$set": {"phone": phone}})
    return (await get_driver_or_404(driver_id)).model_dump()


@api_router.post("/upload", dependencies=ANY_DRIVER)
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


async def file_allowed(path: str, request: Request, t: str) -> bool:
    """A signed link for this exact file, or any signed-in staff session (header or ?t=)."""
    h = request.headers.get("authorization", "")
    bearer = h[7:].strip() if h.lower().startswith("bearer ") else ""
    for tok in (t, bearer):
        if not tok:
            continue
        try:
            p = pyjwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALG], options={"require": ["exp", "iat", "sub", "role", "purpose"]})
        except pyjwt.InvalidTokenError:
            continue
        if p["purpose"] == "file" and p["sub"] == path:
            return True
        if p["purpose"] == "session" and await db.users.find_one({"id": p["sub"], "role": p["role"], "disabled": {"$ne": True}}, {"_id": 1}):
            return True
    return False


@api_router.get("/files/{path:path}")
async def download(path: str, request: Request, t: str = ""):
    if not await file_allowed(path, request, t):
        raise HTTPException(401, "This file needs a valid link or sign-in")
    rec = await db.files.find_one({"storage_path": path}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "File not found")
    data, ct = await run_in_threadpool(get_object, path)
    return Response(content=data, media_type=rec.get("content_type") or ct, headers={"Cache-Control": "private, max-age=3600"})


@api_router.get("/track/{code}")
async def track(code: str):
    return await tracking_payload(code)


class Last4In(BaseModel):
    last4: str


ORDER_TOKEN_MIN = 60 * 24 * 60  # remembered on the phone; also ends 7 days after delivery (check_order_token)


@api_router.post("/track/{code}/verify")
async def verify_order(code: str, body: Last4In, request: Request):
    """Customer proves the order is theirs with the last 4 digits of the phone on it. 5 tries per code per IP,
    10 per code, 30 per IP (15-min lock) so the 10,000 combinations can't be brute-forced."""
    code, ip, last4 = code.strip().upper(), client_ip(request), body.last4.strip()
    limits = [(f"trk:{code}:{ip}", 5), (f"trk:{code}:*", 10), (f"trkip:{ip}", 30)]
    atts = await check_lockout(limits)
    sdoc = await db.stops.find_one({"tracking_code": code}, {"_id": 0})
    digits = re.sub(r"\D", "", (sdoc or {}).get("customer_phone", ""))
    if not (sdoc and len(digits) >= 4 and re.fullmatch(r"\d{4}", last4) and secrets.compare_digest(digits[-4:], last4)):
        await record_failure(limits, atts)
        raise HTTPException(401, "Those digits don't match the phone on this order")
    await clear_lockout(limits[:2])
    return {"token": issue_token(sdoc["id"], "customer", purpose="order", minutes=ORDER_TOKEN_MIN), **order_private(Stop(**sdoc))}


@api_router.get("/track/{code}/private")
async def track_private(code: str, request: Request):
    """Refresh the verified customer's order details (e.g. proof photo after delivery)."""
    sdoc = await db.stops.find_one({"tracking_code": code.strip().upper()}, {"_id": 0})
    if not sdoc:
        raise HTTPException(404, "Tracking code not found")
    await check_order_token(request.headers.get("x-order-token", "").strip(), sdoc["id"])
    return order_private(Stop(**sdoc))


class CustomerNoteIn(BaseModel):
    note: Optional[str] = None


@api_router.put("/stops/{stop_id}/customer-note", dependencies=ORDER_ACCESS)
async def set_customer_note(stop_id: str, body: CustomerNoteIn):
    """Customer's delivery note for the driver (empty clears it). Locked once the driver has arrived."""
    stop = await get_stop_or_404(stop_id)
    if stop.status != "pending":
        raise HTTPException(400, "The driver is already at your door — the note can't be changed now")
    note = " ".join((body.note or "").split())[:200] or None
    await db.stops.update_one({"id": stop_id}, {"$set": {"customer_note": note, "customer_note_at": now_iso() if note else None,
                                                          "customer_note_seen": not note, "customer_note_read_at": None,
                                                          "customer_note_source": "customer" if note else None}})
    if stop.merchant_id:  # remembered for this customer's next order from the same store
        key = {"merchant_id": stop.merchant_id, "phone": stop.customer_phone}
        if note:
            await db.last_customer_notes.update_one(key, {"$set": {**key, "note": note, "updated_at": now_iso()}}, upsert=True)
        else:
            await db.last_customer_notes.delete_one(key)
    if stop.route_id:
        await broadcast_route(stop.route_id)
    return {"customer_note": note}


@api_router.post("/stops/{stop_id}/customer-note/seen", dependencies=STOP_DRIVER)
async def seen_customer_note(stop_id: str):
    stop = await get_stop_or_404(stop_id)
    upd = {"customer_note_seen": True}
    if stop.customer_note and not stop.customer_note_read_at:
        upd["customer_note_read_at"] = now_iso()
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if stop.route_id:
        await broadcast_route(stop.route_id)
    elif hub.rooms.get(f"track:{stop.tracking_code}"):
        await hub.send(f"track:{stop.tracking_code}", await tracking_payload(stop.tracking_code))
    return {"ok": True}


async def remembered_note(merchant_id: str, phone: str) -> tuple:
    """(note, source) for a regular customer: the store's saved note wins over the customer's last note."""
    saved = await db.saved_notes.find_one({"merchant_id": merchant_id, "phone": phone}, {"_id": 0})
    if saved:
        return saved["note"], "saved"
    last = await db.last_customer_notes.find_one({"merchant_id": merchant_id, "phone": phone}, {"_id": 0})
    return (last["note"], "last") if last else (None, None)




def delivered_on_time(st: Stop) -> Optional[bool]:
    """True/False for windowed deliveries, None when the stop had no window."""
    if not st.delivered_at or not st.window_end:
        return None
    local = datetime.fromisoformat(st.delivered_at).astimezone(TZ)
    day = date_cls.fromisoformat(st.window_date) if st.window_date else local.date()
    h, m = map(int, st.window_end.split(":"))
    return local <= datetime.combine(day, time_cls(h, m), TZ)


@api_router.get("/drivers/{driver_id}/summary", dependencies=DRIVER_SELF)
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


@api_router.get("/drivers/{driver_id}/scorecard", dependencies=DRIVER_SELF)
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


@api_router.get("/drivers/{driver_id}/badges/new", dependencies=DRIVER_SELF)
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


@api_router.post("/drivers/{driver_id}/badges/seen", dependencies=DRIVER_SELF)
async def mark_badges_seen(driver_id: str, body: SeenIn):
    await get_driver_or_404(driver_id)
    await db.drivers.update_one({"id": driver_id}, {"$addToSet": {"seen_badges": {"$each": body.keys}}})
    return {"ok": True}


@api_router.get("/scorecards", dependencies=ADMIN_ONLY)
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


@api_router.post("/routes/{route_id}/labels-link", dependencies=ADMIN_ONLY)
async def labels_link(route_id: str):
    """Short-lived (10 min) print link so the label sheet can open in a new tab / print dialog."""
    await get_route_or_404(route_id)
    tok = issue_token("labels", "admin", purpose="labels", minutes=10, rid=route_id)
    return {"path": f"/api/routes/{route_id}/labels.html?token={tok}"}


@api_router.get("/routes/{route_id}/labels.html", response_class=HTMLResponse)
async def route_labels(route_id: str, token: str = "", autoprint: int = 0):
    """Printable A4 sheet: one QR parcel label per open stop, 8 per page, in LOADING order
    (reverse drop order) so the first delivery is loaded last and sits by the door."""
    if decode_token(token, "labels").get("rid") != route_id:
        raise HTTPException(403, "This print link is for a different route")
    route = await get_route_or_404(route_id)
    stops = [x for x in await get_stops(route_id) if x.status not in ("delivered", "failed")][::-1]
    esc = xml_escape
    cards = []
    for load_no, x in enumerate(stops, 1):
        win = f"{x.window_date or ''} {x.window_start}-{x.window_end}".strip() if x.window_end else "ANY TIME"
        note = f'<div class="note">{esc(x.notes)}</div>' if x.notes else ''
        cards.append(f"""<div class="label"><div class="qr">{qr_svg('DASHLOCAL:' + x.tracking_code)}</div>
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


@api_router.get("/analytics", dependencies=ADMIN_ONLY)
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
NOMINATIM_UA = os.environ.get("NOMINATIM_USER_AGENT", "dashlocal-merchant-import/1.0 (ops@dashlocal.app)")
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
    "priority": {"priority", "priority_delivery", "rush", "express"},
    "delivery_note": {"delivery_note", "gate_code", "customer_note", "dropoff_note", "door_note"},
}
TRUTHY = {"yes", "y", "true", "1", "x", "priority", "rush", "express"}
TEMPLATE_CSV = ("customer_name,phone,address,notes,window_date,window_start,window_end,priority,delivery_note\n"
                "Jane Doe,415 555 0123,1 Market St,Leave at reception,,,,,\n"
                "Sam Lee,+14155550188,450 Golden Gate Ave,Ring twice,,14:00,16:00,yes,Gate code 2468\n")
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
                        window_date=d.get("window_date") or None, merchant_id=row["merchant_id"], created_at=now_iso(),
                        priority=(d.get("priority") or "").strip().lower() in TRUTHY)
            stop.fee = compute_fee(stop.model_dump(), await get_pricing(row["merchant_id"]))
            note, source = await remembered_note(row["merchant_id"], stop.customer_phone)
            csv_note = " ".join((d.get("delivery_note") or "").split())[:200]
            if csv_note:  # a delivery_note column also updates the store's saved note for this customer
                key = {"merchant_id": row["merchant_id"], "phone": stop.customer_phone}
                await db.saved_notes.update_one(key, {"$set": {"note": csv_note, "updated_at": now_iso()},
                                                      "$setOnInsert": {**key, "id": str(uuid.uuid4()), "created_at": now_iso()}}, upsert=True)
                note, source = csv_note, "saved"
            if note:  # regular customer: prefill their delivery note (they can still change it)
                stop.customer_note, stop.customer_note_source, stop.customer_note_at = note, source, now_iso()
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


# ---------------------------------------------------------------- auth (admin + merchant accounts, JWT bearer)
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = "HS256"
SESSION_HOURS = 12
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_MIN = 15
DEMO_MERCHANT_USERS = [("mer-1", "bakery@dashlocal.app", "Bakery2026"), ("mer-2", "books@dashlocal.app", "Books2026")]
DEMO_DRIVER_PINS = {"drv-1": "482913", "drv-2": "730516", "drv-3": "264871"}


def hash_pw(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt(12)).decode()


def check_pw(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode(), h.encode())
    except ValueError:
        return False


DUMMY_HASH = hash_pw(secrets.token_hex(8))  # equalises timing for unknown emails


def issue_token(sub: str, role: str, purpose: str = "session", minutes: int = SESSION_HOURS * 60, **extra) -> str:
    now = datetime.now(timezone.utc)
    return pyjwt.encode({"sub": sub, "role": role, "purpose": purpose, "iat": now, "exp": now + timedelta(minutes=minutes), **extra},
                        JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str, purpose: str) -> dict:
    try:
        p = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG], options={"require": ["exp", "iat", "sub", "role", "purpose"]})
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired — sign in again")
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "Invalid sign-in token")
    if p["purpose"] != purpose:
        raise HTTPException(401, "Invalid sign-in token")
    return p


async def current_user(request: Request) -> dict:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer ") or not h[7:].strip():
        raise HTTPException(401, "Sign in required")
    p = decode_token(h[7:].strip(), "session")
    u = await db.users.find_one({"id": p["sub"], "disabled": {"$ne": True}}, {"_id": 0, "password_hash": 0, "pin_hash": 0, "pw_nonce": 0})
    if not u or u["role"] != p["role"]:
        raise HTTPException(401, "Sign in required")
    return u


async def admin_user(u: dict = Depends(current_user)) -> dict:
    if u["role"] != "admin":
        raise HTTPException(403, "Admin sign-in required")
    return u


def check_merchant_access(u: dict, merchant_id: str):
    if u["role"] != "admin" and u.get("merchant_id") != merchant_id:
        raise HTTPException(403, "You can only see your own store")


async def merchant_access(merchant_id: str, u: dict = Depends(current_user)) -> dict:
    check_merchant_access(u, merchant_id)
    return u


async def seed_users():
    info = await db.users.index_information()
    if "email_1" in info and "partialFilterExpression" not in info["email_1"]:
        await db.users.drop_index("email_1")  # drivers have no email: uniqueness only applies to real emails
    await db.users.create_index("email", unique=True, partialFilterExpression={"email": {"$type": "string"}})
    await db.users.create_index("driver_id", unique=True, partialFilterExpression={"driver_id": {"$type": "string"}})
    for did, pin in DEMO_DRIVER_PINS.items():
        if not await db.users.find_one({"role": "driver", "driver_id": did}):
            await db.users.insert_one({"id": str(uuid.uuid4()), "role": "driver", "driver_id": did, "pin_hash": hash_pw(pin),
                                       "disabled": False, "created_at": now_iso()})
    email, pw = os.environ["ADMIN_EMAIL"].strip().lower(), os.environ["ADMIN_PASSWORD"]
    admin = await db.users.find_one({"email": email})
    if not admin:
        await db.users.insert_one({"id": str(uuid.uuid4()), "email": email, "password_hash": hash_pw(pw), "role": "admin",
                                   "name": "Dispatch Admin", "merchant_id": None, "disabled": False, "created_at": now_iso()})
    elif not check_pw(pw, admin["password_hash"]):
        await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_pw(pw)}})  # .env is the source of truth
    for mid, em, p in DEMO_MERCHANT_USERS:
        if not await db.users.find_one({"email": em}):
            await db.users.insert_one({"id": str(uuid.uuid4()), "email": em, "password_hash": hash_pw(p), "role": "merchant",
                                       "name": None, "merchant_id": mid, "disabled": False, "created_at": now_iso()})


class LoginIn(BaseModel):
    email: str
    password: str


def client_ip(request: Request) -> str:
    # the ingress appends the real client IP as the LAST X-Forwarded-For hop; earlier hops are client-controlled
    return request.headers.get("x-forwarded-for", "").split(",")[-1].strip() or (request.client.host if request.client else "-")


async def check_lockout(limits: List[tuple]) -> dict:
    """Raise 429 while any key is locked; returns the live attempt docs (old failures expire)."""
    now = datetime.now(timezone.utc)
    atts = {}
    for key, _ in limits:
        att = await db.login_attempts.find_one({"key": key}, {"_id": 0})
        if att and att.get("locked_until") and datetime.fromisoformat(att["locked_until"]) > now:
            mins = max(1, math.ceil((datetime.fromisoformat(att["locked_until"]) - now).total_seconds() / 60))
            raise HTTPException(429, f"Too many attempts — try again in {mins} min")
        if att and datetime.fromisoformat(att["updated_at"]) < now - timedelta(minutes=LOGIN_LOCK_MIN):
            att = None
        atts[key] = att
    return atts


async def record_failure(limits: List[tuple], atts: dict):
    now = datetime.now(timezone.utc)
    for key, cap in limits:
        fails = (atts.get(key) or {}).get("fails", 0) + 1
        upd = {"key": key, "fails": fails, "updated_at": now.isoformat(), "locked_until": None}
        if fails >= cap:
            upd.update(fails=0, locked_until=(now + timedelta(minutes=LOGIN_LOCK_MIN)).isoformat())
        await db.login_attempts.update_one({"key": key}, {"$set": upd}, upsert=True)


async def clear_lockout(limits: List[tuple]):
    await db.login_attempts.delete_many({"key": {"$in": [k for k, _ in limits if not k.startswith("drvip:")]}})


class DriverLoginIn(BaseModel):
    phone: str
    pin: str


PIN_RE = re.compile(r"^\d{6}$")
WEAK_PINS = {"123456", "654321", "123123", "121212", "112233", "000000"}


@api_router.post("/auth/driver-login")
async def driver_login(body: DriverLoginIn, request: Request):
    """Driver sign-in with phone + 6-digit PIN. A PIN has only 1M combinations, so limits are tight:
    5 tries per phone per device IP, 10 per phone from anywhere, 30 per IP across all phones."""
    phone = normalize_phone(body.phone) or body.phone.strip()
    ip = client_ip(request)
    limits = [(f"drv:{phone}:{ip}", 5), (f"drv:{phone}:*", 10), (f"drvip:{ip}", 30)]
    atts = await check_lockout(limits)
    d = await db.drivers.find_one({"phone": phone}, {"_id": 0})
    u = await db.users.find_one({"role": "driver", "driver_id": d["id"]}, {"_id": 0}) if d else None
    ok = check_pw(body.pin.strip(), (u or {}).get("pin_hash") or DUMMY_HASH) and bool(PIN_RE.match(body.pin.strip()))
    if not (d and u and ok) or u.get("disabled"):
        await record_failure(limits, atts)
        raise HTTPException(401, "Wrong phone number or PIN")
    await clear_lockout(limits)
    await db.users.update_one({"id": u["id"]}, {"$set": {"last_login_at": now_iso()}})
    await remember_public_base(request)
    user = {"id": u["id"], "role": "driver", "driver_id": d["id"], "driver_name": d["name"], "phone": d["phone"], "email": None, "merchant_id": None}
    return {"token": issue_token(u["id"], "driver"), "user": user}


# ---- shop testimonials for the public landing page (admin-managed)
class TestimonialIn(BaseModel):
    quote: str
    person: Optional[str] = None
    shop: str
    published: bool = True


def clean_testimonial(body: TestimonialIn) -> dict:
    quote, shop = " ".join(body.quote.split())[:400], " ".join(body.shop.split())[:80]
    if not quote or not shop:
        raise HTTPException(400, "A quote and the shop name are required")
    return {"quote": quote, "shop": shop, "person": (" ".join((body.person or "").split())[:60] or None), "published": body.published}


@api_router.get("/testimonials")
async def public_testimonials():
    """Published shop quotes for the landing page (newest first, max 6)."""
    return await db.testimonials.find({"published": True}, {"_id": 0, "id": 1, "quote": 1, "person": 1, "shop": 1}).sort("created_at", -1).to_list(6)


@api_router.get("/admin/testimonials", dependencies=ADMIN_ONLY)
async def admin_testimonials():
    return await db.testimonials.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)


@api_router.post("/admin/testimonials", dependencies=ADMIN_ONLY)
async def create_testimonial(body: TestimonialIn):
    doc = {"id": str(uuid.uuid4()), **clean_testimonial(body), "created_at": now_iso(), "updated_at": now_iso()}
    await db.testimonials.insert_one(dict(doc))
    return doc


@api_router.put("/admin/testimonials/{tid}", dependencies=ADMIN_ONLY)
async def update_testimonial(tid: str, body: TestimonialIn):
    res = await db.testimonials.update_one({"id": tid}, {"$set": {**clean_testimonial(body), "updated_at": now_iso()}})
    if not res.matched_count:
        raise HTTPException(404, "Review not found")
    return await db.testimonials.find_one({"id": tid}, {"_id": 0})


@api_router.delete("/admin/testimonials/{tid}", dependencies=ADMIN_ONLY)
async def delete_testimonial(tid: str):
    if not (await db.testimonials.delete_one({"id": tid})).deleted_count:
        raise HTTPException(404, "Review not found")
    return {"ok": True}



class PinIn(BaseModel):
    pin: str


@api_router.get("/admin/driver-accounts", dependencies=ADMIN_ONLY)
async def driver_accounts():
    users = {u["driver_id"]: u for u in await db.users.find({"role": "driver"}, {"_id": 0, "pin_hash": 0}).to_list(500)}
    return [{"id": d["id"], "name": d["name"], "phone": d["phone"], "vehicle": d["vehicle"],
             "has_pin": d["id"] in users, "last_login_at": (users.get(d["id"]) or {}).get("last_login_at")}
            for d in await db.drivers.find({}, {"_id": 0}).sort("id", 1).to_list(500)]


@api_router.put("/admin/drivers/{driver_id}/pin", dependencies=ADMIN_ONLY)
async def set_driver_pin(driver_id: str, body: PinIn):
    pin = body.pin.strip()
    if not PIN_RE.match(pin):
        raise HTTPException(400, "PIN must be exactly 6 digits")
    if pin in WEAK_PINS or len(set(pin)) == 1:
        raise HTTPException(400, "PIN is too easy to guess — pick another")
    d = await db.drivers.find_one({"id": driver_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Driver not found")
    await db.users.update_one({"role": "driver", "driver_id": driver_id},
                              {"$set": {"pin_hash": hash_pw(pin), "disabled": False},
                               "$setOnInsert": {"id": str(uuid.uuid4()), "role": "driver", "driver_id": driver_id, "created_at": now_iso()}},
                              upsert=True)
    await db.login_attempts.delete_many({"key": {"$regex": f"^drv:{re.escape(d['phone'])}:"}})
    return {"ok": True}


@api_router.post("/auth/login")
async def login(body: LoginIn, request: Request):
    email = body.email.strip().lower()
    ip = client_ip(request)
    # per email+IP (5 tries) and per email from anywhere (20 tries), so rotating IPs cannot brute-force one account
    limits = [(f"{email}:{ip}", LOGIN_MAX_FAILS), (f"{email}:*", LOGIN_MAX_FAILS * 4)]
    atts = await check_lockout(limits)
    u = await db.users.find_one({"email": email}, {"_id": 0})
    ok = check_pw(body.password, u["password_hash"] if u else DUMMY_HASH)
    if not u or not ok or u.get("disabled"):
        await record_failure(limits, atts)
        raise HTTPException(401, "Wrong email or password")
    await clear_lockout(limits)
    await remember_public_base(request)
    user = {k: v for k, v in u.items() if k not in ("password_hash", "pw_nonce")}
    if user.get("merchant_id"):
        user["merchant_name"] = (await get_merchant_or_404(user["merchant_id"]))["name"]
    return {"token": issue_token(u["id"], u["role"]), "user": user}


@api_router.get("/auth/me")
async def auth_me(u: dict = Depends(current_user)):
    if u.get("driver_id"):
        d = await db.drivers.find_one({"id": u["driver_id"]}, {"_id": 0})
        u["driver_name"] = d["name"] if d else None
    if u.get("merchant_id"):
        m = await db.merchants.find_one({"id": u["merchant_id"]}, {"_id": 0})
        u["merchant_name"] = m["name"] if m else None
    return u


class MerchantAccountIn(BaseModel):
    store_name: str
    email: str
    password: str


class PasswordIn(BaseModel):
    password: str


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_password(p: str):
    if len(p) < 8 or len(p.encode()) > 72:
        raise HTTPException(400, "Password must be 8–72 characters")


@api_router.get("/admin/merchant-accounts")
async def merchant_accounts(_: dict = Depends(admin_user)):
    users = await db.users.find({"role": "merchant"}, {"_id": 0, "password_hash": 0, "pw_nonce": 0}).to_list(500)
    out = []
    for m in await db.merchants.find({}, {"_id": 0}).sort("id", 1).to_list(500):
        out.append({**m, "accounts": [x for x in users if x["merchant_id"] == m["id"]],
                    "orders": await db.stops.count_documents({"merchant_id": m["id"]})})
    return out


@api_router.post("/admin/merchant-accounts")
async def create_merchant_account(body: MerchantAccountIn, _: dict = Depends(admin_user)):
    name, email = body.store_name.strip(), body.email.strip().lower()
    if not name:
        raise HTTPException(400, "Store name is required")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email")
    check_password(body.password)
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "An account with this email already exists")
    m = {"id": f"mer-{uuid.uuid4().hex[:8]}", "name": name, "contact": email}
    await db.merchants.insert_one(dict(m))
    user = {"id": str(uuid.uuid4()), "email": email, "role": "merchant", "name": None, "merchant_id": m["id"], "disabled": False, "created_at": now_iso()}
    try:
        await db.users.insert_one({**user, "password_hash": hash_pw(body.password)})
    except DuplicateKeyError:
        await db.merchants.delete_one({"id": m["id"]})
        raise HTTPException(409, "An account with this email already exists")
    return {**m, "accounts": [user], "orders": 0}


@api_router.put("/admin/merchant-accounts/{user_id}/password")
async def reset_merchant_password(user_id: str, body: PasswordIn, _: dict = Depends(admin_user)):
    check_password(body.password)
    res = await db.users.update_one({"id": user_id, "role": "merchant"}, {"$set": {"password_hash": hash_pw(body.password)}})
    if not res.matched_count:
        raise HTTPException(404, "Merchant account not found")
    return {"ok": True}


# ---------------------------------------------------------------- merchant delivery report (CSV)
REPORT_RANGES = {"today": 0, "7d": 6, "30d": 29, "all": None, "lastweek": "lastweek"}
REPORT_COLUMNS = ["tracking_code", "customer_name", "customer_phone", "address", "zone", "status", "order_created", "delivery_day",
                  "window", "driver", "arrived_at", "delivered_at", "on_time", "fail_reason", "proof_types", "proof_link",
                  "rating", "rating_comment"]


class ReportIn(BaseModel):
    range: str = "7d"


@api_router.post("/merchants/{merchant_id}/report-link")
async def report_link(merchant_id: str, body: ReportIn, request: Request, u: dict = Depends(merchant_access)):
    """Short-lived download link (5 min) so the CSV can open in a browser tab without a sign-in header."""
    await get_merchant_or_404(merchant_id)
    if body.range not in REPORT_RANGES:
        raise HTTPException(400, f"range must be one of {', '.join(REPORT_RANGES)}")
    tok = issue_token(u["id"], u["role"], purpose="report", minutes=5, mid=merchant_id, rng=body.range)
    await remember_public_base(request)
    return {"path": f"/api/merchants/{merchant_id}/report.csv?token={tok}", "expires_in": 300}


def local_fmt(iso: Optional[str]) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y-%m-%d %H:%M") if iso else ""


def report_window(rng: Optional[str]):
    """(since, until) depot-local YYYY-MM-DD bounds for a report range; None = open-ended."""
    today = datetime.now(TZ).date()
    if rng == "lastweek":  # previous Monday..Sunday
        start = today - timedelta(days=today.weekday() + 7)
        return start.isoformat(), (start + timedelta(days=6)).isoformat()
    back = REPORT_RANGES.get(rng, 6)
    return ((today - timedelta(days=back)).isoformat() if back is not None else None), None


async def remember_public_base(request: Request):
    """Scheduled emails have no request to read the app's public URL from, so keep the latest one."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    if host and not host.startswith(("localhost", "127.", "0.0.0.0")):
        proto = request.headers.get("x-forwarded-proto") or "https"
        await db.settings.update_one({"key": "public_base"}, {"$set": {"key": "public_base", "value": f"{proto}://{host}"}}, upsert=True)


def csv_safe(v) -> str:
    """Stop spreadsheet formula injection from customer-written text."""
    v = "" if v is None else str(v)
    return f"'{v}" if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@api_router.get("/merchants/{merchant_id}/report.csv")
async def report_csv(merchant_id: str, token: str, request: Request):
    p = decode_token(token, "report")
    if p.get("mid") != merchant_id:
        raise HTTPException(403, "This link is for a different store")
    m = await get_merchant_or_404(merchant_id)
    since, until = report_window(p.get("rng"))
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto") or ("http" if host.startswith(("localhost", "127.")) else "https")
    base = f"{proto}://{host}"
    routes = {r["id"]: r for r in await db.routes.find({}, {"_id": 0, "id": 1, "driver_id": 1, "service_date": 1}).to_list(2000)}
    drivers = {d["id"]: d["name"] for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(REPORT_COLUMNS)
    for d in await db.stops.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", 1).to_list(5000):
        st = Stop(**d)
        activity = st.delivered_at or st.created_at
        unfinished = st.status in ("pending", "arrived")  # open orders always appear, whatever the range
        act_day = datetime.fromisoformat(activity).astimezone(TZ).date().isoformat() if activity else None
        if since and not unfinished and (not act_day or act_day < since or (until and act_day > until)):
            continue
        r = routes.get(st.route_id) if st.route_id else None
        link = ""
        if st.status == "delivered":
            link = base + (await share_proof(st.id))["path"]
        on_time = delivered_on_time(st)
        proofs = [k for k, v in (("photo", st.pod_photo_path), ("signature", st.pod_signature), ("qr", st.pod_qr_verified)) if v]
        status = st.status if st.route_id else ("scheduled" if st.window_date and st.window_date > today_str() else "awaiting dispatch")
        w.writerow([st.tracking_code, csv_safe(st.customer_name), st.customer_phone, csv_safe(st.address), st.zone, status, local_fmt(st.created_at),
                    (r or {}).get("service_date") or st.window_date or "", f"{st.window_start}-{st.window_end}" if st.window_end else "",
                    drivers.get((r or {}).get("driver_id"), ""), local_fmt(st.arrived_at), local_fmt(st.delivered_at),
                    "" if on_time is None else ("yes" if on_time else "no"), csv_safe(st.fail_reason), "+".join(proofs), link,
                    st.rating or "", csv_safe(st.rating_comment)])
    slug = re.sub(r"[^a-z0-9]+", "-", m["name"].lower()).strip("-")
    fname = f"{slug}-deliveries-{p.get('rng')}-{today_str()}.csv"
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@api_router.get("/merchant-template.csv")
async def merchant_template():
    return Response(TEMPLATE_CSV, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="dashlocal-orders-template.csv"'})


@api_router.get("/merchants")
async def list_merchants(_: dict = Depends(admin_user)):
    out = []
    for m in await db.merchants.find({}, {"_id": 0}).sort("id", 1).to_list(100):
        out.append({**m, "orders": await db.stops.count_documents({"merchant_id": m["id"]}),
                    "delivered": await db.stops.count_documents({"merchant_id": m["id"], "status": "delivered"}),
                    "flagged": await db.import_rows.count_documents({"merchant_id": m["id"], "status": {"$in": list(FLAGGED)}})})
    return out


@api_router.get("/merchants/{merchant_id}")
async def merchant_detail(merchant_id: str, _: dict = Depends(merchant_access)):
    m = await get_merchant_or_404(merchant_id)
    orders = await db.stops.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).to_list(500)
    flagged = await db.import_rows.find({"merchant_id": merchant_id, "status": {"$in": list(FLAGGED)}}, {"_id": 0}).sort([("created_at", -1), ("line", 1)]).to_list(500)
    jobs = await db.import_jobs.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).to_list(5)
    return {**m, "orders": [Stop(**o).model_dump() for o in orders], "flagged": flagged, "jobs": jobs,
            "counts": {"orders": len(orders), "pending": sum(1 for o in orders if o["status"] in ("pending", "arrived")),
                       "delivered": sum(1 for o in orders if o["status"] == "delivered"), "failed": sum(1 for o in orders if o["status"] == "failed"),
                       "flagged": len(flagged)}}


class SavedNoteIn(BaseModel):
    phone: str
    note: str


@api_router.get("/merchants/{merchant_id}/saved-notes")
async def list_saved_notes(merchant_id: str, _: dict = Depends(merchant_access)):
    return await db.saved_notes.find({"merchant_id": merchant_id}, {"_id": 0}).sort("updated_at", -1).to_list(1000)


@api_router.put("/merchants/{merchant_id}/saved-notes")
async def upsert_saved_note(merchant_id: str, body: SavedNoteIn, _: dict = Depends(merchant_access)):
    """Save (or replace) a regular customer's delivery note, keyed by phone. Filled into every new order for them."""
    await get_merchant_or_404(merchant_id)
    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Enter a valid phone number")
    note = " ".join(body.note.split())[:200]
    if not note:
        raise HTTPException(400, "Write the note to save")
    key = {"merchant_id": merchant_id, "phone": phone}
    await db.saved_notes.update_one(key, {"$set": {"note": note, "updated_at": now_iso()},
                                          "$setOnInsert": {**key, "id": str(uuid.uuid4()), "created_at": now_iso()}}, upsert=True)
    return await db.saved_notes.find_one(key, {"_id": 0})


@api_router.delete("/merchants/{merchant_id}/saved-notes/{note_id}")
async def delete_saved_note(merchant_id: str, note_id: str, _: dict = Depends(merchant_access)):
    res = await db.saved_notes.delete_one({"merchant_id": merchant_id, "id": note_id})
    if not res.deleted_count:
        raise HTTPException(404, "Saved note not found")
    return {"ok": True}


@api_router.post("/merchants/{merchant_id}/import")
async def import_orders(merchant_id: str, file: UploadFile = File(...), _: dict = Depends(merchant_access)):
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
async def import_status(job_id: str, u: dict = Depends(current_user)):
    job = await db.import_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Import not found")
    check_merchant_access(u, job["merchant_id"])
    return job


class RowFixIn(BaseModel):
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    window_date: Optional[str] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    priority: Optional[str] = None


@api_router.put("/import-rows/{row_id}")
async def fix_import_row(row_id: str, body: RowFixIn, u: dict = Depends(current_user)):
    """Edit a flagged row and try it again straight away."""
    row = await db.import_rows.find_one({"id": row_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Row not found")
    check_merchant_access(u, row["merchant_id"])
    if row["status"] not in FLAGGED:
        raise HTTPException(400, f"Row is already {row['status']}")
    data = {**row["data"], **{k: v.strip() for k, v in body.model_dump().items() if v is not None}}
    await db.import_rows.update_one({"id": row_id}, {"$set": {"data": data}})
    out = await process_row({**row, "data": data})
    await refresh_job(row["job_id"], finished=(await db.import_jobs.find_one({"id": row["job_id"]}, {"_id": 0, "status": 1}))["status"] == "done")
    return out


@api_router.delete("/import-rows/{row_id}")
async def dismiss_import_row(row_id: str, u: dict = Depends(current_user)):
    row = await db.import_rows.find_one({"id": row_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Row not found")
    check_merchant_access(u, row["merchant_id"])
    if row["status"] not in FLAGGED:
        raise HTTPException(400, f"Row is already {row['status']}")
    await db.import_rows.update_one({"id": row_id}, {"$set": {"status": "dismissed"}})
    await refresh_job(row["job_id"])
    return {"ok": True}


# ---------------------------------------------------------------- weekly merchant report emails (Emergent managed Resend)
EMAIL_BASE_URL = "https://integrations.emergentagent.com"  # constant on purpose: survives deployment
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "DashLocal")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")
TEST_INBOX = "delivered@resend.dev"  # seeded demo store logins are not real inboxes
WEEKLY_SEND_HOUR = 8  # Monday 08:00 depot (SF) time
WEEKLY_LINK_DAYS = 7
SEND_NOW_COOLDOWN_MIN = 5

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} ≠ real link host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str) -> str:
    """Returns the provider id; raises RuntimeError with a short reason on failure."""
    _assert_safe_email(subject, html)
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    if EMAIL_REPLY_TO:
        payload["contact_email"] = EMAIL_REPLY_TO
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(f"{EMAIL_BASE_URL}/api/v1/email/send", headers={"X-Email-Key": EMAIL_KEY}, json=payload)
        resp.raise_for_status()
        return resp.json().get("id") or ""
    except httpx.HTTPStatusError as e:
        try:
            j = e.response.json()
        except ValueError:
            j = {}
        if j.get("code") in ("insufficient_credits", "integration_disabled", "daily_limit_reached"):
            logger.warning(f"Email paused: {j.get('code')} ({e.response.status_code}): {j.get('message', '')}")
            raise RuntimeError(f"Email paused: {j.get('code')}")
        logger.error(f"Email send failed: {e.response.status_code} {e.response.text[:300]}")
        raise RuntimeError(f"Email provider error {e.response.status_code}")
    except httpx.HTTPError as e:
        logger.error(f"Email send error: {e}")
        raise RuntimeError("Email service unreachable")


async def weekly_summary(merchant_id: str, since: str, until: str) -> dict:
    rows = [Stop(**d) for d in await db.stops.find({"merchant_id": merchant_id}, {"_id": 0}).to_list(5000)]

    def in_week(st: Stop) -> bool:
        a = st.delivered_at or st.created_at
        return bool(a) and since <= datetime.fromisoformat(a).astimezone(TZ).date().isoformat() <= until

    week = [x for x in rows if in_week(x)]
    delivered = [x for x in week if x.status == "delivered"]
    windowed = [x for x in delivered if x.window_end]
    rated = [x.rating for x in delivered if x.rating]
    return {"orders": len([x for x in week if x.created_at and since <= datetime.fromisoformat(x.created_at).astimezone(TZ).date().isoformat() <= until]),
            "delivered": len(delivered), "failed": sum(1 for x in week if x.status == "failed"),
            "open": sum(1 for x in rows if x.status in ("pending", "arrived")),
            "on_time_rate": round(sum(1 for x in windowed if delivered_on_time(x)) / len(windowed) * 100) if windowed else None,
            "avg_rating": avg(rated), "complaints": sum(1 for r in rated if r <= 2)}


def cad(n: float) -> str:
    return f"CA${n:,.2f}"


def weekly_bill_html(b: dict, link: str) -> str:
    e = escape
    paid = b.get("payment")
    rows = [("Drops billed", str(b["drops"])), ("Delivery fees", cad(b["base_total"])), ("Distance", cad(b["distance_total"])),
            ("Priority surcharge", cad(b["priority_total"])), (f"Tax {b['tax_pct']}%", cad(b["tax"])),
            ("Total for the week", cad(b["total"]) + (" (paid)" if paid else ""))]
    owed = b.get("outstanding") or {"total": 0, "weeks": []}
    if owed["total"]:
        rows.append((f"Total due ({len(owed['weeks'])} unpaid week{'s' if len(owed['weeks']) != 1 else ''})", cad(owed["total"])))
    trs = "".join(f'<tr><td style="padding:8px 0;border-bottom:1px solid #e5e5e5;color:#555">{e(k)}</td>'
                  f'<td style="padding:8px 0;border-bottom:1px solid #e5e5e5;text-align:right;font-weight:bold">{e(v)}</td></tr>' for k, v in rows)
    return (f'<p style="margin:28px 0 8px;font-size:16px;font-weight:bold">Your statement</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{trs}</table>'
            f'<p style="margin:16px 0 8px"><a href="{e(link, quote=True)}" style="display:inline-block;background:#0a0a0a;color:#ffffff;padding:12px 20px;text-decoration:none;font-weight:bold">Download the statement CSV</a></p>'
            f'<p style="margin:0;color:#777;font-size:12px">Amounts in Canadian dollars. Payment is arranged directly with dispatch — we never ask for card details by email.</p>')


def weekly_invoices_html(b: dict, portal: str) -> str:
    """Weekly email section summarising the per-delivery invoices (fee + HST) and what is still outstanding."""
    e = escape
    return (f'<p style="margin:24px 0 8px;font-size:16px;font-weight:bold">Invoices this week</p>'
            f'<p style="margin:0;color:#555">{b["count"]} invoice{"s" if b["count"] != 1 else ""} · subtotal {e(cad(b["subtotal"]))} · HST {e(cad(b["hst"]))} · '
            f'<b>total {e(cad(b["total"]))}</b></p>'
            f'<p style="margin:8px 0 0;font-weight:bold">Outstanding: {e(cad(b["outstanding"]))} ({b["unpaid_count"]} invoice{"s" if b["unpaid_count"] != 1 else ""})</p>'
            f'<p style="margin:12px 0 0"><a href="{e(portal, quote=True)}" style="color:#e11d1d;font-weight:bold">View and download your invoices</a></p>')


def weekly_email_html(store: str, since: str, until: str, s: dict, link: str, bill: Optional[dict] = None, statement: Optional[str] = None) -> str:
    e = escape
    rng = f"{nice_date(since)} – {nice_date(until)}"
    cells = [("Delivered", s["delivered"]), ("Failed", s["failed"]),
             ("On time", "—" if s["on_time_rate"] is None else f"{s['on_time_rate']}%"),
             ("Avg rating", "—" if s["avg_rating"] is None else f"{s['avg_rating']} / 5"),
             ("Complaints (1–2★)", s["complaints"]), ("New orders", s["orders"]), ("Still open", s["open"])]
    rows = "".join(f'<tr><td style="padding:8px 0;border-bottom:1px solid #e5e5e5;color:#555">{e(k)}</td>'
                   f'<td style="padding:8px 0;border-bottom:1px solid #e5e5e5;text-align:right;font-weight:bold">{e(str(v))}</td></tr>' for k, v in cells)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4"><tr><td align="center" style="padding:24px">'
            f'<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border:2px solid #0a0a0a;font-family:Arial,sans-serif;color:#0a0a0a">'
            f'<tr><td style="background:#0a0a0a;color:#ffffff;padding:20px 24px;font-size:20px;font-weight:bold;letter-spacing:1px">{e(EMAIL_FROM_NAME.upper())} · WEEKLY DELIVERY REPORT</td></tr>'
            f'<tr><td style="padding:24px">'
            f'<p style="margin:0 0 4px;font-size:18px;font-weight:bold">{e(store)}</p>'
            f'<p style="margin:0 0 16px;color:#555">Week of {e(rng)} (San Francisco time)</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'
            f'<p style="margin:24px 0 8px"><a href="{e(link, quote=True)}" style="display:inline-block;background:#e11d1d;color:#ffffff;padding:12px 20px;text-decoration:none;font-weight:bold">Download the full CSV report</a></p>'
            f'<p style="margin:0;color:#777;font-size:12px">The download link works for {WEEKLY_LINK_DAYS} days. It lists every drop with status, times, proof-of-delivery links and ratings.</p>'
            + (weekly_invoices_html(bill, statement) if bill and statement else "") +
            f'</td></tr><tr><td style="padding:16px 24px;border-top:1px solid #e5e5e5;color:#888;font-size:12px">'
            f'Sent by {e(EMAIL_FROM_NAME)} every Monday morning. Turn this email off from the Merchant console. We never ask for your password by email.'
            f'</td></tr></table></td></tr></table>')


async def send_weekly_report(merchant_id: str, trigger: str) -> dict:
    """Email last week's summary + a 7-day CSV link to every login of the store. Logged in db.report_emails."""
    m = await get_merchant_or_404(merchant_id)
    since, until = report_window("lastweek")
    log = {"id": str(uuid.uuid4()), "merchant_id": merchant_id, "trigger": trigger, "week_start": since, "week_end": until,
           "created_at": now_iso(), "recipients": [], "status": "sent", "error": None}
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    users = await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0}).to_list(20)
    if not base:
        log.update(status="failed", error="App address unknown yet — sign in once so links can be built")
    elif not users:
        log.update(status="failed", error="Store has no login email")
    else:
        tok = issue_token(f"weekly:{merchant_id}", "merchant", purpose="report", minutes=WEEKLY_LINK_DAYS * 24 * 60, mid=merchant_id, rng="lastweek")
        link = f"{base}/api/merchants/{merchant_id}/report.csv?token={tok}"
        summary = await weekly_summary(merchant_id, since, until)
        week_invs = await db.invoices.find({"merchant_id": merchant_id, "date": {"$gte": since, "$lte": until}}, {"_id": 0}).to_list(2000)
        owed = invoice_summary(await db.invoices.find({"merchant_id": merchant_id}, {"_id": 0, "status": 1, "total": 1}).to_list(10000))
        bill = {"count": len(week_invs), "subtotal": round(sum(i["subtotal"] for i in week_invs), 2), "hst": round(sum(i["hst"] for i in week_invs), 2),
                "total": round(sum(i["total"] for i in week_invs), 2), "outstanding": owed["outstanding"], "unpaid_count": owed["unpaid_count"]}
        html = weekly_email_html(m["name"], since, until, summary, link, bill, f"{base}/sign-in/merchant")
        subject = f"{m['name']}: weekly delivery report & invoices ({nice_date(since)} – {nice_date(until)}) · {cad(bill['outstanding'])} outstanding"
        log["bill_total"], log["total_due"] = bill["total"], bill["outstanding"]
        errors = []
        for u in users:
            to = TEST_INBOX if u.get("email") in {em for _, em, _ in DEMO_MERCHANT_USERS} else u["email"]
            try:
                eid = await send_email(to=to, subject=subject, html=html)
                log["recipients"].append({"login": u["email"], "to": to, "email_id": eid, "status": "sent"})
            except Exception as ex:
                errors.append(str(ex))
                log["recipients"].append({"login": u["email"], "to": to, "email_id": None, "status": "failed"})
        if errors:
            log.update(status="failed" if len(errors) == len(users) else "partial", error=errors[0])
        log["summary"] = summary
    await db.report_emails.insert_one(dict(log))
    return log


def next_weekly_send(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(TZ)
    d = now.date() + timedelta(days=(7 - now.weekday()) % 7)
    at = datetime.combine(d, time_cls(WEEKLY_SEND_HOUR, 0), TZ)
    return at if at > now else at + timedelta(days=7)


async def weekly_scheduler():
    """Every minute: on Monday from 08:00 SF, send each opted-in store's report once per ISO week."""
    while True:
        try:
            now = datetime.now(TZ)
            if now.weekday() == 0 and now.hour >= WEEKLY_SEND_HOUR:
                wk = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
                for m in await db.merchants.find({"weekly_email": {"$ne": False}}, {"_id": 0, "id": 1}).to_list(500):
                    claimed = await db.merchants.update_one({"id": m["id"], "weekly_sent_week": {"$ne": wk}}, {"$set": {"weekly_sent_week": wk}})
                    if claimed.modified_count:
                        log = await send_weekly_report(m["id"], "scheduled")
                        tries = await db.report_emails.count_documents({"merchant_id": m["id"], "trigger": "scheduled", "week_start": log["week_start"]})
                        if log["status"] == "failed" and tries < 3:  # retry next minute, at most 3 tries a week
                            await db.merchants.update_one({"id": m["id"]}, {"$unset": {"weekly_sent_week": ""}})
        except Exception as e:
            logger.error(f"Weekly report scheduler error: {e}")
        await asyncio.sleep(60)


class WeeklyEmailIn(BaseModel):
    enabled: bool


async def weekly_email_status(merchant_id: str) -> dict:
    m = await get_merchant_or_404(merchant_id)
    users = await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0, "email": 1}).to_list(20)
    last = await db.report_emails.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).to_list(1)
    return {"enabled": m.get("weekly_email", True), "recipients": [u["email"] for u in users],
            "next_send": next_weekly_send().isoformat(), "last": last[0] if last else None}


@api_router.get("/merchants/{merchant_id}/weekly-email")
async def get_weekly_email(merchant_id: str, _: dict = Depends(merchant_access)):
    return await weekly_email_status(merchant_id)


@api_router.put("/merchants/{merchant_id}/weekly-email")
async def set_weekly_email(merchant_id: str, body: WeeklyEmailIn, _: dict = Depends(merchant_access)):
    await get_merchant_or_404(merchant_id)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"weekly_email": body.enabled}})
    return await weekly_email_status(merchant_id)


@api_router.post("/merchants/{merchant_id}/weekly-email/send-now")
async def send_weekly_now(merchant_id: str, request: Request, _: dict = Depends(merchant_access)):
    """Send last week's report right away to the store's own login emails (rate-limited)."""
    await remember_public_base(request)
    recent = await db.report_emails.find_one({"merchant_id": merchant_id, "trigger": "manual", "status": {"$ne": "failed"},
                                              "created_at": {"$gt": (datetime.now(timezone.utc) - timedelta(minutes=SEND_NOW_COOLDOWN_MIN)).isoformat()}})
    if recent:
        raise HTTPException(429, f"A report was just sent — try again in a few minutes")
    log = await send_weekly_report(merchant_id, "manual")
    return {**(await weekly_email_status(merchant_id)), "sent": log}


# ---------------------------------------------------------------- merchant problem alerts (failed / running late)
LATE_GRACE_MIN = 5  # predicted arrival must be this far past the window end before we alert


async def maybe_late_alerts(route: Route, stops: List[Stop]):
    """Once per stop: email the merchant when the live ETA passes the end of the delivery window."""
    if route.status != "active" or route.service_date != today_str() or not route.driver_id:
        return
    open_ = [s for s in stops if s.status in ("pending", "arrived")]
    cand = {s.id for s in open_ if s.merchant_id and s.window_end and not s.merchant_late_alert_at}
    if not cand:
        return
    d = await db.drivers.find_one({"id": route.driver_id}, {"_id": 0})
    start = {"lat": d["lat"], "lng": d["lng"]} if d else DEPOT
    base = plan_base(route.service_date)
    for s, x in zip(open_, schedule(start, [o.model_dump() for o in open_], base)):
        if s.id in cand and x["late_min"] > LATE_GRACE_MIN:
            claimed = await db.stops.update_one({"id": s.id, "merchant_late_alert_at": None}, {"$set": {"merchant_late_alert_at": now_iso()}})
            if claimed.modified_count:
                eta = (base + timedelta(minutes=x["arrive_min"])).isoformat()
                spawn(send_merchant_alert(s, "late", eta=eta, late_min=round(x["late_min"])))


async def late_alert_watcher():
    """Every minute: re-check today's active routes so late alerts go out even when the driver app is idle."""
    while True:
        try:
            for r in await db.routes.find({"status": "active", "service_date": today_str()}, {"_id": 0}).to_list(200):
                route = Route(**r)
                await maybe_late_alerts(route, await get_stops(route.id))
        except Exception as e:
            logger.error(f"Late alert watcher error: {e}")
        await asyncio.sleep(60)


ALERT_COPY = {
    "late": ("is running late", "Our live ETA is past the delivery window. The driver is still on the way."),
    "delivered_late": ("was delivered late", "The drop was completed after the end of the delivery window."),
    "failed": ("could not be delivered", "The driver could not complete this drop."),
}


def merchant_alert_html(store: str, st: Stop, kind: str, driver: str, eta: Optional[str], late_min: Optional[int]) -> str:
    e = escape
    head, lead = ALERT_COPY[kind]
    rows = [("Tracking code", st.tracking_code), ("Customer", st.customer_name), ("Address", st.address),
            ("Window", f"{st.window_start}–{st.window_end}" if st.window_end else "—"), ("Driver", driver)]
    if kind == "late":
        rows.append(("New ETA", f"{local_fmt(eta)[-5:]} (about {late_min} min late)"))
    if kind == "delivered_late":
        rows.append(("Delivered at", local_fmt(st.delivered_at)[-5:]))
    if kind == "failed":
        rows.append(("Reason", st.fail_reason or "Not given"))
    trs = "".join(f'<tr><td style="padding:6px 0;border-bottom:1px solid #e5e5e5;color:#555">{e(k)}</td>'
                  f'<td style="padding:6px 0;border-bottom:1px solid #e5e5e5;text-align:right;font-weight:bold">{e(str(v))}</td></tr>' for k, v in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4"><tr><td align="center" style="padding:24px">'
            f'<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border:2px solid #0a0a0a;font-family:Arial,sans-serif;color:#0a0a0a">'
            f'<tr><td style="background:#e11d1d;color:#ffffff;padding:16px 24px;font-size:18px;font-weight:bold;letter-spacing:1px">DROP {e(st.tracking_code)} {e(head.upper())}</td></tr>'
            f'<tr><td style="padding:24px"><p style="margin:0 0 4px;font-size:16px;font-weight:bold">{e(store)}</p>'
            f'<p style="margin:0 0 16px;color:#555">{e(lead)}</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{trs}</table>'
            f'<p style="margin:16px 0 0;color:#777;font-size:12px">Times are San Francisco time. You get one email per affected drop — switch these alerts off in the Merchant console.</p>'
            f'</td></tr></table></td></tr></table>')


async def send_merchant_alert(st: Stop, kind: str, eta: Optional[str] = None, late_min: Optional[int] = None):
    """Email every login of the store about one problem drop. Logged in db.merchant_alerts."""
    log = {"id": str(uuid.uuid4()), "merchant_id": st.merchant_id, "stop_id": st.id, "tracking_code": st.tracking_code,
           "customer_name": st.customer_name, "kind": kind, "eta": eta, "late_min": late_min, "reason": st.fail_reason,
           "created_at": now_iso(), "status": "sent", "error": None, "recipients": []}
    try:
        m = await get_merchant_or_404(st.merchant_id)
        if m.get("problem_alerts") is False:
            log["status"] = "off"
        else:
            users = await db.users.find({"merchant_id": st.merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0}).to_list(20)
            r = await db.routes.find_one({"id": st.route_id}, {"_id": 0, "driver_id": 1}) if st.route_id else None
            d = await db.drivers.find_one({"id": (r or {}).get("driver_id")}, {"_id": 0, "name": 1}) if r else None
            driver = d["name"].split(" ")[0] if d else "—"
            html = merchant_alert_html(m["name"], st, kind, driver, eta, late_min)
            subject = f"{m['name']}: drop {st.tracking_code} {ALERT_COPY[kind][0]}"
            demo = {em for _, em, _ in DEMO_MERCHANT_USERS}
            errors = []
            for u in users:
                to = TEST_INBOX if u["email"] in demo else u["email"]
                try:
                    eid = await send_email(to=to, subject=subject, html=html)
                    log["recipients"].append({"login": u["email"], "to": to, "email_id": eid, "status": "sent"})
                except Exception as ex:
                    errors.append(str(ex))
                    log["recipients"].append({"login": u["email"], "to": to, "email_id": None, "status": "failed"})
            if not users:
                log.update(status="failed", error="Store has no login email")
            elif errors:
                log.update(status="failed" if len(errors) == len(users) else "partial", error=errors[0])
    except Exception as ex:
        logger.error(f"Merchant alert {kind} for {st.tracking_code} failed: {ex}")
        log.update(status="failed", error=str(ex)[:200])
    await db.merchant_alerts.insert_one(dict(log))


class AlertsIn(BaseModel):
    enabled: bool


async def merchant_alerts_status(merchant_id: str) -> dict:
    m = await get_merchant_or_404(merchant_id)
    recent = await db.merchant_alerts.find({"merchant_id": merchant_id}, {"_id": 0, "recipients": 0}).sort("created_at", -1).to_list(10)
    return {"enabled": m.get("problem_alerts", True), "recent": recent}


@api_router.get("/merchants/{merchant_id}/alerts")
async def get_merchant_alerts(merchant_id: str, _: dict = Depends(merchant_access)):
    return await merchant_alerts_status(merchant_id)


@api_router.put("/merchants/{merchant_id}/alerts")
async def set_merchant_alerts(merchant_id: str, body: AlertsIn, _: dict = Depends(merchant_access)):
    await get_merchant_or_404(merchant_id)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"problem_alerts": body.enabled}})
    return await merchant_alerts_status(merchant_id)


# ---------------------------------------------------------------- pricing & weekly merchant bills (CAD, collected outside the app)
PRICING_DEFAULT = {"currency": "CAD", "base_fee": 8.00, "included_km": 5.0, "per_km": 1.25, "priority_fee": 6.00,
                   "charge_failed": True, "tax_pct": 0.0}
PRICING_LIMITS = {"base_fee": 1000, "included_km": 500, "per_km": 100, "priority_fee": 1000, "tax_pct": 30}
PRICING_NAMES = {"base_fee": "Per-delivery fee", "included_km": "Km included", "per_km": "Extra per km",
                 "priority_fee": "Priority surcharge", "tax_pct": "Sales tax %"}
ROAD_FACTOR = 1.3  # straight-line depot→drop distance × this ≈ road distance


async def get_pricing(merchant_id: Optional[str] = None) -> dict:
    """Shared price list, with a store's custom rates layered on top when merchant_id is given."""
    doc = await db.settings.find_one({"key": "pricing"}, {"_id": 0}) or {}
    p = {**PRICING_DEFAULT, **(doc.get("value") or {})}
    if merchant_id:
        m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0, "pricing_override": 1}) or {}
        p.update(m.get("pricing_override") or {})
    return p


def compute_fee(stop: dict, p: dict) -> dict:
    km = round(haversine_m(DEPOT, stop) / 1000 * ROAD_FACTOR, 1)
    dist = round(max(0.0, km - p["included_km"]) * p["per_km"], 2)
    pri = round(p["priority_fee"], 2) if stop.get("priority") else 0.0
    return {"base": round(p["base_fee"], 2), "km": km, "distance_fee": dist, "priority_fee": pri,
            "total": round(p["base_fee"] + dist + pri, 2), "currency": p["currency"], "priced_at": now_iso()}


class PricingIn(BaseModel):
    base_fee: float
    included_km: float
    per_km: float
    priority_fee: float
    charge_failed: bool
    tax_pct: float


@api_router.get("/pricing")
async def read_pricing(u: dict = Depends(current_user)):
    if u["role"] not in ("admin", "merchant"):
        raise HTTPException(403, "Not allowed")
    return await get_pricing(u.get("merchant_id") if u["role"] == "merchant" else None)


@api_router.put("/admin/pricing", dependencies=ADMIN_ONLY)
async def update_pricing(body: PricingIn):
    """New prices apply to orders created (or re-priced) from now on; existing orders keep their price."""
    v = body.model_dump()
    for k, cap in PRICING_LIMITS.items():
        if not (0 <= v[k] <= cap) or v[k] != v[k]:
            raise HTTPException(400, f"{PRICING_NAMES[k]} must be between 0 and {cap}")
        v[k] = round(v[k], 2)
    await db.settings.update_one({"key": "pricing"}, {"$set": {"key": "pricing", "value": v, "updated_at": now_iso()}}, upsert=True)
    return await get_pricing()


def week_bounds(week: Optional[str]) -> tuple:
    """Monday..Sunday (SF) containing `week` (any date), default this week."""
    today = datetime.now(TZ).date()
    try:
        d = date_cls.fromisoformat(week) if week else today
    except ValueError:
        raise HTTPException(400, "week must be YYYY-MM-DD")
    start = d - timedelta(days=d.weekday())
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


async def merchant_bill(merchant_id: str, week: Optional[str]) -> dict:
    p = await get_pricing(merchant_id)
    since, until = week_bounds(week)
    lines, open_count, open_est = [], 0, 0.0
    # only open orders + drops completed around this week (UTC bounds padded a day; exact SF day filter below)
    lo = (date_cls.fromisoformat(since) - timedelta(days=1)).isoformat()
    hi = (date_cls.fromisoformat(until) + timedelta(days=2)).isoformat()
    q = {"merchant_id": merchant_id, "$or": [{"status": {"$in": ["pending", "arrived"]}}, {"delivered_at": {"$gte": lo, "$lt": hi}}]}
    for d in await db.stops.find(q, {"_id": 0}).sort("delivered_at", 1).to_list(20000):
        st = Stop(**d)
        fee = st.fee or compute_fee(d, p)
        if st.status in ("pending", "arrived"):
            open_count += 1
            open_est += fee["total"]
            continue
        if st.status == "failed" and not p["charge_failed"]:
            continue
        day = datetime.fromisoformat(st.delivered_at).astimezone(TZ).date().isoformat() if st.delivered_at else None
        if day and since <= day <= until:
            lines.append({"stop_id": st.id, "tracking_code": st.tracking_code, "customer_name": st.customer_name, "day": day,
                          "status": st.status, "priority": st.priority, **{k: fee[k] for k in ("km", "base", "distance_fee", "priority_fee", "total")}})
    sub = round(sum(x["total"] for x in lines), 2)
    tax = round(sub * p["tax_pct"] / 100, 2)
    this_monday = date_cls.fromisoformat(week_bounds(None)[0])
    pay = await db.bill_payments.find_one({"merchant_id": merchant_id, "week_start": since}, {"_id": 0})
    return {"merchant_id": merchant_id, "week_start": since, "week_end": until, "current_week": since == this_monday.isoformat(),
            "can_mark_paid": until < datetime.now(TZ).date().isoformat(), "payment": pay,
            "currency": p["currency"], "tax_pct": p["tax_pct"], "lines": lines, "drops": len(lines),
            "base_total": round(sum(x["base"] for x in lines), 2), "distance_total": round(sum(x["distance_fee"] for x in lines), 2),
            "priority_total": round(sum(x["priority_fee"] for x in lines), 2), "subtotal": sub, "tax": tax, "total": round(sub + tax, 2),
            "open_orders": {"count": open_count, "estimate": round(open_est, 2)},
            "weeks": [(this_monday - timedelta(weeks=i)).isoformat() for i in range(8)]}


@api_router.get("/merchants/{merchant_id}/bill")
async def get_bill(merchant_id: str, week: Optional[str] = None, _: dict = Depends(merchant_access)):
    await get_merchant_or_404(merchant_id)
    return {**(await merchant_bill(merchant_id, week)), "outstanding": await merchant_outstanding(merchant_id)}


OUTSTANDING_WEEKS = 26


async def merchant_outstanding(merchant_id: str) -> dict:
    """Finished weeks (up to 26 back) with a bill > 0 that are not marked paid."""
    first = await db.stops.find({"merchant_id": merchant_id, "delivered_at": {"$ne": None}}, {"_id": 0, "delivered_at": 1}).sort("delivered_at", 1).to_list(1)
    if not first:
        return {"total": 0.0, "weeks": []}
    this_monday = date_cls.fromisoformat(week_bounds(None)[0])
    start = max(date_cls.fromisoformat(week_bounds(datetime.fromisoformat(first[0]["delivered_at"]).astimezone(TZ).date().isoformat())[0]),
                this_monday - timedelta(weeks=OUTSTANDING_WEEKS))
    paid = {x["week_start"] for x in await db.bill_payments.find({"merchant_id": merchant_id}, {"_id": 0, "week_start": 1}).to_list(1000)}
    weeks, wk = [], start
    while wk < this_monday:
        if wk.isoformat() not in paid:
            b = await merchant_bill(merchant_id, wk.isoformat())
            if b["total"] > 0:
                weeks.append({"week_start": b["week_start"], "week_end": b["week_end"], "total": b["total"]})
        wk += timedelta(weeks=1)
    return {"total": round(sum(x["total"] for x in weeks), 2), "weeks": weeks}


class StatementIn(BaseModel):
    week: Optional[str] = None


@api_router.post("/merchants/{merchant_id}/statement-link")
async def statement_link(merchant_id: str, body: StatementIn, u: dict = Depends(merchant_access)):
    await get_merchant_or_404(merchant_id)
    since, _ = week_bounds(body.week)
    tok = issue_token(u["id"], u["role"], purpose="statement", minutes=5, mid=merchant_id, wk=since)
    return {"path": f"/api/merchants/{merchant_id}/statement.csv?token={tok}"}


@api_router.get("/merchants/{merchant_id}/statement.csv")
async def statement_csv(merchant_id: str, token: str):
    p = decode_token(token, "statement")
    if p.get("mid") != merchant_id:
        raise HTTPException(403, "This link is for a different store")
    m = await get_merchant_or_404(merchant_id)
    b = await merchant_bill(merchant_id, p.get("wk"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{m['name']} — weekly statement {b['week_start']} to {b['week_end']} ({b['currency']})"])
    w.writerow(["date", "tracking_code", "customer_name", "status", "priority", "km", "base_fee", "distance_fee", "priority_fee", "total"])
    for x in b["lines"]:
        w.writerow([x["day"], x["tracking_code"], csv_safe(x["customer_name"]), x["status"], "yes" if x["priority"] else "",
                    x["km"], f"{x['base']:.2f}", f"{x['distance_fee']:.2f}", f"{x['priority_fee']:.2f}", f"{x['total']:.2f}"])
    w.writerow([])
    w.writerow(["", "", "", "", "", "", "", "", "Subtotal", f"{b['subtotal']:.2f}"])
    w.writerow(["", "", "", "", "", "", "", "", f"Tax {b['tax_pct']}%", f"{b['tax']:.2f}"])
    w.writerow(["", "", "", "", "", "", "", "", "Total", f"{b['total']:.2f}"])
    slug = re.sub(r"[^a-z0-9]+", "-", m["name"].lower()).strip("-")
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-statement-{b["week_start"]}.csv"'})


@api_router.get("/admin/billing", dependencies=ADMIN_ONLY)
async def admin_billing(week: Optional[str] = None):
    out = []
    since, until = week_bounds(week)
    for m in await db.merchants.find({}, {"_id": 0}).sort("id", 1).to_list(500):
        b = await merchant_bill(m["id"], week)
        owed = await merchant_outstanding(m["id"])
        out.append({"id": m["id"], "name": m["name"], "drops": b["drops"], "subtotal": b["subtotal"], "tax": b["tax"], "total": b["total"],
                    "open_orders": b["open_orders"], "payment": b["payment"], "can_mark_paid": b["can_mark_paid"],
                    "outstanding": owed["total"], "unpaid_weeks": len(owed["weeks"]), "custom_rates": bool(m.get("pricing_override"))})
    this_monday = date_cls.fromisoformat(week_bounds(None)[0])
    return {"week_start": since, "week_end": until, "stores": out, "total": round(sum(x["total"] for x in out), 2),
            "drops": sum(x["drops"] for x in out), "weeks": [(this_monday - timedelta(weeks=i)).isoformat() for i in range(8)],
            "outstanding": round(sum(x["outstanding"] for x in out), 2), "currency": (await get_pricing())["currency"]}


class PaidIn(BaseModel):
    paid: bool
    note: Optional[str] = None


@api_router.put("/admin/merchants/{merchant_id}/bills/{week}/paid")
async def mark_bill_paid(merchant_id: str, week: str, body: PaidIn, u: dict = Depends(admin_user)):
    """Mark a finished week's bill as paid (payment collected outside the app) or back to unpaid."""
    await get_merchant_or_404(merchant_id)
    since, until = week_bounds(week)
    if until >= datetime.now(TZ).date().isoformat():
        raise HTTPException(400, "This week isn't over yet — bills can be marked paid from Monday")
    if body.paid:
        note = (body.note or "").strip()
        if not note:
            raise HTTPException(400, "Add a payment note (e.g. e-Transfer reference or cheque number)")
        b = await merchant_bill(merchant_id, since)
        await db.bill_payments.update_one({"merchant_id": merchant_id, "week_start": since}, {"$set": {
            "merchant_id": merchant_id, "week_start": since, "amount": b["total"], "paid_at": now_iso(), "by": u["email"],
            "note": note[:200]}}, upsert=True)
    else:
        await db.bill_payments.delete_one({"merchant_id": merchant_id, "week_start": since})
    return await merchant_bill(merchant_id, since)


class StoreRatesIn(BaseModel):
    base_fee: Optional[float] = None
    included_km: Optional[float] = None
    per_km: Optional[float] = None
    priority_fee: Optional[float] = None
    charge_failed: Optional[bool] = None
    tax_pct: Optional[float] = None


async def store_rates(merchant_id: str) -> dict:
    m = await get_merchant_or_404(merchant_id)
    return {"merchant_id": merchant_id, "name": m["name"], "shared": await get_pricing(), "override": m.get("pricing_override") or {},
            "effective": await get_pricing(merchant_id)}


@api_router.get("/admin/merchants/{merchant_id}/pricing", dependencies=ADMIN_ONLY)
async def get_store_rates(merchant_id: str):
    return await store_rates(merchant_id)


@api_router.put("/admin/merchants/{merchant_id}/pricing", dependencies=ADMIN_ONLY)
async def set_store_rates(merchant_id: str, body: StoreRatesIn):
    """Custom rates for one store. Fields left empty (null) follow the shared price list. New orders only."""
    await get_merchant_or_404(merchant_id)
    v = {k: x for k, x in body.model_dump().items() if x is not None}
    for k, cap in PRICING_LIMITS.items():
        if k in v:
            if not (0 <= v[k] <= cap) or v[k] != v[k]:
                raise HTTPException(400, f"{PRICING_NAMES[k]} must be between 0 and {cap}")
            v[k] = round(v[k], 2)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"pricing_override": v}})
    return await store_rates(merchant_id)


class PriorityIn(BaseModel):
    priority: bool


@api_router.put("/merchants/{merchant_id}/orders/{stop_id}/priority")
async def set_order_priority(merchant_id: str, stop_id: str, body: PriorityIn, _: dict = Depends(merchant_access)):
    """Merchant marks an order as priority (or not) before dispatch; the order is re-priced."""
    st = await get_stop_or_404(stop_id)
    if st.merchant_id != merchant_id:
        raise HTTPException(404, "Order not found")
    if st.route_id or st.status != "pending":
        raise HTTPException(400, "Order is already dispatched — ask dispatch to change priority")
    d = {**st.model_dump(), "priority": body.priority}
    await db.stops.update_one({"id": stop_id}, {"$set": {"priority": body.priority, "fee": compute_fee(d, await get_pricing(merchant_id))}})
    return (await get_stop_or_404(stop_id)).model_dump()


@api_router.get("/digest", dependencies=ADMIN_ONLY)
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
async def ws_route(ws: WebSocket, route_id: str, token: str = ""):
    try:
        p = decode_token(token, "session")
        u = await db.users.find_one({"id": p["sub"], "disabled": {"$ne": True}}, {"_id": 0})
        r = await db.routes.find_one({"id": route_id}, {"_id": 0, "driver_id": 1})
        ok = bool(u) and (u["role"] == "admin" or (u["role"] == "driver" and r and r.get("driver_id") == u.get("driver_id")))
    except HTTPException:
        ok = False
    if not ok:
        await ws.close(code=4401)
        return
    room = f"route:{route_id}"
    await hub.join(room, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(room, ws)


# ---------------------------------------------------------------- invoices with HST (one per completed delivery, CAD)
BUSINESS_DEFAULT = {"name": "DashLocal", "address": "Business address — set in Admin › Invoices", "hst_number": "HST # — set in Admin › Invoices", "hst_pct": 13.0}


async def get_business() -> dict:
    doc = await db.settings.find_one({"key": "business"}, {"_id": 0}) or {}
    return {**BUSINESS_DEFAULT, **(doc.get("value") or {})}


async def next_invoice_number() -> str:
    c = await db.counters.find_one_and_update({"key": "invoice"}, {"$inc": {"n": 1}}, upsert=True, return_document=True)
    return f"DL-{c['n']:06d}"


async def create_invoice(stop_id: str) -> Optional[dict]:
    """Idempotent: one invoice per delivered merchant stop. Snapshots business + merchant details at issue time."""
    s = await db.stops.find_one({"id": stop_id}, {"_id": 0})
    if not s or s.get("status") != "delivered" or not s.get("merchant_id"):
        return None
    if existing := await db.invoices.find_one({"stop_id": stop_id}, {"_id": 0}):
        return existing
    m = await db.merchants.find_one({"id": s["merchant_id"]}, {"_id": 0}) or {}
    biz = await get_business()
    fee = s.get("fee") or compute_fee(s, await get_pricing(s["merchant_id"]))
    lines = [{"description": "Same-day delivery fee", "amount": fee["base"]}]
    if fee.get("distance_fee"):
        lines.append({"description": f"Distance ({fee['km']} km)", "amount": fee["distance_fee"]})
    if fee.get("priority_fee"):
        lines.append({"description": "Priority surcharge", "amount": fee["priority_fee"]})
    subtotal = round(sum(x["amount"] for x in lines), 2)
    hst = round(subtotal * biz["hst_pct"] / 100, 2)
    delivered = datetime.fromisoformat(s.get("delivered_at") or now_iso()).astimezone(TZ)
    inv = {"id": str(uuid.uuid4()), "number": None, "merchant_id": s["merchant_id"], "stop_id": stop_id,
           "date": delivered.date().isoformat(), "issued_at": now_iso(), "currency": "CAD",
           "business": biz, "merchant": {"name": m.get("name", ""), "address": m.get("address")},
           "delivery": {"tracking_code": s["tracking_code"], "customer_name": s["customer_name"], "address": s["address"],
                        "delivered_at": s.get("delivered_at"), "delivered_local": delivered.strftime("%Y-%m-%d %H:%M"), "km": fee.get("km"), "priority": bool(s.get("priority"))},
           "lines": lines, "subtotal": subtotal, "hst_pct": biz["hst_pct"], "hst": hst, "total": round(subtotal + hst, 2),
           "status": "unpaid", "paid_at": None, "paid_note": None}
    try:
        inv["number"] = await next_invoice_number()
        await db.invoices.insert_one(dict(inv))
    except DuplicateKeyError:  # a parallel call already issued it
        return await db.invoices.find_one({"stop_id": stop_id}, {"_id": 0})
    return inv


async def backfill_invoices():
    await db.invoices.create_index("stop_id", unique=True)
    await db.invoices.create_index("number", unique=True)
    for s in await db.stops.find({"status": "delivered", "merchant_id": {"$ne": None}}, {"_id": 0, "id": 1}).to_list(5000):
        await create_invoice(s["id"])


def invoice_summary(invs: list) -> dict:
    out = round(sum(i["total"] for i in invs if i["status"] in ("unpaid", "reported")), 2)
    paid = round(sum(i["total"] for i in invs if i["status"] == "paid"), 2)
    return {"outstanding": out, "paid": paid, "unpaid_count": sum(i["status"] in ("unpaid", "reported") for i in invs),
            "reported_count": sum(i["status"] == "reported" for i in invs), "count": len(invs)}


@api_router.get("/merchants/{merchant_id}/invoices")
async def merchant_invoices(merchant_id: str, status: Optional[str] = None, _: dict = Depends(merchant_access)):
    m = await get_merchant_or_404(merchant_id)
    invs = await db.invoices.find({"merchant_id": merchant_id}, {"_id": 0}).sort("number", -1).to_list(2000)
    # "unpaid" = everything still owed (including payments the shop reported but dispatch hasn't confirmed)
    shown = [i for i in invs if not status or i["status"] == status or (status == "unpaid" and i["status"] == "reported")]
    return {**invoice_summary(invs), "invoices": shown[:200], "billing_address": m.get("address"), "currency": "CAD"}


class AddressIn(BaseModel):
    address: str


@api_router.put("/merchants/{merchant_id}/billing-address")
async def set_billing_address(merchant_id: str, body: AddressIn, _: dict = Depends(merchant_access)):
    """Address printed on new invoices (issued invoices keep the address they were issued with)."""
    await get_merchant_or_404(merchant_id)
    addr = " ".join(body.address.split())[:200]
    if not addr:
        raise HTTPException(400, "Enter the billing address")
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"address": addr}})
    await db.invoices.update_many({"merchant_id": merchant_id, "merchant.address": None}, {"$set": {"merchant.address": addr}})
    return {"billing_address": addr}


@api_router.post("/merchants/{merchant_id}/invoices/{invoice_id}/link")
async def invoice_link(merchant_id: str, invoice_id: str, u: dict = Depends(merchant_access)):
    if not await db.invoices.find_one({"id": invoice_id, "merchant_id": merchant_id}, {"_id": 1}):
        raise HTTPException(404, "Invoice not found")
    tok = issue_token(invoice_id, u["role"], purpose="invoice", minutes=5)
    return {"path": f"/api/invoices/{invoice_id}.pdf?token={tok}"}


def invoice_pdf(inv: dict) -> bytes:
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors as rl
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=f"Invoice {inv['number']}", author=inv["business"]["name"])
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=10, leading=14, textColor=rl.HexColor("#333335"))
    small = ParagraphStyle("s", parent=body, fontSize=8.5, textColor=rl.HexColor("#5C5C5E"))
    h = ParagraphStyle("h", parent=body, fontName="Helvetica-Bold", fontSize=22, leading=26)
    e = lambda v: escape(str(v or ""))
    money = lambda n: f"CA${n:,.2f}"
    biz, mer, d = inv["business"], inv["merchant"], inv["delivery"]
    logo = Paragraph('<font name="Helvetica-Bold" size="18" color="#333335">DASH</font><font name="Helvetica-Bold" size="18" color="#D7262E">LOCAL</font>', body)
    head = Table([[logo, Paragraph("INVOICE", ParagraphStyle("r", parent=h, alignment=2))]], colWidths=[3.45 * inch, 3.45 * inch])
    meta = Table([
        [Paragraph(f"<b>{e(biz['name'])}</b><br/>{e(biz['address'])}<br/>HST Reg. No. {e(biz['hst_number'])}", body),
         Paragraph(f"<b>Invoice #</b> {e(inv['number'])}<br/><b>Date</b> {e(inv['date'])}<br/><b>Status</b> {e(inv['status'].upper())}"
                   + (f"<br/><b>Paid</b> {e(inv['paid_at'][:10])}" if inv.get("paid_at") else ""), ParagraphStyle("m", parent=body, alignment=2))],
    ], colWidths=[3.45 * inch, 3.45 * inch])
    bill_to = Paragraph(f"<b>BILL TO</b><br/>{e(mer['name'])}<br/>{e(mer.get('address') or 'Address not on file')}", body)
    deliv = Paragraph(f"<b>DELIVERY</b> {e(d['tracking_code'])} · {e(d['customer_name'])} · {e(d['address'])}<br/>"
                      f"Delivered {e(d.get('delivered_local') or (d.get('delivered_at') or '')[:16].replace('T', ' '))}{' · Priority' if d.get('priority') else ''}", body)
    rows = [["Description", "Amount"]] + [[x["description"], money(x["amount"])] for x in inv["lines"]] + [
        ["Subtotal", money(inv["subtotal"])], [f"HST ({inv['hst_pct']:g}%, Ontario)", money(inv["hst"])], ["Total owing (CAD)", money(inv["total"])]]
    n = len(inv["lines"])
    t = Table(rows, colWidths=[5.2 * inch, 1.7 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 10), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
        ("BACKGROUND", (0, 0), (-1, 0), rl.HexColor("#F5F5F5")), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, rl.HexColor("#333335")), ("LINEABOVE", (0, n + 1), (-1, n + 1), 0.5, rl.HexColor("#E5E5E5")),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 12), ("LINEABOVE", (0, -1), (-1, -1), 1.2, rl.HexColor("#2B8932")),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    doc.build([head, Spacer(1, 14), meta, Spacer(1, 18), bill_to, Spacer(1, 12), deliv, Spacer(1, 16), t, Spacer(1, 24),
               Paragraph("Thank you for delivering with DashLocal. Payment is arranged directly with dispatch; please quote the invoice number.", small)])
    return buf.getvalue()


@api_router.get("/invoices/{invoice_id}.pdf")
async def download_invoice(invoice_id: str, token: str):
    p = link_payload(token, "invoice")
    if p["sub"] != invoice_id:
        raise HTTPException(403, "This link is for a different invoice")
    inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    pdf = await run_in_threadpool(invoice_pdf, inv)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="DashLocal-{inv["number"]}.pdf"', "Cache-Control": "no-store"})


@api_router.get("/admin/invoices", dependencies=ADMIN_ONLY)
async def admin_invoices(status: Optional[str] = None, merchant_id: Optional[str] = None):
    q = {k: v for k, v in (("status", status), ("merchant_id", merchant_id)) if v}
    invs = await db.invoices.find(q, {"_id": 0}).sort("number", -1).to_list(500)
    alls = await db.invoices.find({}, {"_id": 0, "status": 1, "total": 1}).to_list(10000)
    return {**invoice_summary(alls), "invoices": invs}


class InvoicesPaidIn(BaseModel):
    ids: List[str]
    paid: bool = True
    note: Optional[str] = None


@api_router.post("/admin/invoices/mark-paid")
async def mark_invoices_paid(body: InvoicesPaidIn, u: dict = Depends(admin_user)):
    """Confirm payment (usually one the shop reported) or set invoices back to unpaid (e.g. reported payment not received)."""
    if not body.ids:
        raise HTTPException(400, "Pick at least one invoice")
    note = " ".join((body.note or "").split())[:200]
    invs = await db.invoices.find({"id": {"$in": body.ids[:500]}}, {"_id": 0}).to_list(500)
    if body.paid:  # validate the whole batch first so a bad row never leaves it half-saved
        for inv in invs:
            if not (note or (inv.get("report") or {}).get("note")):
                raise HTTPException(400, f"Add a payment note for {inv['number']} (e.g. e-Transfer reference)")
    n = 0
    for inv in invs:
        if body.paid:
            upd = {"status": "paid", "paid_at": now_iso(), "paid_note": note or inv["report"]["note"], "paid_by": u["email"]}
        else:
            upd = {"status": "unpaid", "paid_at": None, "paid_note": None, "paid_by": None, "report": None}
        n += (await db.invoices.update_one({"id": inv["id"]}, {"$set": upd})).modified_count
    return {"updated": n}


class ReportPaidIn(BaseModel):
    ids: List[str]
    note: str


@api_router.post("/merchants/{merchant_id}/invoices/report-paid")
async def report_invoices_paid(merchant_id: str, body: ReportPaidIn, u: dict = Depends(merchant_access)):
    """Shop says it paid (e-Transfer, cheque…). Invoices show 'payment reported' until an admin confirms."""
    note = " ".join(body.note.split())[:200]
    if not body.ids or not note:
        raise HTTPException(400, "Pick invoices and add how you paid (e.g. e-Transfer reference)")
    res = await db.invoices.update_many({"id": {"$in": body.ids[:500]}, "merchant_id": merchant_id, "status": "unpaid"},
                                        {"$set": {"status": "reported", "report": {"note": note, "at": now_iso(), "by": u["email"]}}})
    if res.modified_count:
        biz_base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
        m = await get_merchant_or_404(merchant_id)
        spawn(notify_admin(f"{m['name']} reported a payment", f"<p>{res.modified_count} invoice(s) · “{escape(note)}”</p><p>Confirm it in Admin › Invoices.</p>", biz_base))
    return {"updated": res.modified_count}


class BusinessIn(BaseModel):
    name: str
    address: str
    hst_number: str
    hst_pct: float


@api_router.get("/admin/business", dependencies=ADMIN_ONLY)
async def read_business():
    return await get_business()


@api_router.put("/admin/business", dependencies=ADMIN_ONLY)
async def update_business(body: BusinessIn):
    """Details printed on new invoices; issued invoices keep what they were issued with."""
    v = {"name": " ".join(body.name.split())[:80], "address": " ".join(body.address.split())[:200],
         "hst_number": " ".join(body.hst_number.split())[:40], "hst_pct": round(body.hst_pct, 3)}
    if not v["name"] or not v["address"] or not v["hst_number"]:
        raise HTTPException(400, "Business name, address and HST number are required")
    if not 0 <= v["hst_pct"] <= 30:
        raise HTTPException(400, "HST % must be between 0 and 30")
    await db.settings.update_one({"key": "business"}, {"$set": {"key": "business", "value": v}}, upsert=True)
    return v


@api_router.get("/rate-card")
async def rate_card():
    """Public: the standard prices shown on the landing page (stores may have custom rates)."""
    p, biz = await get_pricing(), await get_business()
    return {k: p[k] for k in ("currency", "base_fee", "included_km", "per_km", "priority_fee")} | {"hst_pct": biz["hst_pct"]}


# ---------------------------------------------------------------- review requests + merchant sign-up requests
REVIEW_LINK_DAYS = 14
SETPW_LINK_HOURS = 72


def demo_inbox(email: str) -> str:
    """Seeded demo logins are not real inboxes; route their mail to the provider's test inbox."""
    return TEST_INBOX if email in {em for _, em, _ in DEMO_MERCHANT_USERS} else email


async def app_base(request: Request) -> str:
    await remember_public_base(request)
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    if not base:
        raise HTTPException(400, "App address unknown yet — open the app from its public link and try again")
    return base


def brand_email(title: str, body_html: str, cta: Optional[tuple] = None) -> str:
    btn = (f'<p style="margin:24px 0"><a href="{escape(cta[1])}" style="background:#2B8932;color:#fff;padding:14px 22px;'
           f'border-radius:10px;text-decoration:none;font-family:Arial,sans-serif;font-weight:bold">{escape(cta[0])}</a></p>') if cta else ""
    return (f'<!doctype html><html><body style="margin:0;background:#F5F5F5;padding:24px">'
            f'<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:16px;padding:28px;color:#333335">'
            f'<div style="font-family:Arial,sans-serif;font-weight:bold;font-size:20px"><span style="color:#333335">DASH</span><span style="color:#D7262E">LOCAL</span></div>'
            f'<h1 style="font-family:Arial,sans-serif;font-size:22px;margin:20px 0 8px">{escape(title)}</h1>'
            f'<div style="font-family:Georgia,serif;font-size:16px;line-height:1.6">{body_html}</div>{btn}'
            f'<p style="font-family:Georgia,serif;font-size:13px;color:#5C5C5E">Same-day local delivery for small businesses.</p></div></body></html>')


async def notify_admin(subject: str, body_html: str, base: Optional[str]):
    """Best effort: email the dispatch admin; never blocks the request."""
    to = os.environ.get("ADMIN_NOTIFY_EMAIL") or os.environ.get("ADMIN_EMAIL")
    if not to:
        return
    try:
        await send_email(to=to, subject=subject, html=brand_email(subject, body_html, ("Open the admin panel", f"{base}/admin") if base else None))
    except Exception as e:
        logger.warning(f"Admin notify failed: {e}")


class ReviewRequestIn(BaseModel):
    merchant_id: str


@api_router.post("/admin/review-requests", dependencies=ADMIN_ONLY)
async def send_review_request(body: ReviewRequestIn, request: Request):
    """Email a store a private link to write its own quote; it lands hidden until the admin publishes it."""
    m = await get_merchant_or_404(body.merchant_id)
    users = await db.users.find({"merchant_id": m["id"], "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0}).to_list(20)
    if not users:
        raise HTTPException(400, "This store has no login email yet")
    base = await app_base(request)
    rid = str(uuid.uuid4())
    link = f"{base}/review/{issue_token(rid, 'merchant', purpose='review', minutes=REVIEW_LINK_DAYS * 24 * 60)}"
    html = brand_email(f"How is DashLocal working for {m['name']}?",
                       f"<p>We'd love a short quote about delivering with DashLocal. With your OK, it may appear on our website for other small businesses to read.</p>"
                       f"<p>The link works once and lasts {REVIEW_LINK_DAYS} days.</p>", ("Write a quick review", link))
    sent, errors = [], []
    for u in users:
        try:
            await send_email(to=demo_inbox(u["email"]), subject=f"A quick review of DashLocal? — {m['name']}", html=html)
            sent.append(u["email"])
        except Exception as e:
            errors.append(str(e))
    if not sent:
        raise HTTPException(424, f"Email could not be sent: {errors[0] if errors else 'unknown error'}")
    doc = {"id": rid, "merchant_id": m["id"], "shop": m["name"], "emails": sent, "status": "sent", "created_at": now_iso(), "submitted_at": None, "reminded_at": None}
    await db.review_requests.insert_one(dict(doc))
    return doc


@api_router.get("/admin/review-requests", dependencies=ADMIN_ONLY)
async def list_review_requests():
    return await db.review_requests.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)


def link_payload(token: str, purpose: str) -> dict:
    """Decode an emailed link token with a message that makes sense on a public page."""
    try:
        return decode_token(token, purpose)
    except HTTPException as e:
        raise HTTPException(400, "This link has expired — ask DashLocal for a new one" if "expired" in str(e.detail) else "This link isn't valid")


async def open_review_request(token: str) -> dict:
    rid = link_payload(token, "review")["sub"]
    rr = await db.review_requests.find_one({"id": rid}, {"_id": 0})
    if not rr:
        raise HTTPException(404, "This review link is no longer valid")
    return rr


@api_router.get("/review-requests/{token}")
async def get_review_request(token: str):
    rr = await open_review_request(token)
    return {"shop": rr["shop"], "status": rr["status"]}


class ReviewSubmitIn(BaseModel):
    quote: str
    person: Optional[str] = None


@api_router.post("/review-requests/{token}")
async def submit_review(token: str, body: ReviewSubmitIn, request: Request):
    rr = await open_review_request(token)
    if rr["status"] != "sent":
        raise HTTPException(400, "Thanks — a review was already sent with this link")
    t = clean_testimonial(TestimonialIn(quote=body.quote, person=body.person, shop=rr["shop"], published=False))
    res = await db.review_requests.update_one({"id": rr["id"], "status": "sent"}, {"$set": {"status": "submitted", "submitted_at": now_iso()}})
    if not res.modified_count:
        raise HTTPException(400, "Thanks — a review was already sent with this link")
    doc = {"id": str(uuid.uuid4()), **t, "from_shop": True, "merchant_id": rr["merchant_id"], "created_at": now_iso(), "updated_at": now_iso()}
    await db.testimonials.insert_one(dict(doc))
    await notify_admin(f"New review from {rr['shop']} to approve", f"<p>“{escape(t['quote'])}”</p><p>Publish it from Shop reviews in the admin panel.</p>",
                       (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value"))
    return {"ok": True}


REVIEW_REMINDER_DAYS = 7


async def send_review_reminders() -> int:
    """One gentle reminder per review link still unused after 7 days (the link keeps its original expiry)."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=REVIEW_REMINDER_DAYS)).isoformat()
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    if not base:
        return 0
    sent = 0
    for rr in await db.review_requests.find({"status": "sent", "created_at": {"$lte": cutoff}, "reminded_at": None}, {"_id": 0}).to_list(100):
        left = timedelta(days=REVIEW_LINK_DAYS) - (now - datetime.fromisoformat(rr["created_at"]))
        if left <= timedelta(hours=12):
            continue  # too close to expiry to be useful
        # claim first so two workers never double-send
        if not (await db.review_requests.update_one({"id": rr["id"], "reminded_at": None}, {"$set": {"reminded_at": now_iso()}})).modified_count:
            continue
        link = f"{base}/review/{issue_token(rr['id'], 'merchant', purpose='review', minutes=int(left.total_seconds() // 60))}"
        days_left = max(1, round(left.total_seconds() / 86400))
        html = brand_email(f"Still happy to share a few words, {rr['shop']}?",
                           "<p>Just a friendly nudge: a sentence or two about delivering with DashLocal helps other small businesses find us. "
                           f"No pressure at all — the link stays open for {days_left} more day{'s' if days_left != 1 else ''}.</p>",
                           ("Write a quick review", link))
        ok = False
        for em in rr.get("emails", []):
            try:
                await send_email(to=demo_inbox(em), subject=f"A quick review of DashLocal? — {rr['shop']}", html=html)
                ok = True
            except Exception as e:
                logger.warning(f"Review reminder failed for {rr['id']}: {e}")
        attempts = rr.get("reminder_attempts", 0) + 1
        # failed sends (e.g. provider rate limit) are retried on the next hourly run, up to 3 tries
        upd = {"reminder_status": "sent" if ok else "failed", "reminder_attempts": attempts}
        if not ok and attempts < 3:
            upd["reminded_at"] = None
        await db.review_requests.update_one({"id": rr["id"]}, {"$set": upd})
        sent += ok
    return sent


async def review_reminder_watcher():
    await asyncio.sleep(30)
    while True:
        try:
            await send_review_reminders()
        except Exception as e:
            logger.warning(f"Review reminder loop error: {e}")
        await asyncio.sleep(3600)


@api_router.post("/admin/review-requests/run-reminders", dependencies=ADMIN_ONLY)
async def run_review_reminders():
    """Manual trigger of the hourly reminder check (testing/ops)."""
    return {"sent": await send_review_reminders()}


GROWTH_TIPS = [
    ("Upload orders every day", "Shops that send a CSV each morning get the smoothest routes. The template in your portal has every column."),
    ("Save notes for your regulars", "Gate codes and drop-off tips you save are filled in on every new order for that customer."),
    ("Share your tracking links", "Customers who can follow their order live call less and come back more."),
    ("Use priority for rush orders", "Mark urgent orders as priority so they go out first (a small surcharge applies)."),
    ("Watch your weekly email", "Your Monday report and statement show deliveries, on-time rate and what you owe."),
]


async def send_first_delivery_email(merchant_id: str):
    """One-time follow-up after a newly approved shop's first delivery, with tips to grow."""
    m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0})
    if not m:
        return
    users = await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0, "email": 1}).to_list(20)
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    tips = "".join(f"<li style=\"margin-bottom:10px\"><b>{escape(t)}</b><br>{escape(b)}</li>" for t, b in GROWTH_TIPS)
    html = brand_email(f"Your first DashLocal delivery is done 🎉",
                       f"<p>Congratulations, {escape(m['name'])}: your first order just reached its customer. A few tips to get the most from DashLocal:</p>"
                       f"<ol style=\"padding-left:20px\">{tips}</ol><p>Questions? Just reply to this email.</p>",
                       ("Open your portal", f"{base}/sign-in/merchant") if base else None)
    ok = False
    for u in users:
        try:
            await send_email(to=demo_inbox(u["email"]), subject=f"First delivery done — tips to grow, {m['name']}", html=html)
            ok = True
        except Exception as e:
            logger.warning(f"First-delivery email failed for {merchant_id}: {e}")
    # failed sends are retried on the shop's next delivery (first_delivery_email_at reopened)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"first_delivery_email_status": "sent" if ok else "failed",
                                                                  **({} if ok else {"first_delivery_email_at": None})}})


@api_router.get("/merchants/{merchant_id}/checklist")
async def merchant_checklist(merchant_id: str, _: dict = Depends(merchant_access)):
    """First-steps checklist for newly approved shops; each step ticks itself from real activity."""
    m = await get_merchant_or_404(merchant_id)
    steps = [
        {"key": "password", "label": "Choose your password", "done": True},
        {"key": "upload", "label": "Upload your first orders (CSV)", "done": bool(await db.import_jobs.find_one({"merchant_id": merchant_id}, {"_id": 1}))},
        {"key": "note", "label": "Save a delivery note for a regular customer", "done": bool(await db.saved_notes.find_one({"merchant_id": merchant_id}, {"_id": 1}))},
        {"key": "delivered", "label": "Get your first delivery completed", "done": bool(await db.stops.find_one({"merchant_id": merchant_id, "status": "delivered"}, {"_id": 1}))},
    ]
    return {"show": bool(m.get("onboarding")), "steps": steps, "done": sum(s["done"] for s in steps), "total": len(steps)}


@api_router.post("/merchants/{merchant_id}/checklist/dismiss")
async def dismiss_checklist(merchant_id: str, _: dict = Depends(merchant_access)):
    await get_merchant_or_404(merchant_id)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"onboarding": False}})
    return {"ok": True}


class SignupIn(BaseModel):
    store_name: str
    contact_name: str
    email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    message: Optional[str] = None


@api_router.post("/signup-requests")
async def request_signup(body: SignupIn, request: Request):
    """Public: a shop asks for a DashLocal account. The admin approves it from the admin panel."""
    clip = lambda v, n: " ".join((v or "").split())[:n]
    doc = {"store_name": clip(body.store_name, 80), "contact_name": clip(body.contact_name, 60), "email": body.email.strip().lower()[:120],
           "phone": clip(body.phone, 30) or None, "address": clip(body.address, 160) or None, "message": clip(body.message, 500) or None}
    if not doc["store_name"] or not doc["contact_name"]:
        raise HTTPException(400, "Store name and your name are required")
    if not EMAIL_RE.match(doc["email"]):
        raise HTTPException(400, "Enter a valid email")
    ip = client_ip(request)
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    if await db.signup_requests.count_documents({"ip": ip, "created_at": {"$gte": hour_ago}}) >= 5:
        raise HTTPException(429, "Too many requests — try again in an hour")
    if await db.signup_requests.find_one({"email": doc["email"], "status": "pending"}) or await db.users.find_one({"email": doc["email"]}):
        return {"ok": True}  # same answer either way: don't reveal which emails already have accounts
    await db.signup_requests.insert_one({**doc, "id": str(uuid.uuid4()), "status": "pending", "ip": ip, "created_at": now_iso()})
    base = None
    try:
        base = await app_base(request)
    except HTTPException:
        pass
    asyncio.create_task(notify_admin(f"New shop sign-up request: {doc['store_name']}",
                       f"<p><b>{escape(doc['store_name'])}</b> — {escape(doc['contact_name'])}</p><p>{escape(doc['message'] or '')}</p>"
                       "<p>Approve or decline it from Sign-up requests in the admin panel.</p>", base))
    return {"ok": True}


@api_router.get("/admin/signup-requests", dependencies=ADMIN_ONLY)
async def list_signups():
    return await db.signup_requests.find({}, {"_id": 0, "ip": 0}).sort("created_at", -1).to_list(200)


@api_router.post("/admin/signup-requests/{sid}/approve", dependencies=ADMIN_ONLY)
async def approve_signup(sid: str, request: Request):
    """Create the store + its login (no password yet) and email a one-time set-password link."""
    sr = await db.signup_requests.find_one({"id": sid}, {"_id": 0})
    if not sr:
        raise HTTPException(404, "Request not found")
    if sr["status"] != "pending":
        raise HTTPException(400, f"Request already {sr['status']}")
    if await db.users.find_one({"email": sr["email"]}):
        raise HTTPException(409, "An account with this email already exists")
    base = await app_base(request)
    m = {"id": f"mer-{uuid.uuid4().hex[:8]}", "name": sr["store_name"], "contact": sr["email"], "onboarding": True,
         "approved_via_signup": True, "first_delivery_email_at": None, "address": sr.get("address")}
    nonce = secrets.token_urlsafe(12)
    user = {"id": str(uuid.uuid4()), "email": sr["email"], "role": "merchant", "name": sr["contact_name"], "merchant_id": m["id"],
            "disabled": False, "created_at": now_iso(), "pw_nonce": nonce, "password_hash": hash_pw(secrets.token_urlsafe(24))}
    await db.merchants.insert_one(dict(m))
    try:
        await db.users.insert_one(user)
    except DuplicateKeyError:
        await db.merchants.delete_one({"id": m["id"]})
        raise HTTPException(409, "An account with this email already exists")
    link = f"{base}/set-password/{issue_token(user['id'], 'merchant', purpose='setpw', minutes=SETPW_LINK_HOURS * 60, n=nonce)}"
    status = "sent"
    try:
        await send_email(to=demo_inbox(sr["email"]), subject="Your DashLocal store is ready",
                         html=brand_email(f"Welcome to DashLocal, {sr['contact_name'].split(' ')[0]}",
                                          f"<p>Your store <b>{escape(sr['store_name'])}</b> is approved. Choose a password to sign in and upload your first orders.</p>"
                                          f"<p>The link works once and lasts {SETPW_LINK_HOURS} hours.</p>", ("Choose your password", link)))
    except Exception as e:
        status = f"failed: {e}"
    await db.signup_requests.update_one({"id": sid}, {"$set": {"status": "approved", "merchant_id": m["id"], "decided_at": now_iso(), "email_status": status}})
    return {"ok": True, "merchant_id": m["id"], "email_status": status}


@api_router.post("/admin/signup-requests/{sid}/decline", dependencies=ADMIN_ONLY)
async def decline_signup(sid: str):
    res = await db.signup_requests.update_one({"id": sid, "status": "pending"}, {"$set": {"status": "declined", "decided_at": now_iso()}})
    if not res.matched_count:
        raise HTTPException(404, "No pending request with this id")
    return {"ok": True}


async def setpw_user(token: str) -> dict:
    p = link_payload(token, "setpw")
    u = await db.users.find_one({"id": p["sub"], "disabled": {"$ne": True}}, {"_id": 0})
    if not u or not u.get("pw_nonce") or not secrets.compare_digest(u["pw_nonce"], str(p.get("n", ""))):
        raise HTTPException(400, "This link was already used or has been replaced — ask DashLocal for a new one")
    return u


@api_router.get("/auth/set-password/{token}")
async def setpw_info(token: str):
    u = await setpw_user(token)
    m = await db.merchants.find_one({"id": u.get("merchant_id")}, {"_id": 0}) or {}
    return {"email": u["email"], "store_name": m.get("name")}


class SetPasswordIn(BaseModel):
    token: str
    password: str


@api_router.post("/auth/set-password")
async def setpw(body: SetPasswordIn, request: Request):
    u = await setpw_user(body.token)
    check_password(body.password)
    await db.users.update_one({"id": u["id"]}, {"$set": {"password_hash": hash_pw(body.password), "last_login_at": now_iso()}, "$unset": {"pw_nonce": ""}})
    await remember_public_base(request)
    user = {k: v for k, v in u.items() if k not in ("password_hash", "pw_nonce")}
    user["merchant_name"] = (await db.merchants.find_one({"id": u.get("merchant_id")}, {"_id": 0}) or {}).get("name")
    return {"token": issue_token(u["id"], u["role"]), "user": user}


app.include_router(api_router)

# Only the app's own web addresses may call the API from a browser (phone apps send no Origin and are unaffected).
CORS_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_credentials=False, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
