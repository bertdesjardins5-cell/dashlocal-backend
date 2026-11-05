from fastapi import APIRouter, HTTPException, Depends, Request
import jwt as pyjwt
from fastapi.responses import Response
from fastapi.concurrency import run_in_threadpool
import asyncio
import uuid
import json
import re
from pydantic import BaseModel, Field
from typing import Optional, Dict
from datetime import datetime, timezone, timedelta
from core import ADMIN_ONLY, CONSENT_VERSION, DEPOT, JWT_ALG, JWT_SECRET, current_user, db, decode_token, issue_token, logger, now_iso, put_object
from routes import auth
from routes import merchants as merchant_routes

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- privacy & compliance (PIPEDA / PHIPA)
CONSENT_TEXT = ("I consent to LocalDash and its designated independent drivers collecting and processing my name, address, and "
                "contact details solely for the purpose of same-day parcel/prescription fulfillment.")
PRIVACY_DEFAULT = {"retention_days": 730}
AUDIT_SKIP = re.compile(r"^/api/(drivers/[^/]+/(location|simulate-step|badges/new|coaching)|rate-card|testimonials|$)")
AUDIT_DEDUPE_S = 60
_audit_seen: Dict[str, float] = {}
AUDIT_LABELS = [  # first match wins: path pattern -> what kind of data was touched
    (r"^/api/files/", "Proof photo"), (r"/invoices|/statement", "Invoices"), (r"^/api/admin/audit", "Audit log"),
    (r"^/api/admin/customers", "Customer data request"), (r"^/api/admin/privacy", "Privacy settings"), (r"^/api/(stops|track|orders)", "Order / customer details"),
    (r"^/api/routes|/route$", "Route & stops"), (r"^/api/merchants/[^/]+/(import|orders|saved-notes)", "Store orders & customers"),
    (r"^/api/merchants", "Store account"), (r"^/api/auth", "Sign-in"), (r"^/api/signup-requests|^/api/admin/signups", "Sign-up requests"),
    (r"^/api/drivers", "Driver"), (r"^/api/admin", "Admin"),
]


def audit_label(path: str) -> str:
    return next((lbl for pat, lbl in AUDIT_LABELS if re.search(pat, path)), "Other")


def audit_actor(request: Request) -> Optional[dict]:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        try:
            p = pyjwt.decode(h[7:].strip(), JWT_SECRET, algorithms=[JWT_ALG])
            return {"id": p.get("sub"), "role": p.get("role")}
        except pyjwt.InvalidTokenError:
            return {"id": None, "role": "invalid-token"}
    if request.headers.get("x-order-token") or request.query_params.get("t"):
        return {"id": None, "role": "customer"}
    return None


async def write_audit(entry: dict):
    try:
        if entry["actor_id"]:
            u = await db.users.find_one({"id": entry["actor_id"]}, {"_id": 0, "email": 1, "driver_id": 1, "merchant_id": 1})
            if u:
                entry["actor"] = u.get("email") or (f"driver {u['driver_id']}" if u.get("driver_id") else entry["actor_id"])
        await db.audit_log.insert_one(entry)
    except Exception as e:
        logger.warning(f"Audit write failed: {e}")


@router.get("/admin/audit", dependencies=ADMIN_ONLY)
async def audit_log(q: Optional[str] = None, role: Optional[str] = None, before: Optional[str] = None, limit: int = 50):
    """Who accessed what data and when (newest first). `before` = the `at` of the last row seen, for paging."""
    flt: dict = {}
    if role:
        flt["role"] = role
    if before:
        flt["at"] = {"$lt": before}
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        flt["$or"] = [{"actor": rx}, {"path": rx}, {"data": rx}, {"ip": rx}]
    rows = await db.audit_log.find(flt, {"_id": 0}).sort("at", -1).to_list(max(1, min(limit, 200)))
    return {"rows": rows, "total": await db.audit_log.count_documents(flt)}


async def get_privacy() -> dict:
    doc = await db.settings.find_one({"key": "privacy"}, {"_id": 0})
    return {**PRIVACY_DEFAULT, **((doc or {}).get("value") or {})}


def retention_cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def old_stop_query(cutoff: str) -> dict:
    """Finished (or never-delivered) orders whose last activity is older than the cutoff and still hold personal details."""
    return {"erased_at": None, "$or": [{"delivered_at": {"$lt": cutoff}}, {"delivered_at": None, "created_at": {"$lt": cutoff}}]}


ERASED = "Erased (retention)"


async def shred_files(paths: list):
    """Delete stored proof photos: the object is overwritten with nothing (storage has no delete call) and the
    file record is marked deleted so it can never be served again."""
    for p in paths:
        try:
            await run_in_threadpool(put_object, p, b"", "application/octet-stream")
        except Exception as e:
            logger.warning(f"Photo shred failed for {p}: {e}")  # still flagged below so it can never be served; retried later
            await db.files.update_one({"storage_path": p}, {"$set": {"is_deleted": True, "deleted_at": now_iso(), "shred_pending": True}})
            continue
        await db.files.update_one({"storage_path": p}, {"$set": {"is_deleted": True, "deleted_at": now_iso(), "size": 0, "shred_pending": False}})


async def erase_stops(ids: list, label: str = ERASED):
    """Remove the person's details from these orders; invoice numbers and totals stay for tax records."""
    if not ids:
        return
    photos = [s["pod_photo_path"] for s in await db.stops.find({"id": {"$in": ids}, "pod_photo_path": {"$nin": [None, ""]}},
                                                              {"_id": 0, "pod_photo_path": 1}).to_list(5000)]
    await shred_files(photos)
    await db.stops.update_many({"id": {"$in": ids}}, {"$set": {
        "customer_name": label, "customer_phone": "", "address": label, "notes": "", "customer_note": None,
        "pod_signature": None, "pod_photo_path": None, "rating_comment": None, "follow_up_note": None,
        "lat": DEPOT["lat"], "lng": DEPOT["lng"], "share_token": None, "erased_at": now_iso()}})
    await db.invoices.update_many({"stop_id": {"$in": ids}}, {"$set": {"delivery.customer_name": label, "delivery.address": label}})
    await db.contact_logs.update_many({"stop_id": {"$in": ids}, "erased_at": {"$exists": False}},  # anonymised, not deleted
                                      {"$set": {"message": None, "erased_at": now_iso()}})


async def erase_saved_notes(query: dict) -> int:
    """Blank saved delivery notes in place (soft erase): the phone is replaced so they never match a customer again."""
    n = 0
    for d in await db.saved_notes.find({**query, "erased_at": {"$exists": False}}, {"_id": 0, "id": 1}).to_list(5000):
        await db.saved_notes.update_one({"id": d["id"]}, {"$set": {"phone": f"erased:{d['id']}", "note": "", "erased_at": now_iso()}})
        n += 1
    return n


CUSTOMER_EXPORT_FIELDS = ("tracking_code", "customer_name", "customer_phone", "address", "postal_code", "status", "created_at",
                          "window_date", "window_start", "window_end", "arrived_at", "delivered_at", "notes", "customer_note",
                          "rating", "rating_comment", "rated_at", "fail_reason", "consent_given", "consent_at", "consent_by", "erased_at")


def customer_phone_or_400(phone: str) -> str:
    p = merchant_routes.normalize_phone(phone or "")
    if not p:
        raise HTTPException(400, "Enter the customer's phone number, e.g. +15195550123")
    return p


async def customer_data(phone: str) -> dict:
    stops = await db.stops.find({"customer_phone": phone}, {"_id": 0}).sort("created_at", -1).to_list(2000)
    merchants = {m["id"]: m["name"] for m in await db.merchants.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)}
    orders = [{**{k: s.get(k) for k in CUSTOMER_EXPORT_FIELDS}, "store": merchants.get(s.get("merchant_id")),
               "has_photo": bool(s.get("pod_photo_path")), "has_signature": bool(s.get("pod_signature"))} for s in stops]
    notes = await db.saved_notes.find({"phone": phone}, {"_id": 0}).to_list(200)
    return {"phone": phone, "orders": orders, "saved_notes": [{"store": merchants.get(n.get("merchant_id")), "note": n.get("note"),
                                                                 "updated_at": n.get("updated_at")} for n in notes]}


@router.get("/admin/customers/lookup", dependencies=ADMIN_ONLY)
async def customer_lookup(phone: str):
    """PIPEDA access request: everything LocalDash holds about one customer, found by phone number."""
    d = await customer_data(customer_phone_or_400(phone))
    names = sorted({o["customer_name"] for o in d["orders"] if o["customer_name"]})
    return {"phone": d["phone"], "names": names, "orders": len(d["orders"]), "saved_notes": len(d["saved_notes"]),
            "latest": d["orders"][:5], "requests": await db.data_requests.find({"phone": d["phone"]}, {"_id": 0}).sort("at", -1).to_list(20)}


@router.post("/admin/customers/export-link", dependencies=ADMIN_ONLY)
async def customer_export_link(phone: str, request: Request):
    p = customer_phone_or_400(phone)
    u = await current_user(request)
    tok = issue_token(u["id"], "admin", purpose="customer-export", minutes=5, phone=p)
    return {"path": f"/api/admin/customers/export.json?token={tok}", "expires_in": 300}


@router.get("/admin/customers/export.json")
async def customer_export(token: str, request: Request):
    t = decode_token(token, "customer-export")
    d = await customer_data(t["phone"])
    await db.data_requests.insert_one({"id": str(uuid.uuid4()), "kind": "export", "phone": t["phone"], "by": t["sub"], "at": now_iso(),
                                       "orders": len(d["orders"]), "ip": auth.client_ip(request)})
    body = {"exported_at": now_iso(), "exported_by": "LocalDash Inc. — PIPEDA access request", **d}
    fname = f"localdash-customer-{t['phone'].lstrip('+')}.json"
    return Response(content=json.dumps(body, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})


class EraseIn(BaseModel):
    phone: str
    confirm: str  # must be "ERASE"


@router.post("/admin/customers/erase", dependencies=ADMIN_ONLY)
async def customer_erase(body: EraseIn, request: Request):
    """PIPEDA erasure request: wipe one customer's details from every order and delete their saved notes."""
    if body.confirm != "ERASE":
        raise HTTPException(400, 'Type ERASE to confirm')
    p = customer_phone_or_400(body.phone)
    u = await current_user(request)
    open_ = await db.stops.count_documents({"customer_phone": p, "status": {"$in": ["pending", "arrived"]}})
    if open_:
        raise HTTPException(409, f"{open_} order{'s are' if open_ > 1 else ' is'} still out for delivery — erase after they are closed")
    ids = [s["id"] for s in await db.stops.find({"customer_phone": p}, {"_id": 0, "id": 1}).to_list(5000)]
    await erase_stops(ids, "Erased (customer request)")
    deleted_notes = await erase_saved_notes({"phone": p})
    await db.data_requests.insert_one({"id": str(uuid.uuid4()), "kind": "erase", "phone": p, "by": u.get("email") or u["id"], "at": now_iso(),
                                       "orders": len(ids), "saved_notes": deleted_notes, "ip": auth.client_ip(request)})
    return {"erased_orders": len(ids), "deleted_saved_notes": deleted_notes}


async def run_retention() -> dict:
    """Anonymise (never delete) personal details older than the retention period. The audit log is kept."""
    p = await get_privacy()
    cutoff = retention_cutoff(p["retention_days"])
    ids = [d["id"] for d in await db.stops.find(old_stop_query(cutoff), {"_id": 0, "id": 1}).to_list(5000)]
    await erase_stops(ids)
    notes = await erase_saved_notes({"$or": [{"updated_at": {"$lt": cutoff}}, {"updated_at": None, "created_at": {"$lt": cutoff}}]})
    await shred_files([f["storage_path"] for f in await db.files.find({"shred_pending": True}, {"_id": 0, "storage_path": 1}).to_list(500)])
    await db.settings.update_one({"key": "privacy"}, {"$set": {"last_run_at": now_iso(), "last_run_erased": len(ids)}}, upsert=True)
    return {"erased_orders": len(ids), "deleted_saved_notes": notes, "cutoff": cutoff}


async def retention_watcher():
    await asyncio.sleep(30)
    while True:
        try:
            await run_retention()
        except Exception as e:
            logger.warning(f"Retention loop error: {e}")
        await asyncio.sleep(6 * 3600)


class PrivacyIn(BaseModel):
    retention_days: int = Field(ge=30, le=3650)


@router.get("/admin/privacy", dependencies=ADMIN_ONLY)
async def privacy_settings():
    p = await get_privacy()
    doc = await db.settings.find_one({"key": "privacy"}, {"_id": 0}) or {}
    cutoff = retention_cutoff(p["retention_days"])
    soon = retention_cutoff(p["retention_days"] - 30)
    return {**p, "last_run_at": doc.get("last_run_at"), "last_run_erased": doc.get("last_run_erased"),
            "due_now": await db.stops.count_documents(old_stop_query(cutoff)),
            "due_within_30_days": await db.stops.count_documents(old_stop_query(soon)),
            "erased_total": await db.stops.count_documents({"erased_at": {"$ne": None}}),
            "consent_text": CONSENT_TEXT}


@router.put("/admin/privacy", dependencies=ADMIN_ONLY)
async def update_privacy(body: PrivacyIn):
    await db.settings.update_one({"key": "privacy"}, {"$set": {"value.retention_days": body.retention_days, "updated_at": now_iso()}}, upsert=True)
    return await privacy_settings()


@router.post("/admin/privacy/run-retention", dependencies=ADMIN_ONLY)
async def run_retention_now():
    return await run_retention()


@router.get("/privacy/consent-text")
async def consent_text():
    return {"text": CONSENT_TEXT, "version": CONSENT_VERSION}


PRIVACY_OFFICER_DEFAULT = {"officer_name": "Albert Desjardins", "officer_email": "bertdesjardins@hotmail.com"}


@router.get("/privacy/officer")
async def privacy_officer():
    """Public: who to contact about privacy (shown on the Privacy Policy page)."""
    p = await get_privacy()
    return {k: p.get(k) or v for k, v in PRIVACY_OFFICER_DEFAULT.items()}


class OfficerIn(BaseModel):
    officer_name: str = Field(min_length=2, max_length=80)
    officer_email: str = Field(max_length=120)


@router.put("/admin/privacy/officer", dependencies=ADMIN_ONLY)
async def set_privacy_officer(body: OfficerIn):
    email = body.officer_email.strip().lower()
    name = " ".join(body.officer_name.split())
    if len(name) < 2:
        raise HTTPException(400, "Enter the Privacy Officer's full name")
    if not auth.EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email")
    await db.settings.update_one({"key": "privacy"}, {"$set": {"value.officer_name": name, "value.officer_email": email}}, upsert=True)
    return await privacy_officer()


class MyConsentIn(BaseModel):
    consent_timestamp: str


@router.get("/me/consent")
async def my_consent(u: dict = Depends(current_user)):
    """Has this login accepted the current privacy & compliance terms (consent modal)?"""
    return {"phipa_consent_given": bool(u.get("phipa_consent_given")) and u.get("consent_version") == CONSENT_VERSION,
            "consent_timestamp": u.get("consent_timestamp"), "consent_recorded_at": u.get("consent_recorded_at"), "role": u["role"]}


@router.post("/me/consent")
async def give_consent(body: MyConsentIn, u: dict = Depends(current_user)):
    """Record acceptance: the device timestamp from the modal plus the server's own time (the authoritative one)."""
    rec = {"phipa_consent_given": True, "consent_timestamp": body.consent_timestamp[:40], "consent_recorded_at": now_iso(),
           "consent_version": CONSENT_VERSION}
    await db.users.update_one({"id": u["id"]}, {"$set": rec})
    await db.consent_log.insert_one({"id": str(uuid.uuid4()), "user_id": u["id"], "role": u["role"], **rec})
    return {**rec, "user_id": u["id"], "role": u["role"]}
