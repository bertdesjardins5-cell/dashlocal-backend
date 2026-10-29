from fastapi import HTTPException
import random
import uuid
import re
import string
from typing import List, Optional
from datetime import datetime, timezone, timedelta, date as date_cls, time as time_cls
from core import DEPOT, MAX_BOOKING_DAYS, TZ, db, now_iso, optimize_sequence, path_length_m, plan_base, today_str
from models import Driver, Route, Stop
from routes import feedback
from routes import invoices

# ---------------------------------------------------------------- seed
ZONES = {
    "Downtown": {"postal": "N2G", "center": (43.4500, -80.4890), "city": "Kitchener", "streets": ["King St W", "Queen St S", "Duke St W", "Frederick St", "Victoria St N"]},
    "Uptown": {"postal": "N2J", "center": (43.4650, -80.5220), "city": "Waterloo", "streets": ["King St N", "Erb St W", "Regina St N", "Albert St", "Caroline St N"]},
    "Westmount": {"postal": "N2M", "center": (43.4420, -80.5150), "city": "Kitchener", "streets": ["Westmount Rd W", "Glasgow St", "Highland Rd W", "Belmont Ave W", "Union Blvd"]},
    "Stanley Park": {"postal": "N2A", "center": (43.4560, -80.4520), "city": "Kitchener", "streets": ["Ottawa St N", "Weber St E", "Fairway Rd N", "Lackner Blvd", "River Rd E"]},
}
NAMES = ["Ava Chen", "Liam Patel", "Noah Garcia", "Mia Johnson", "Ethan Kim", "Zoe Martinez", "Lucas Brown", "Ivy Nguyen",
         "Omar Haddad", "Sara Lopez", "Ben Carter", "Nina Rossi", "Leo Park", "Emma Wilson", "Kai Tanaka", "Ruby Singh",
         "Jack Moore", "Lila Ahmed", "Owen Diaz", "Chloe Evans", "Max Fischer", "Aria Shah", "Theo Clark", "Maya Reed"]


async def seed(force: bool = False):
    """Demo data. On startup it only fills an empty database (inserts, never deletes). Wiping old demo data
    happens only with force=True, from the explicit admin "Reset demo" action."""
    if not force and await db.drivers.count_documents({}) > 0:
        return
    if force:
        await wipe_demo()
    await insert_demo()
    from routes import merchants as merchant_routes  # lazy: routes.merchants imports this module
    for m in merchant_routes.MERCHANTS:
        await db.merchants.update_one({"id": m["id"]}, {"$setOnInsert": m}, upsert=True)


async def wipe_demo():
    await db.drivers.delete_many({})
    await db.code_redemptions.delete_many({})
    await db.coaching_notes.delete_many({})
    await db.import_jobs.delete_many({})
    await db.import_rows.delete_many({})
    await db.merchant_alerts.delete_many({})
    await db.bill_payments.delete_many({})
    await db.invoices.delete_many({})
    await db.counters.delete_many({"key": "invoice"})
    await db.payment_sessions.delete_many({})
    await db.report_emails.delete_many({})
    await db.stops.delete_many({})
    await db.routes.delete_many({})


async def insert_demo():
    rnd = random.Random(42)
    drivers = [
        Driver(id="drv-1", name="Marcus Reid", phone="+15195550101", vehicle="VAN-07", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso(), compass_zone="North"),
        Driver(id="drv-2", name="Priya Nair", phone="+15195550102", vehicle="VAN-12", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso(), compass_zone="South"),
        Driver(id="drv-3", name="Diego Santos", phone="+15195550103", vehicle="BIKE-03", lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso(), compass_zone="East"),
    ]
    await db.drivers.insert_many([d.model_dump() for d in drivers])
    stops, n = [], 0
    for zone, z in ZONES.items():
        for _ in range(6):
            lat = z["center"][0] + rnd.uniform(-0.007, 0.007)
            lng = z["center"][1] + rnd.uniform(-0.008, 0.008)
            stops.append(Stop(id=str(uuid.uuid4()), customer_name=NAMES[n % len(NAMES)], customer_phone=f"+1519555{100 + n:04d}",
                              address=f"{rnd.randint(10, 900)} {rnd.choice(z['streets'])}, {z['city']}", postal_code=z["postal"],
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
    await create_batch("Downtown", "drv-1", start=True)
    await seed_history(rnd, drivers)
    # demo: keep the most recent low rating open so the admin follow-up list is never empty
    low = await db.stops.find({"rating": {"$lte": 2}}, {"_id": 0, "id": 1}).sort("rated_at", -1).to_list(1)
    if low:
        await db.stops.update_one({"id": low[0]["id"]}, {"$set": {"follow_up_status": "open"}})
    await invoices.ensure_platform_invoices()


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
                                 customer_phone=f"+1519555{100 + rnd.randint(0, 99):04d}", address=f"{rnd.randint(10, 900)} {rnd.choice(z['streets'])}, {z['city']}",
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
                                                "order_value": value, "discount": round(value * feedback.APOLOGY_DISCOUNT_PCT / 100, 2), "order_ref": f"ORD-{rnd.randint(10000, 99999)}",
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
