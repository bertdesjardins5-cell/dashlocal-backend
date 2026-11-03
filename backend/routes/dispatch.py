from fastapi import APIRouter, HTTPException, Depends, Request
import asyncio
import math
import uuid
from pydantic import BaseModel
from typing import Optional, Dict
from core import ADMIN_ONLY, DEPOT, DRIVER_SELF, GEOFENCE_M, ROUTE_DRIVER, admin_user, current_user, db, haversine_m, now_iso, optimize_sequence, path_length_m, plan_base, require_driver_self, require_route_driver, today_str
from models import BatchIn, Driver, LocationIn, Route, SequenceIn, Stop, apply_sequence, get_driver_or_404, get_route_or_404, get_stops, route_detail
from routes import push
from services import realtime
from services import seed

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- endpoints
@router.get("/")
async def root():
    return {"message": "ArriveWise API"}


@router.post("/admin/reset")
async def reset(request: Request):
    await admin_user(await current_user(request))  # wipes all data incl. merchant orders: admin only
    if await db.settings.find_one({"key": "demo_removed"}, {"_id": 1}):
        raise HTTPException(400, "Demo data was removed for launch — the demo reset is turned off")
    await seed.seed(force=True)
    return {"ok": True}


@router.get("/drivers", dependencies=ADMIN_ONLY)
async def list_drivers():
    docs = await db.drivers.find({"deleted": {"$ne": True}}, {"_id": 0}).to_list(100)
    out = []
    for d in docs:
        r = await db.routes.find_one({"driver_id": d["id"], "status": {"$in": ["active", "planned"]}}, {"_id": 0, "id": 1, "name": 1, "status": 1})
        out.append({**Driver(**d).model_dump(), "route": r})
    return out


@router.get("/drivers/{driver_id}/route")
async def driver_route(driver_id: str, u: dict = Depends(require_driver_self)):
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
    return {"route": for_viewer(await route_detail(Route(**doc)), u)}


# Driver screens only get what the drop needs (PHIPA minimum necessary): recipient name, street address and
# delivery notes. No phone numbers (calls go through the masked line), prices, invoice items or consent records.
DRIVER_HIDDEN = ("customer_phone", "fee", "consent_given", "consent_at", "consent_by", "consent_text", "rating_comment",
                 "follow_up_note", "apology_code", "merchant_late_alert_at", "merchant_fail_alert_at", "matter_ref")


def driver_stop_view(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in DRIVER_HIDDEN}


def stop_for(u: dict, d: dict) -> dict:
    return driver_stop_view(d) if u.get("role") == "driver" else d


def for_viewer(route: dict, u: dict) -> dict:
    if u.get("role") != "driver":
        return route
    return {**route, "stops": [driver_stop_view(s) for s in route["stops"]]}


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
        await realtime.broadcast_route(rdoc["id"])
    return {"ok": True, "geofence_arrived_stop_id": geofenced, "route_active": bool(rdoc)}


@router.post("/drivers/{driver_id}/location", dependencies=DRIVER_SELF)
async def post_location(driver_id: str, body: LocationIn):
    return await update_location(driver_id, body.lat, body.lng)


@router.post("/drivers/{driver_id}/simulate-step", dependencies=DRIVER_SELF)
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


@router.get("/zones", dependencies=ADMIN_ONLY)
async def zones(date: Optional[str] = None):
    docs = await db.stops.find(seed.eligible_query(seed.check_service_date(date)), {"_id": 0}).to_list(1000)
    groups: Dict[str, dict] = {}
    for d in docs:
        g = groups.setdefault(d["zone"], {"zone": d["zone"], "postal_code": d["postal_code"], "count": 0, "orders": []})
        g["count"] += 1
        g["orders"].append(Stop(**d).model_dump())
    return sorted(groups.values(), key=lambda g: -g["count"])


@router.post("/routes/batch", dependencies=ADMIN_ONLY)
async def batch(body: BatchIn):
    await get_driver_or_404(body.driver_id)
    async with _dispatch_lock:
        route = await seed.create_batch(body.zone, body.driver_id, service_date=body.service_date)
    detail = await route_detail(route)
    push.notify_run(route.driver_id, route.id, route.name, len(detail["stops"]))
    return detail


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


@router.put("/admin/drivers/{driver_id}/compass-zone", dependencies=ADMIN_ONLY)
async def set_compass_zone(driver_id: str, body: CompassZoneIn):
    if body.zone is not None and body.zone not in COMPASS:
        raise HTTPException(400, f"zone must be one of {', '.join(COMPASS)} or empty")
    await get_driver_or_404(driver_id)
    await db.drivers.update_one({"id": driver_id}, {"$set": {"compass_zone": body.zone}})
    return (await get_driver_or_404(driver_id)).model_dump()


@router.get("/dispatch/preview", dependencies=ADMIN_ONLY)
async def dispatch_preview(date: Optional[str] = None):
    """Unassigned orders for the day split by N/S/E/W, with the driver each zone would go to."""
    service_date = seed.check_service_date(date)
    docs = await db.stops.find(seed.eligible_query(service_date), {"_id": 0, "lat": 1, "lng": 1}).to_list(5000)
    counts = {z: 0 for z in COMPASS}
    for d in docs:
        counts[compass_of(d)] += 1
    drivers = [Driver(**d) for d in await db.drivers.find({"id": {"$ne": "drv-review"}, "deleted": {"$ne": True}}, {"_id": 0}).sort("id", 1).to_list(100)]  # Play reviewer never gets real orders
    return {"service_date": service_date, "total": len(docs),
            "zones": [{"zone": z, "count": counts[z], "driver": next(({"id": d.id, "name": d.name} for d in drivers if d.compass_zone == z), None)} for z in COMPASS],
            "drivers": [{"id": d.id, "name": d.name, "vehicle": d.vehicle, "compass_zone": d.compass_zone} for d in drivers]}


@router.post("/routes/auto-dispatch", dependencies=ADMIN_ONLY)
async def auto_dispatch(body: AutoDispatchIn):
    async with _dispatch_lock:  # two taps at once must not create duplicate / empty routes
        return await _auto_dispatch(body)


_dispatch_lock = asyncio.Lock()


async def _auto_dispatch(body: AutoDispatchIn):
    """One tap: split every unassigned order for the day into North/South/East/West of the depot and give each
    zone's stops to the driver covering it. A zone nobody covers goes to the driver with the fewest stops.
    Each driver gets one route for the day (stops are added to their existing open route, which is re-optimised)."""
    service_date = seed.check_service_date(body.service_date)
    docs = await db.stops.find(seed.eligible_query(service_date), {"_id": 0}).to_list(5000)
    if not docs:
        raise HTTPException(400, "No unassigned orders for that day")
    drivers = [Driver(**d) for d in await db.drivers.find({"id": {"$ne": "drv-review"}, "deleted": {"$ne": True}}, {"_id": 0}).sort("id", 1).to_list(100)]  # Play reviewer never gets real orders
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
            await realtime.broadcast_route(route.id)
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
        push.notify_run(did, route.id, route.name, len(stops), added=not created)
        drv = next(d for d in drivers if d.id == did)
        helping = [x["zone"] for x in splits if x["helper_id"] == did]
        out.append({"route_id": route.id, "route_name": route.name, "driver_id": did, "driver_name": drv.name, "zones": list(zones),
                    "stops": len(stops), "created": created, "fallback": [z for z in zones if drv.compass_zone != z and z not in helping],
                    "helping": helping})
    return {"service_date": service_date, "dispatched": len(docs), "routes": out, "splits": splits}


@router.get("/routes", dependencies=ADMIN_ONLY)
async def list_routes():
    docs = await db.routes.find({"service_date": {"$gte": today_str()}}, {"_id": 0}).sort("created_at", -1).to_list(200)
    out = []
    for d in docs:
        det = await route_detail(Route(**d))
        det.pop("stops")
        out.append(det)
    return out


@router.get("/routes/{route_id}")
async def get_route(route_id: str, u: dict = Depends(require_route_driver)):
    return for_viewer(await route_detail(await get_route_or_404(route_id)), u)


@router.post("/routes/{route_id}/optimize", dependencies=ADMIN_ONLY)
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
    await realtime.broadcast_route(route_id)
    return await route_detail(await get_route_or_404(route_id))


@router.put("/routes/{route_id}/sequence", dependencies=ADMIN_ONLY)
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
    await realtime.broadcast_route(route_id)
    return await route_detail(await get_route_or_404(route_id))


@router.post("/routes/{route_id}/update-seen", dependencies=ROUTE_DRIVER)
async def route_update_seen(route_id: str):
    """Driver acknowledged the dispatcher's latest re-order."""
    await get_route_or_404(route_id)
    await db.routes.update_one({"id": route_id}, {"$set": {"resequence_seen": True}})
    return await route_detail(await get_route_or_404(route_id))


@router.post("/routes/{route_id}/start", dependencies=ROUTE_DRIVER)
async def start_route(route_id: str):
    route = await get_route_or_404(route_id)
    if route.status != "planned":
        raise HTTPException(400, "Route already started")
    if route.service_date > today_str():
        raise HTTPException(400, f"Route is scheduled for {seed.nice_date(route.service_date)}")
    if route.driver_id and await db.routes.find_one({"driver_id": route.driver_id, "status": "active"}):
        raise HTTPException(400, "Driver already has an active route")
    await db.routes.update_one({"id": route_id}, {"$set": {"status": "active", "started_at": now_iso(), "actual_distance_m": 0}})
    if route.driver_id:
        await db.drivers.update_one({"id": route.driver_id}, {"$set": {"lat": DEPOT["lat"], "lng": DEPOT["lng"]}})
    await realtime.broadcast_route(route_id)
    return await route_detail(await get_route_or_404(route_id))


async def get_stop_or_404(stop_id: str) -> Stop:
    doc = await db.stops.find_one({"id": stop_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Stop not found")
    return Stop(**doc)


async def signature_required(stop: Stop) -> bool:
    """Stores set to 'signature at the door' (on by default for law offices) can't be completed without one."""
    if not stop.merchant_id:
        return False
    return bool(await db.merchants.find_one({"id": stop.merchant_id, "signature_required": True}, {"_id": 1}))


async def is_pharmacy_drop(stop: Stop) -> bool:
    """Prescription hand-off: orders from a pharmacy batch run or a pharmacy-partner store need photo + signature."""
    if stop.batch_run:
        return True
    if not stop.merchant_id:
        return False
    return bool(await db.merchants.find_one({"id": stop.merchant_id, "pharmacy_partner": True}, {"_id": 1}))
