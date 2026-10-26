from fastapi import HTTPException, Depends, Request
import bcrypt
import jwt as pyjwt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import os
import math
import logging
import requests
import secrets
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone, timedelta, date as date_cls, time as time_cls
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet, InvalidToken

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)

# ---------------------------------------------------------------- encryption at rest (PIPEDA / PHIPA safeguards)
# Free-text notes, signatures and proof photos are encrypted with Fernet (AES-128-CBC + HMAC) before they are stored.
FERNET = Fernet(os.environ["ENCRYPTION_KEY"].encode())
ENC_PREFIX = "enc:"
ENC_FIELDS = {"stops": ("notes", "customer_note", "pod_signature"), "saved_notes": ("note",)}


def enc_str(v):
    if not isinstance(v, str) or not v or v.startswith(ENC_PREFIX):
        return v
    return ENC_PREFIX + FERNET.encrypt(v.encode()).decode()


def dec_str(v):
    if isinstance(v, str) and v.startswith(ENC_PREFIX):
        try:
            return FERNET.decrypt(v[len(ENC_PREFIX):].encode()).decode()
        except InvalidToken:
            return None
    return v


def enc_bytes(b: bytes) -> bytes:
    return FERNET.encrypt(b)


def dec_bytes(b: bytes) -> bytes:
    try:
        return FERNET.decrypt(b)
    except InvalidToken:
        return b  # files stored before encryption was switched on


class _EncCursor:
    def __init__(self, cur, fields):
        self._cur, self._f = cur, fields

    def __getattr__(self, name):  # sort / limit / skip ... keep chaining on the wrapper
        attr = getattr(self._cur, name)
        if not callable(attr):
            return attr

        def call(*a, **k):
            r = attr(*a, **k)
            return self if r is self._cur else r
        return call

    def _dec(self, d):
        if isinstance(d, dict):
            for f in self._f:
                if f in d:
                    d[f] = dec_str(d[f])
        return d

    async def to_list(self, *a, **k):
        return [self._dec(d) for d in await self._cur.to_list(*a, **k)]

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        async for d in self._cur:
            yield self._dec(d)


class _EncCollection:
    """Transparent field encryption for one Mongo collection: writes encrypt, reads decrypt."""
    def __init__(self, coll, fields):
        self._c, self._f = coll, fields

    def __getattr__(self, name):
        return getattr(self._c, name)

    def _enc_doc(self, d):
        if not isinstance(d, dict):
            return d
        d = dict(d)
        for f in self._f:
            if f in d:
                d[f] = enc_str(d[f])
        return d

    def _enc_update(self, u):
        if not isinstance(u, dict):
            return u
        return {k: (self._enc_doc(v) if k in ("$set", "$setOnInsert") else v) for k, v in u.items()}

    def find(self, *a, **k):
        return _EncCursor(self._c.find(*a, **k), self._f)

    async def find_one(self, *a, **k):
        return _EncCursor(None, self._f)._dec(await self._c.find_one(*a, **k))

    async def find_one_and_update(self, flt, upd, *a, **k):
        return _EncCursor(None, self._f)._dec(await self._c.find_one_and_update(flt, self._enc_update(upd), *a, **k))

    def insert_one(self, doc, *a, **k):
        return self._c.insert_one(self._enc_doc(doc), *a, **k)

    def insert_many(self, docs, *a, **k):
        return self._c.insert_many([self._enc_doc(d) for d in docs], *a, **k)

    def update_one(self, flt, upd, *a, **k):
        return self._c.update_one(flt, self._enc_update(upd), *a, **k)

    def update_many(self, flt, upd, *a, **k):
        return self._c.update_many(flt, self._enc_update(upd), *a, **k)

    def replace_one(self, flt, doc, *a, **k):
        return self._c.replace_one(flt, self._enc_doc(doc), *a, **k)


class _EncDB:
    def __init__(self, raw):
        self._raw = raw

    def __getattr__(self, name):
        coll = getattr(self._raw, name)
        return _EncCollection(coll, ENC_FIELDS[name]) if name in ENC_FIELDS else coll

    def __getitem__(self, name):
        return self.__getattr__(name)


db = _EncDB(client[os.environ['DB_NAME']])

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("twilio.http_client").setLevel(logging.WARNING)


async def require_admin(request: Request):
    """Route dependency: dispatch admin sign-in (functions resolve at call time)."""
    return await admin_user(await current_user(request))


ADMIN_ONLY = [Depends(require_admin)]


def driver_consented(u: dict) -> dict:
    """Drivers must accept the privacy & compliance terms (consent modal) before working any manifest data."""
    if u["role"] == "driver" and not (u.get("phipa_consent_given") and u.get("consent_version") == CONSENT_VERSION):
        raise HTTPException(403, "Accept the privacy & compliance terms to continue")
    return u


async def require_driver_self(driver_id: str, request: Request):
    """The signed-in driver themself (or the dispatch admin)."""
    u = await current_user(request)
    if u["role"] == "admin" or (u["role"] == "driver" and u.get("driver_id") == driver_id):
        return driver_consented(u)
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
            return driver_consented(u)
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
            return driver_consented(u)
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
DEPOT = {"lat": 43.4516, "lng": -80.4925, "name": "Kitchener-Waterloo Depot"}
TZ = ZoneInfo("America/Toronto")  # depot timezone for customer delivery windows
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


# ---------------------------------------------------------------- auth (admin + merchant accounts, JWT bearer)
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = "HS256"
SESSION_HOURS = 12


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
RUNS = {  # Eastern (depot) time
    "afternoon": {"label": "Afternoon Batch", "cutoff": "13:30", "pickup": ("14:00", "14:30"), "window": ("14:30", "18:00")},
    "evening": {"label": "Evening Batch", "cutoff": "16:30", "pickup": ("17:00", "17:30"), "window": ("17:30", "20:30")},
}


def hhmm12(t: str) -> str:
    return datetime.strptime(t, "%H:%M").strftime("%-I:%M %p")
CONSENT_VERSION = "2026-06"  # bump when the consent modal terms change: everyone is asked again
