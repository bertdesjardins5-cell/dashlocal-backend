from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from core import ADMIN_ONLY, db, now_iso
from routes import auth
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
