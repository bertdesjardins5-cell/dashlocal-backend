from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pymongo.errors import DuplicateKeyError
import os
from html import escape
import uuid
import re
from twilio.rest import Client as TwilioClient
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timedelta
from core import ADMIN_ONLY, ROUTE_DRIVER, RUNS, TZ, db, hhmm12, logger, now_iso
from models import Stop, pickup_window_end
from routes import auth
from routes import emails
from routes import onboarding
from routes import stops as stop_routes
from services import realtime

router = APIRouter(prefix="/api")


class PickupIn(BaseModel):
    parcel_count: int = Field(ge=0, le=500)


@router.post("/routes/{route_id}/pickups/{manifest_id}/check-in", dependencies=ROUTE_DRIVER)
async def pickup_check_in(route_id: str, manifest_id: str, body: PickupIn):
    """Driver confirms a pharmacy pickup: time + how many parcels they collected (shop sees it in the portal)."""
    if not await db.stops.find_one({"route_id": route_id, "manifest_id": manifest_id}, {"_id": 1}):
        raise HTTPException(404, "That pickup is not on this route")
    if await db.pickups.find_one({"manifest_id": manifest_id, "route_id": route_id}, {"_id": 1}):
        raise HTTPException(400, "This pickup is already checked in")
    expected = await db.stops.count_documents({"manifest_id": manifest_id, "route_id": route_id})  # parcels for this driver's route
    now = datetime.now(TZ)
    rdoc = await db.routes.find_one({"id": route_id}, {"_id": 0, "driver_id": 1})
    doc = {"id": f"{manifest_id}|{route_id}", "manifest_id": manifest_id, "merchant_id": manifest_id.split(":")[0], "route_id": route_id, "driver_id": (rdoc or {}).get("driver_id"),
           "picked_up_at": now_iso(), "parcel_count": body.parcel_count, "expected": expected, "on_time": now <= pickup_window_end(manifest_id)}
    try:
        await db.pickups.insert_one(dict(doc))
    except DuplicateKeyError:
        raise HTTPException(400, "This pickup is already checked in")
    await realtime.broadcast_route(route_id)
    realtime.spawn(text_patients_left_store(manifest_id, route_id))
    return doc


async def text_patients_left_store(manifest_id: str, route_id: str):
    """Pickup checked in: text each patient that their delivery is on its way, with the run's delivery window.
    Wording never mentions medication or the pharmacy type (texts show on locked phones)."""
    m = await db.merchants.find_one({"id": manifest_id.split(":")[0]}, {"_id": 0, "name": 1}) or {}
    r = RUNS[manifest_id.split(":")[2]]
    window = f"{hhmm12(r['window'][0])}–{hhmm12(r['window'][1])}"
    store = re.sub(r"\b(pharmacy|pharmacie|drug\s*store|drugs|apothecary|rx|chemist|prescriptions?|medications?|meds)\b", "", m.get("name") or "", flags=re.I)
    store = re.sub(r"\s{2,}", " ", store).strip(" -·&,") or "your local store"
    for d in await db.stops.find({"manifest_id": manifest_id, "route_id": route_id, "status": {"$in": ["pending", "arrived"]}}, {"_id": 0}).to_list(500):
        st = Stop(**d)
        if await db.contact_logs.find_one({"stop_id": st.id, "kind": "left_store"}, {"_id": 1}):
            continue
        body = (f"DashLocal: your delivery {st.tracking_code} from {store} has left the store and is on its way. "
                f"Expected today {window} (Eastern time). Someone will need to sign for it.")
        status = "skipped_fictional" if stop_routes.FICTIONAL_NUMBER.match(st.customer_phone or "") else "not_configured"
        sid = None
        if status != "skipped_fictional" and os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_PHONE_NUMBER"):
            try:
                tw = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
                res = await run_in_threadpool(lambda: tw.messages.create(to=st.customer_phone, from_=stop_routes.twilio_number(), body=body))
                status, sid = res.status, res.sid
            except Exception as e:
                logger.error(f"Left-store SMS failed for {st.tracking_code}: {e}")
                status = "failed"
        await db.contact_logs.insert_one({"id": str(uuid.uuid4()), "stop_id": st.id, "channel": "sms", "kind": "left_store",
                                          "message": body, "twilio_sid": sid, "status": status, "created_at": now_iso()})


MISSED_PICKUP_GRACE_H = 3  # only alert within this many hours after the window ends (avoids alerting on old days at startup)


async def send_missed_pickup_alerts(now: Optional[datetime] = None) -> list:
    """After a run's pickup window ends, any pharmacy manifest today with drops not checked in → one email to admin + pharmacy."""
    now = now or datetime.now(TZ)
    day = now.date().isoformat()
    sent = []
    for key, r in RUNS.items():
        end = pickup_window_end(f"x:{day}:{key}")
        if not (end <= now < end + timedelta(hours=MISSED_PICKUP_GRACE_H)):
            continue
        for m in await db.merchants.find({"pharmacy_partner": True}, {"_id": 0}).to_list(500):
            mid = f"{m['id']}:{day}:{key}"
            stops = await db.stops.find({"manifest_id": mid, "status": {"$in": ["pending", "arrived"]}, "erased_at": None}, {"_id": 0, "route_id": 1}).to_list(1000)
            if not stops:
                continue
            done = {p["route_id"] for p in await db.pickups.find({"manifest_id": mid}, {"_id": 0, "route_id": 1}).to_list(50)}
            missing = sum(1 for s in stops if not s.get("route_id") or s["route_id"] not in done)
            if not missing:
                continue
            try:
                await db.missed_pickup_alerts.insert_one({"id": mid, "merchant_id": m["id"], "run": key, "missing": missing, "created_at": now_iso()})
            except DuplicateKeyError:
                continue
            realtime.spawn(email_missed_pickup(m, r, mid, missing, len(stops)))
            sent.append(mid)
    return sent


async def email_missed_pickup(m: dict, r: dict, mid: str, missing: int, total: int):
    subject = f"Missed pickup: {m['name']} — {r['label']} ({missing} of {total} drops not collected)"
    body = (f"<p>The <b>{r['label']}</b> pickup window at <b>{escape(m['name'])}</b> ended at <b>{hhmm12(r['pickup'][1])}</b>, "
            f"but <b>{missing} of {total}</b> drops have not been checked in by a driver.</p>"
            f"<p>Dispatch is following up. Pharmacy: if the parcels were collected, no action is needed; otherwise please keep them ready.</p>")
    await onboarding.notify_admin(subject, body, None)
    for to in await merchant_alert_recipients(m["id"]):
        try:
            await emails.send_email(to=to, subject=subject, html=onboarding.brand_email(subject, body))
        except Exception as e:
            logger.warning(f"Missed pickup email failed: {e}")


async def merchant_alert_recipients(merchant_id: str) -> list:
    demo = {em for _, em, _ in auth.DEMO_MERCHANT_USERS}
    return [emails.TEST_INBOX if u["email"] in demo else u["email"]
            for u in await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0, "email": 1}).to_list(20)]


@router.post("/admin/missed-pickups/run", dependencies=ADMIN_ONLY)
async def run_missed_pickups(at: Optional[str] = None):
    """Run the missed-pickup check now; `at` (ISO, Eastern) pretends it is that time — for testing."""
    try:
        now = datetime.fromisoformat(at).replace(tzinfo=TZ) if at else None
    except ValueError:
        raise HTTPException(400, "at must be an ISO date-time, e.g. 2026-06-01T14:40:00")
    return {"sent": await send_missed_pickup_alerts(now)}
