from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timezone, timedelta, date as date_cls, time as time_cls
from core import DEPOT, RUNS, TZ, db, hhmm12, now_iso, plan_base, schedule, today_str

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
    matter_ref: Optional[str] = None  # law offices: client file / matter number, printed on the signed receipt
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
    batch_run: Optional[str] = None  # pharmacy pickup run: afternoon | evening
    manifest_id: Optional[str] = None  # "{merchant_id}:{YYYY-MM-DD}:{run}" — one pickup manifest
    consent_given: bool = False  # merchant confirmed CONSENT_TEXT when placing the order
    consent_at: Optional[str] = None
    consent_by: Optional[str] = None
    consent_text: Optional[str] = None
    erased_at: Optional[str] = None  # personal details removed by the retention job
    rating_tags: List[str] = []  # quick feedback chips picked with the star rating


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


async def pickup_run_labels(stops: list) -> dict:
    """Pharmacy batch drops: stop_id -> which pickup run it belongs to, its pickup time and the pharmacy."""
    batch = [s for s in stops if s.batch_run in RUNS]
    if not batch:
        return {}
    names = {m["id"]: m for m in await db.merchants.find({"id": {"$in": list({s.merchant_id for s in batch})}},
                                                         {"_id": 0, "id": 1, "name": 1, "pickup": 1}).to_list(100)}
    out = {}
    for s in batch:
        r, m = RUNS[s.batch_run], names.get(s.merchant_id, {})
        out[s.id] = {"run": s.batch_run, "label": r["label"], "pickup_label": f"{hhmm12(r['pickup'][0])} – {hhmm12(r['pickup'][1])}",
                     "pickup_lat": (m.get("pickup") or {}).get("lat"), "pickup_lng": (m.get("pickup") or {}).get("lng"),
                     "date": s.manifest_id.split(":")[1] if s.manifest_id else None,
                     "pharmacy": m.get("name"), "pickup_address": (m.get("pickup") or {}).get("address")}
    return out


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
    runs = await pickup_run_labels(stops)
    pickups = await route_pickups(stops, runs)
    out_stops = []
    for s in stops:
        d = s.model_dump()
        p = plan.get(s.id)
        d["planned_eta"] = (now + timedelta(minutes=round(p["arrive_min"]))).isoformat() if p else None
        d["late_min"] = round(p["late_min"]) if p else 0
        d["pickup_run"] = runs.get(s.id)
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
        "pickups": pickups,
    }


async def merchant_pickup(merchant_id: str, r: dict) -> Optional[dict]:
    """Today's check-in for this run (shown even after the run rolls over to tomorrow), summed across routes."""
    for day in dict.fromkeys([today_str(), r["date"]]):
        mid = f"{merchant_id}:{day}:{r['key']}"
        docs = await db.pickups.find({"manifest_id": mid}, {"_id": 0}).sort("picked_up_at", 1).to_list(20)
        if docs:
            return {"date": day, "picked_up_at": docs[-1]["picked_up_at"], "parcel_count": sum(d["parcel_count"] for d in docs),
                    "expected": await db.stops.count_documents({"manifest_id": mid}), "on_time": all(d["on_time"] for d in docs),
                    "routes": len(docs)}
    return None


async def route_pickups(stops: list, runs: dict) -> list:
    """One entry per pharmacy pickup manifest on this route, with its check-in (picked-up time + parcel count)."""
    by: Dict[str, dict] = {}
    for s in stops:
        if s.manifest_id and s.id in runs:
            p = by.setdefault(s.manifest_id, {"manifest_id": s.manifest_id, **runs[s.id], "drops": 0})
            p["drops"] += 1
    if not by:
        return []
    done = {d["manifest_id"]: d for d in await db.pickups.find({"manifest_id": {"$in": list(by)}, "route_id": stops[0].route_id}, {"_id": 0}).to_list(100)}
    return [{**p, "picked_up_at": done.get(k, {}).get("picked_up_at"), "parcel_count": done.get(k, {}).get("parcel_count"),
             "on_time": done.get(k, {}).get("on_time")} for k, p in by.items()]


def pickup_window_end(manifest_id: str) -> datetime:
    _, day, run = manifest_id.split(":")
    h, m = map(int, RUNS[run]["pickup"][1].split(":"))
    return datetime.combine(date_cls.fromisoformat(day), time_cls(h, m), TZ)
