from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import re
import secrets
import uuid
from core import ADMIN_ONLY, DEPOT, DRIVER_TERMS_VERSION, db, hash_pw, now_iso, path_length_m
from models import Driver, Route, Stop
from routes import auth, onboarding
from routes import privacy
from services import realtime

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------- launch cleanup
# Everything a launch wipes. Kept on purpose: the admin login, settings, audit_log and consent_log (PIPEDA/PHIPA
# records of who accessed what and who agreed to what), data_requests (privacy request history) and the geocode cache.
WIPE = ["drivers", "stops", "routes", "invoices", "bill_payments", "payment_sessions", "square_charges", "square_locks", "merchants", "pickups",
        "coaching_notes", "code_redemptions", "import_jobs", "import_rows", "merchant_alerts", "report_emails",
        "review_requests", "testimonials", "signup_requests", "driver_shoutouts", "batch_alerts", "missed_pickup_alerts",
        "contact_logs", "saved_notes", "last_customer_notes", "counters", "login_attempts"]


async def demo_removed() -> bool:
    """Once the launch cleanup ran, the sample data is never seeded again (startup or demo reset)."""
    return bool(await db.settings.find_one({"key": "demo_removed"}, {"_id": 1}))


class CleanupIn(BaseModel):
    confirm: str


@router.get("/admin/launch-cleanup", dependencies=ADMIN_ONLY)
async def cleanup_status(request: Request):
    done = await db.settings.find_one({"key": "demo_removed"}, {"_id": 0})
    counts = {c: await db[c].count_documents({}) for c in ("merchants", "drivers", "stops", "routes", "invoices")}
    counts["logins"] = await db.users.count_documents({"role": {"$ne": "admin"}})
    preview = auth.is_preview(request)
    return {"preview": preview, "done_at": (done or {}).get("value"), "available": not preview and not done, "counts": counts}


@router.post("/admin/launch-cleanup", dependencies=ADMIN_ONLY)
async def launch_cleanup(body: CleanupIn, request: Request):
    if auth.is_preview(request):
        raise HTTPException(403, "Launch cleanup only runs on the live app — the preview keeps its sample data for testing")
    if body.confirm.strip().upper() != "DELETE":
        raise HTTPException(400, "Type DELETE to confirm")
    if await demo_removed():
        raise HTTPException(409, "Launch cleanup has already run")
    removed = {c: (await db[c].delete_many({})).deleted_count for c in WIPE}
    removed["users"] = (await db.users.delete_many({"role": {"$ne": "admin"}})).deleted_count
    paths = [f["storage_path"] for f in await db.files.find({"is_deleted": {"$ne": True}}, {"_id": 0, "storage_path": 1}).to_list(100000)]
    await db.files.update_many({}, {"$set": {"is_deleted": True, "deleted_at": now_iso(), "shred_pending": True}})  # never served; retried if interrupted
    realtime.spawn(privacy.shred_files(paths))  # overwrite the stored photos in the background
    removed["files"] = len(paths)
    await db.settings.update_one({"key": "demo_removed"}, {"$set": {"key": "demo_removed", "value": now_iso()}}, upsert=True)
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------- Play Store reviewer kit
REVIEW_MID, REVIEW_DID, REVIEW_RID = "mer-review", "drv-review", "route-review"
REVIEW_EMAIL, REVIEW_PHONE = "review-store@arrivewise.app", "+15195550199"  # 555-01xx = fictional: no real texts go out
REVIEW_STOPS = [("RV1001", "Alex Morgan", "+15195550190", "200 King St W, Kitchener", 43.4516, -80.4925),
                ("RV1002", "Jamie Lee", "+15195550191", "30 Duke St W, Kitchener", 43.4530, -80.4880),
                ("RV1003", "Sam Taylor", "+15195550192", "105 Frederick St, Kitchener", 43.4545, -80.4850)]


def strong_pin() -> str:
    while True:
        pin = f"{secrets.randbelow(10 ** 6):06d}"
        if pin not in auth.WEAK_PINS and len(set(pin)) > 3:
            return pin


async def reviewer_status() -> dict:
    u = await db.users.find_one({"email": REVIEW_EMAIL}, {"_id": 0, "id": 1})
    r = await db.routes.find_one({"id": REVIEW_RID}, {"_id": 0, "status": 1, "service_date": 1})
    return {"exists": bool(u), "store_email": REVIEW_EMAIL, "driver_phone": REVIEW_PHONE, "route": r,
            "orders": [{"tracking_code": c, "phone_last4": p[-4:], "address": a} for c, _, p, a, _, _ in REVIEW_STOPS]}


@router.get("/admin/reviewer-kit", dependencies=ADMIN_ONLY)
async def get_reviewer_kit():
    return await reviewer_status()


@router.post("/admin/reviewer-kit", dependencies=ADMIN_ONLY)
async def make_reviewer_kit():
    """Create (or reset) the Play Store reviewer store, driver and a fresh 3-stop route for today.
    New password and PIN every time; they are shown once and never stored in plain text."""
    pw, pin = secrets.token_urlsafe(9), strong_pin()
    await db.merchants.update_one({"id": REVIEW_MID}, {"$set": {"name": "ArriveWise Review Store", "contact": REVIEW_EMAIL, "business_type": "store",
                                                                 "signature_required": False, "weekly_email": False}}, upsert=True)
    await db.users.update_one({"email": REVIEW_EMAIL}, {"$set": {
        "role": "merchant", "merchant_id": REVIEW_MID, "password_hash": hash_pw(pw), "disabled": False, "terms_consent": True,
        "terms_version": onboarding.TERMS_VERSION, "terms_consent_at": now_iso()},
        "$setOnInsert": {"id": str(uuid.uuid4()), "email": REVIEW_EMAIL, "name": "Play Store reviewer", "created_at": now_iso()}}, upsert=True)
    await db.drivers.update_one({"id": REVIEW_DID}, {"$set": Driver(id=REVIEW_DID, name="Robin Reviewer", phone=REVIEW_PHONE, vehicle="VAN-RV",
                                                                     lat=DEPOT["lat"], lng=DEPOT["lng"], updated_at=now_iso()).model_dump()}, upsert=True)
    await db.users.update_one({"role": "driver", "driver_id": REVIEW_DID}, {"$set": {
        "pin_hash": hash_pw(pin), "disabled": False, "driver_terms_version": DRIVER_TERMS_VERSION, "driver_terms_at": now_iso()},
        "$setOnInsert": {"id": str(uuid.uuid4()), "role": "driver", "driver_id": REVIEW_DID, "created_at": now_iso()}}, upsert=True)
    await db.login_attempts.delete_many({"key": {"$regex": f"^(drv:{re.escape(REVIEW_PHONE)}:|{re.escape(REVIEW_EMAIL)}:)"}})
    await db.stops.delete_many({"merchant_id": REVIEW_MID})
    await db.routes.delete_one({"id": REVIEW_RID})
    stops = [Stop(id=str(uuid.uuid4()), route_id=REVIEW_RID, seq=i + 1, merchant_id=REVIEW_MID, customer_name=n, customer_phone=p, address=a,
                  postal_code="N2G", zone="Downtown", lat=lat, lng=lng, tracking_code=c, notes="Play Store review order")
             for i, (c, n, p, a, lat, lng) in enumerate(REVIEW_STOPS)]
    await db.stops.insert_many([s.model_dump() for s in stops])
    await db.routes.insert_one(Route(id=REVIEW_RID, name="R-REVIEW Downtown", zone="Downtown", driver_id=REVIEW_DID,
                                     planned_distance_m=round(path_length_m(DEPOT, [{"lat": s.lat, "lng": s.lng} for s in stops]))).model_dump())
    return {**(await reviewer_status()), "store_password": pw, "driver_pin": pin}
