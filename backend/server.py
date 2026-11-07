from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
import os
import time
import uuid
import re
from core import ENC_FIELDS, HTML_CSP, client, db, enc_str, init_storage, logger, now_iso
from routes import alerts, auth, awards, legal, billing, dispatch, emails, feedback, invoices, launch, merchants as merchant_routes, onboarding, payments, pickups as pickup_routes, privacy, push, receipts, setup, linkpages, scorecards, square, crashes, account, stops as stop_routes
from services import realtime
from services import seed


app = FastAPI()
app.add_exception_handler(linkpages.LinkPageError, linkpages.link_error_page)


@app.get("/health", include_in_schema=False)
@app.head("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


async def backfill_fees():
    """Orders from before pricing existed get a fee (background, so startup isn't delayed). Pricing cached per store."""
    cache = {}
    for d in await db.stops.find({"merchant_id": {"$ne": None}, "fee": None}, {"_id": 0}).to_list(5000):
        mid = d["merchant_id"]
        if mid not in cache:
            cache[mid] = await billing.get_pricing(mid)
        await db.stops.update_one({"id": d["id"]}, {"$set": {"fee": billing.compute_fee(d, cache[mid])}})


@app.on_event("startup")
async def startup():
    await db.code_redemptions.create_index("code", unique=True)  # a code can only ever be redeemed once
    await db.geocodes.create_index("key", unique=True)
    await db.stops.update_many({"nearby_sms_status": "sending"}, {"$set": {"nearby_sms_status": None}})  # interrupted by a restart: retry
    demo = not await launch.demo_removed()  # after the launch cleanup no sample data is ever re-created
    if demo:
        await seed.seed()
    for m in merchant_routes.MERCHANTS if demo else []:  # insert-only: never overwrites or deletes
        await db.merchants.update_one({"id": m["id"]}, {"$setOnInsert": m}, upsert=True)
    await auth.seed_users(demo)
    realtime.spawn(backfill_fees())
    for did, z in (("drv-1", "North"), ("drv-2", "South"), ("drv-3", "East")) if demo else ():  # demo coverage for auto dispatch
        await db.drivers.update_one({"id": did, "compass_zone": {"$exists": False}}, {"$set": {"compass_zone": z}})
    realtime.spawn(emails.weekly_scheduler())
    realtime.spawn(awards.award_scheduler())
    realtime.spawn(alerts.late_alert_watcher())
    realtime.spawn(onboarding.review_reminder_watcher())
    realtime.spawn(invoices.backfill_invoices())
    realtime.spawn(invoices.platform_fee_watcher())
    realtime.spawn(privacy.retention_watcher())
    realtime.spawn(invoices.batch_progress_watcher())
    realtime.spawn(square.billing_watcher())
    await db.batch_alerts.create_index("id", unique=True)
    await db.missed_pickup_alerts.create_index("id", unique=True)
    await db.driver_shoutouts.create_index("stop_id", unique=True)
    if "manifest_id_1" in await db.pickups.index_information():
        await db.pickups.drop_index("manifest_id_1")  # a run split across routes gets one check-in per route
    await db.pickups.create_index([("manifest_id", 1), ("route_id", 1)], unique=True)
    await db.audit_log.create_index("at")
    for coll, fields in ENC_FIELDS.items():  # one-time: encrypt rows stored before encryption at rest was switched on
        raw = client[os.environ['DB_NAME']][coll]
        for f in fields:
            async for d in raw.find({f: {"$type": "string", "$not": re.compile(r"^enc:"), "$ne": ""}}, {"_id": 1, f: 1}):
                await raw.update_one({"_id": d["_id"]}, {"$set": {f: enc_str(d[f])}})
    # imports interrupted by a restart: send unfinished rows back to the queue
    for j in await db.import_jobs.find({"status": "running"}, {"_id": 0, "id": 1}).to_list(100):
        realtime.spawn(merchant_routes.process_import(j["id"]))
    try:
        await run_in_threadpool(init_storage)
    except Exception as e:
        logger.error(f"Storage init failed: {e}")


@app.middleware("http")
async def audit_and_security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"  # browsers must always use HTTPS
    path = request.url.path
    if path.startswith("/api/") and resp.headers.get("content-type", "").startswith("text/html") and "content-security-policy" not in resp.headers:
        resp.headers["Content-Security-Policy"] = HTML_CSP  # server-rendered pages (proof of delivery): no scripts at all
    if path.startswith("/api/") and request.method != "OPTIONS" and not privacy.AUDIT_SKIP.match(path):
        actor = privacy.audit_actor(request) or ({"id": None, "role": "public"} if request.method != "GET" else None)
        if actor:
            now = time.time()
            key = f"{actor['id']}|{actor['role']}|{request.method}|{path}"
            if request.method != "GET" or now - privacy._audit_seen.get(key, 0) > privacy.AUDIT_DEDUPE_S:  # repeat polling logged once a minute
                privacy._audit_seen[key] = now
                if len(privacy._audit_seen) > 20000:
                    privacy._audit_seen.clear()
                realtime.spawn(privacy.write_audit({"id": str(uuid.uuid4()), "at": now_iso(), "actor_id": actor["id"], "role": actor["role"],
                                   "actor": actor["role"] if not actor["id"] else actor["id"], "method": request.method,
                                   "path": path, "data": privacy.audit_label(path), "status": resp.status_code, "ip": auth.client_ip(request)}))
    return resp


for module in (pickup_routes, dispatch, stop_routes, feedback, scorecards, auth, merchant_routes, emails, alerts, billing, invoices, payments, onboarding, privacy, launch, setup, push, receipts, square, crashes, account, awards, legal):
    app.include_router(module.router)

# Only the app's own web addresses may call the API from a browser (phone apps send no Origin and are unaffected).
CORS_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_credentials=False, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
