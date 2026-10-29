from fastapi import HTTPException, WebSocket
from fastapi.concurrency import run_in_threadpool
import os
import asyncio
import uuid
from twilio.rest import Client as TwilioClient
from typing import List, Dict, Set
from datetime import timezone, timedelta
from core import DEPOT, db, issue_token, logger, now_iso, plan_base, schedule, today_str
from models import Driver, Route, Stop, get_stops, route_detail
from routes import alerts
from routes import dispatch
from routes import stops as stop_routes
from services import seed

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
            "customer_note_source": stop.customer_note_source, "payment": order_payment(stop.model_dump()),
            "tips_total": tips_total(stop)}


def tips_total(stop: "Stop") -> float:
    from routes.square import customer_payments  # lazy: routes.square imports this module
    return round(sum(max(0, min(p["tip"], p["refundable"])) for p in customer_payments(stop.model_dump())), 2)


def order_payment(s: dict):
    from routes.square import order_payment as op  # lazy: routes.square imports this module
    return op(s)


async def tracking_payload(code: str) -> dict:
    sdoc = await db.stops.find_one({"tracking_code": code.upper()}, {"_id": 0})
    if not sdoc:
        raise HTTPException(404, "Tracking code not found")
    stop = Stop(**sdoc)
    out = {"stop": public_stop(stop), "route_status": None, "driver": None, "stops_away": None,
           "eta_minutes": None, "eta_time": None, "late_min": 0, "path": [], "alert": None, "depot": DEPOT}
    if not stop.route_id:
        out["alert"] = f"Scheduled for {seed.nice_date(stop.window_date)}" if stop.window_date and stop.window_date > today_str() else "Order received — awaiting dispatch"
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
        out["alert"] = f"Scheduled for {seed.nice_date(route.service_date)}"
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
    if stop_routes.FICTIONAL_NUMBER.match(stop.customer_phone):
        return "skipped_fictional"
    if not (os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_PHONE_NUMBER")):
        return "not_configured"
    where = "you're next" if stops_away == 0 else f"{stops_away} stop{'s' if stops_away > 1 else ''} away"
    body = f"ArriveWise: {driver_name.split(' ')[0]} is {where} with your delivery {stop.tracking_code}. Track live in the app."
    try:
        tw = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        res = await run_in_threadpool(lambda: tw.messages.create(to=stop.customer_phone, from_=stop_routes.twilio_number(), body=body))
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
    await alerts.maybe_late_alerts(route, stops)
    for s in stops:
        if hub.rooms.get(f"track:{s.tracking_code}"):
            await hub.send(f"track:{s.tracking_code}", await tracking_payload(s.tracking_code))
    if hub.rooms.get(f"route:{route_id}"):
        await hub.send(f"route:{route_id}", dispatch.for_viewer(await route_detail(route), {"role": "driver"}))  # only drivers listen here


async def maybe_complete_route(route_id: str):
    stops = await get_stops(route_id)
    if stops and all(s.status in ("delivered", "failed") for s in stops):
        await db.routes.update_one({"id": route_id}, {"$set": {"status": "completed", "completed_at": now_iso()}})
