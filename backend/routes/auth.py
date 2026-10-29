from fastapi import APIRouter, HTTPException, Depends, Request
from pymongo.errors import DuplicateKeyError
import os
import math
import uuid
import re
import secrets
from html import escape
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from fastapi.concurrency import run_in_threadpool
from core import ADMIN_ONLY, DEPOT, DUMMY_HASH, admin_user, check_pw, current_user, db, hash_pw, issue_token, logger, now_iso
from models import Driver
from routes import merchants as merchant_routes
from routes import onboarding
from routes import stops as stop_routes

router = APIRouter(prefix="/api")


LOGIN_MAX_FAILS = 5
LOGIN_LOCK_MIN = 15
DEMO_MERCHANT_USERS = [("mer-1", "bakery@dashlocal.app", "Bakery2026"), ("mer-2", "books@dashlocal.app", "Books2026")]
DEMO_DRIVER_PINS = {"drv-1": "482913", "drv-2": "730516", "drv-3": "264871"}


def is_preview(request: Request) -> bool:
    """Preview/dev host (the ingress routes by Host, so the live app never receives a preview Host)."""
    host = request.headers.get("host", "").split(":")[0].lower()
    return host.endswith((".preview.emergentagent.com", ".preview.emergentcf.cloud"))


def demo_secret_blocked(request: Request, key: str, secret: str) -> bool:
    """The seeded demo passwords/PINs are public (in the code), so they never work on the live app.
    Once changed to a real secret the account works everywhere."""
    demo = {em: p for _, em, p in DEMO_MERCHANT_USERS} | DEMO_DRIVER_PINS
    return key in demo and secret == demo[key] and not is_preview(request)


async def seed_users(demo: bool = True):
    info = await db.users.index_information()
    if "email_1" in info and "partialFilterExpression" not in info["email_1"]:
        await db.users.drop_index("email_1")  # drivers have no email: uniqueness only applies to real emails
    await db.users.create_index("email", unique=True, partialFilterExpression={"email": {"$type": "string"}})
    await db.users.create_index("driver_id", unique=True, partialFilterExpression={"driver_id": {"$type": "string"}})
    for did, pin in DEMO_DRIVER_PINS.items():
        if demo and not await db.users.find_one({"role": "driver", "driver_id": did}):
            await db.users.insert_one({"id": str(uuid.uuid4()), "role": "driver", "driver_id": did, "pin_hash": hash_pw(pin),
                                       "disabled": False, "created_at": now_iso()})
    email, pw = os.environ["ADMIN_EMAIL"].strip().lower(), os.environ["ADMIN_PASSWORD"]
    admin = await db.users.find_one({"email": email})
    if not admin:
        await db.users.insert_one({"id": str(uuid.uuid4()), "email": email, "password_hash": hash_pw(pw), "role": "admin",
                                   "name": "Dispatch Admin", "merchant_id": None, "disabled": False, "created_at": now_iso()})
    elif not check_pw(pw, admin["password_hash"]):
        await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_pw(pw)}})  # .env is the source of truth
    for mid, em, p in DEMO_MERCHANT_USERS:
        if demo and not await db.users.find_one({"email": em}):
            await db.users.insert_one({"id": str(uuid.uuid4()), "email": em, "password_hash": hash_pw(p), "role": "merchant",
                                       "name": None, "merchant_id": mid, "disabled": False, "created_at": now_iso()})


class LoginIn(BaseModel):
    email: str
    password: str


def client_ip(request: Request) -> str:
    # the ingress appends the real client IP as the LAST X-Forwarded-For hop; earlier hops are client-controlled
    return request.headers.get("x-forwarded-for", "").split(",")[-1].strip() or (request.client.host if request.client else "-")


async def check_lockout(limits: List[tuple]) -> dict:
    """Raise 429 while any key is locked; returns the live attempt docs (old failures expire)."""
    now = datetime.now(timezone.utc)
    atts = {}
    for key, _ in limits:
        att = await db.login_attempts.find_one({"key": key}, {"_id": 0})
        if att and att.get("locked_until") and datetime.fromisoformat(att["locked_until"]) > now:
            mins = max(1, math.ceil((datetime.fromisoformat(att["locked_until"]) - now).total_seconds() / 60))
            raise HTTPException(429, f"Too many attempts — try again in {mins} min")
        if att and datetime.fromisoformat(att["updated_at"]) < now - timedelta(minutes=LOGIN_LOCK_MIN):
            att = None
        atts[key] = att
    return atts


async def record_failure(limits: List[tuple], atts: dict):
    now = datetime.now(timezone.utc)
    for key, cap in limits:
        fails = (atts.get(key) or {}).get("fails", 0) + 1
        upd = {"key": key, "fails": fails, "updated_at": now.isoformat(), "locked_until": None}
        if fails >= cap:
            upd.update(fails=0, locked_until=(now + timedelta(minutes=LOGIN_LOCK_MIN)).isoformat())
        await db.login_attempts.update_one({"key": key}, {"$set": upd}, upsert=True)


async def clear_lockout(limits: List[tuple]):
    await db.login_attempts.delete_many({"key": {"$in": [k for k, _ in limits if not k.startswith("drvip:")]}})


class DriverLoginIn(BaseModel):
    phone: str
    pin: str


PIN_RE = re.compile(r"^\d{6}$")
WEAK_PINS = {"123456", "654321", "123123", "121212", "112233", "000000"}


@router.post("/auth/driver-login")
async def driver_login(body: DriverLoginIn, request: Request):
    """Driver sign-in with phone + 6-digit PIN. A PIN has only 1M combinations, so limits are tight:
    5 tries per phone per device IP, 10 per phone from anywhere, 30 per IP across all phones."""
    phone = merchant_routes.normalize_phone(body.phone) or body.phone.strip()
    ip = client_ip(request)
    limits = [(f"drv:{phone}:{ip}", 5), (f"drv:{phone}:*", 10), (f"drvip:{ip}", 30)]
    atts = await check_lockout(limits)
    d = await db.drivers.find_one({"phone": phone}, {"_id": 0})
    u = await db.users.find_one({"role": "driver", "driver_id": d["id"]}, {"_id": 0}) if d else None
    ok = check_pw(body.pin.strip(), (u or {}).get("pin_hash") or DUMMY_HASH) and bool(PIN_RE.match(body.pin.strip()))
    if not (d and u and ok) or u.get("disabled") or demo_secret_blocked(request, d["id"], body.pin.strip()):
        await record_failure(limits, atts)
        raise HTTPException(401, "Wrong phone number or PIN")
    await clear_lockout(limits)
    await db.users.update_one({"id": u["id"]}, {"$set": {"last_login_at": now_iso()}})
    await merchant_routes.remember_public_base(request)
    user = {"id": u["id"], "role": "driver", "driver_id": d["id"], "driver_name": d["name"], "phone": d["phone"], "email": None, "merchant_id": None}
    return {"token": issue_token(u["id"], "driver"), "user": user}


# ---- shop testimonials for the public landing page (admin-managed)
class TestimonialIn(BaseModel):
    quote: str
    person: Optional[str] = None
    shop: str
    published: bool = True


def clean_testimonial(body: TestimonialIn) -> dict:
    quote, shop = " ".join(body.quote.split())[:400], " ".join(body.shop.split())[:80]
    if not quote or not shop:
        raise HTTPException(400, "A quote and the shop name are required")
    return {"quote": quote, "shop": shop, "person": (" ".join((body.person or "").split())[:60] or None), "published": body.published}


@router.get("/testimonials")
async def public_testimonials():
    """Published shop quotes for the landing page (newest first, max 6)."""
    return await db.testimonials.find({"published": True}, {"_id": 0, "id": 1, "quote": 1, "person": 1, "shop": 1}).sort("created_at", -1).to_list(6)


@router.get("/admin/testimonials", dependencies=ADMIN_ONLY)
async def admin_testimonials():
    return await db.testimonials.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)


@router.post("/admin/testimonials", dependencies=ADMIN_ONLY)
async def create_testimonial(body: TestimonialIn):
    doc = {"id": str(uuid.uuid4()), **clean_testimonial(body), "created_at": now_iso(), "updated_at": now_iso()}
    await db.testimonials.insert_one(dict(doc))
    return doc


@router.put("/admin/testimonials/{tid}", dependencies=ADMIN_ONLY)
async def update_testimonial(tid: str, body: TestimonialIn):
    res = await db.testimonials.update_one({"id": tid}, {"$set": {**clean_testimonial(body), "updated_at": now_iso()}})
    if not res.matched_count:
        raise HTTPException(404, "Review not found")
    return await db.testimonials.find_one({"id": tid}, {"_id": 0})


@router.delete("/admin/testimonials/{tid}", dependencies=ADMIN_ONLY)
async def delete_testimonial(tid: str):
    if not (await db.testimonials.delete_one({"id": tid})).deleted_count:
        raise HTTPException(404, "Review not found")
    return {"ok": True}



class PinIn(BaseModel):
    pin: str


@router.get("/admin/driver-accounts", dependencies=ADMIN_ONLY)
async def driver_accounts():
    users = {u["driver_id"]: u for u in await db.users.find({"role": "driver"}, {"_id": 0, "pin_hash": 0}).to_list(500)}
    return [{"id": d["id"], "name": d["name"], "phone": d["phone"], "vehicle": d["vehicle"],
             "has_pin": d["id"] in users, "last_login_at": (users.get(d["id"]) or {}).get("last_login_at"), "pin_request_at": d.get("pin_request_at")}
            for d in await db.drivers.find({}, {"_id": 0}).sort("id", 1).to_list(500)]


class NewDriverIn(BaseModel):
    name: str
    phone: str
    vehicle: str
    pin: str
    compass_zone: Optional[str] = None


@router.post("/admin/drivers", dependencies=ADMIN_ONLY)
async def create_driver(body: NewDriverIn, request: Request):
    name, vehicle = body.name.strip(), body.vehicle.strip()
    phone = merchant_routes.normalize_phone(body.phone)
    if not name or not vehicle:
        raise HTTPException(400, "Name and vehicle are required")
    if not phone:
        raise HTTPException(400, "Enter a valid phone number")
    if body.compass_zone not in (None, "North", "South", "East", "West"):
        raise HTTPException(400, "Zone must be North, South, East or West")
    if await db.drivers.find_one({"phone": phone}, {"_id": 1}):
        raise HTTPException(409, "A driver with this phone number already exists")
    pin = body.pin.strip()
    if not PIN_RE.match(pin):
        raise HTTPException(400, "PIN must be exactly 6 digits")
    if pin in WEAK_PINS or len(set(pin)) == 1:
        raise HTTPException(400, "PIN is too easy to guess — pick another")
    d = Driver(id=f"drv-{uuid.uuid4().hex[:8]}", name=name, phone=phone, vehicle=vehicle, lat=DEPOT["lat"], lng=DEPOT["lng"],
               updated_at=now_iso(), compass_zone=body.compass_zone)
    base = await onboarding.app_base(request)  # before saving, so a missing app address can't leave a half-created driver
    await db.drivers.insert_one(d.model_dump())
    await db.users.insert_one({"id": str(uuid.uuid4()), "role": "driver", "driver_id": d.id, "pin_hash": hash_pw(pin),
                               "disabled": False, "created_at": now_iso()})
    invite = await send_driver_invite(d, pin, base)
    return {"id": d.id, "name": d.name, "phone": d.phone, "vehicle": d.vehicle, "has_pin": True, "last_login_at": None, "invite": invite}


async def send_driver_invite(d: Driver, pin: str, base: str, reset: bool = False) -> str:
    """Welcome text with the sign-in link, phone and PIN (owner's choice). Never blocks creating the driver."""
    body = (f"DashLocal: {d.name.split()[0]}, your driver PIN was reset. New PIN {pin}. Sign in at {base}/sign-in/staff "
            f"(Driver tab) with this phone number. Keep your PIN private.") if reset else (
            f"Welcome to DashLocal, {d.name.split()[0]}! Sign in to the driver app at {base}/sign-in/staff "
            f"(Driver tab) with this phone number and PIN {pin}. Keep your PIN private.")
    try:
        tw = stop_routes.twilio_client()
        await run_in_threadpool(lambda: tw.messages.create(to=d.phone, from_=stop_routes.twilio_number(), body=body))
        status = "sent"
    except HTTPException:
        status = "not_configured"
    except Exception as e:
        logger.warning(f"Driver invite SMS failed for {d.id}: {e}")
        status = "failed"
    await db.drivers.update_one({"id": d.id}, {"$set": {"invite_status": status, "invite_sent_at": now_iso()}})
    return status


class MerchantSettingsIn(BaseModel):
    business_type: Optional[str] = None
    signature_required: Optional[bool] = None


@router.put("/admin/merchants/{merchant_id}/settings", dependencies=ADMIN_ONLY)
async def merchant_settings(merchant_id: str, body: MerchantSettingsIn):
    upd = {}
    if body.business_type is not None:
        if body.business_type not in onboarding.BUSINESS_TYPES:
            raise HTTPException(400, "Unknown business type")
        upd["business_type"] = body.business_type
        if body.business_type == "law_office" and body.signature_required is None:
            upd["signature_required"] = True  # switching to law office turns the signature rule on (can be turned off after)
    if body.signature_required is not None:
        upd["signature_required"] = body.signature_required
    if not upd:
        raise HTTPException(400, "Nothing to change")
    res = await db.merchants.update_one({"id": merchant_id}, {"$set": upd})
    if not res.matched_count:
        raise HTTPException(404, "Store not found")
    return await db.merchants.find_one({"id": merchant_id}, {"_id": 0})


@router.put("/admin/drivers/{driver_id}/pin", dependencies=ADMIN_ONLY)
async def set_driver_pin(driver_id: str, body: PinIn):
    pin = body.pin.strip()
    if not PIN_RE.match(pin):
        raise HTTPException(400, "PIN must be exactly 6 digits")
    if pin in WEAK_PINS or len(set(pin)) == 1:
        raise HTTPException(400, "PIN is too easy to guess — pick another")
    d = await db.drivers.find_one({"id": driver_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Driver not found")
    await db.users.update_one({"role": "driver", "driver_id": driver_id},
                              {"$set": {"pin_hash": hash_pw(pin), "disabled": False},
                               "$setOnInsert": {"id": str(uuid.uuid4()), "role": "driver", "driver_id": driver_id, "created_at": now_iso()}},
                              upsert=True)
    await db.login_attempts.delete_many({"key": {"$regex": f"^drv:{re.escape(d['phone'])}:"}})
    await db.drivers.update_one({"id": driver_id}, {"$set": {"pin_request_at": None}})  # any "forgot PIN" request is handled
    return {"ok": True}


# ---- driver "Forgot PIN": texts (and emails) dispatch, who resets it with one tap in Admin › Drivers
class ForgotPinIn(BaseModel):
    phone: str


PIN_REQUEST_COOLDOWN_MIN = 10


async def dispatch_phone() -> Optional[str]:
    return (await db.settings.find_one({"key": "dispatch_phone"}, {"_id": 0}) or {}).get("value")


@router.post("/auth/driver-forgot-pin")
async def driver_forgot_pin(body: ForgotPinIn, request: Request):
    """Public. Always answers the same way so it can't be used to find out which numbers are drivers."""
    ip = client_ip(request)
    limits = [(f"pinreq:{ip}", 10)]
    atts = await check_lockout(limits)
    await record_failure(limits, atts)  # every request counts towards the per-device limit
    ok = {"ok": True, "message": "If this number belongs to a DashLocal driver, dispatch has been asked to reset your PIN. You'll get a text with a new one."}
    phone = merchant_routes.normalize_phone(body.phone)
    d = await db.drivers.find_one({"phone": phone}, {"_id": 0}) if phone else None
    if not d:
        return ok
    last = d.get("pin_request_at")
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(minutes=PIN_REQUEST_COOLDOWN_MIN):
        return ok  # dispatch was already told
    await db.drivers.update_one({"id": d["id"]}, {"$set": {"pin_request_at": now_iso()}})
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value") or ""
    from services import realtime  # lazy: avoids an import cycle
    realtime.spawn(tell_dispatch_pin_request(d, base))  # in the background, so the reply time doesn't reveal real driver numbers
    return ok


async def tell_dispatch_pin_request(d: dict, base: str):
    link = f"{base}/admin/drivers?focus={d['id']}"
    to = await dispatch_phone()
    if to:
        try:
            tw = stop_routes.twilio_client()
            await run_in_threadpool(lambda: tw.messages.create(to=to, from_=stop_routes.twilio_number(),
                                    body=f"DashLocal: driver {d['name']} ({d['phone']}) forgot their PIN. Tap to text them a new one: {link}"))
        except Exception as e:
            logger.warning(f"Forgot-PIN text to dispatch failed: {e}")
    await onboarding.notify_admin(f"{d['name']} forgot their driver PIN",
                                  f"<p>{escape(d['name'])} ({escape(d['phone'])}) asked for a new PIN. Open Admin › Drivers and tap <b>NEW PIN BY TEXT</b>.</p>", base or None)


class DispatchPhoneIn(BaseModel):
    phone: Optional[str] = None


@router.get("/admin/dispatch-phone", dependencies=ADMIN_ONLY)
async def get_dispatch_phone():
    return {"phone": await dispatch_phone()}


@router.put("/admin/dispatch-phone", dependencies=ADMIN_ONLY)
async def set_dispatch_phone(body: DispatchPhoneIn):
    """Mobile that gets a text when a driver taps Forgot PIN (empty = email only)."""
    raw = (body.phone or "").strip()
    phone = merchant_routes.normalize_phone(raw) if raw else None
    if raw and not phone:
        raise HTTPException(400, "Enter a valid mobile number")
    await db.settings.update_one({"key": "dispatch_phone"}, {"$set": {"key": "dispatch_phone", "value": phone}}, upsert=True)
    return {"phone": phone}


@router.post("/admin/drivers/{driver_id}/pin/text", dependencies=ADMIN_ONLY)
async def reset_and_text_pin(driver_id: str, request: Request):
    """One tap: pick a fresh random PIN, save it, and text it to the driver with the sign-in link."""
    d = await db.drivers.find_one({"id": driver_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Driver not found")
    last = d.get("pin_texted_at")
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(seconds=60):
        raise HTTPException(429, "A new PIN was just texted — wait a minute before sending another")
    await db.drivers.update_one({"id": driver_id}, {"$set": {"pin_texted_at": now_iso()}})
    base = await onboarding.app_base(request)
    while True:
        pin = f"{secrets.randbelow(10 ** 6):06d}"
        if pin not in WEAK_PINS and len(set(pin)) > 2:
            break
    await set_driver_pin(driver_id, PinIn(pin=pin))
    status = await send_driver_invite(Driver(**d), pin, base, reset=True)
    return {"ok": True, "invite": status, "pin": None if status == "sent" else pin}  # show the PIN only if the text failed


@router.post("/auth/login")
async def login(body: LoginIn, request: Request):
    email = body.email.strip().lower()
    ip = client_ip(request)
    # per email+IP (5 tries) and per email from anywhere (20 tries), so rotating IPs cannot brute-force one account
    limits = [(f"{email}:{ip}", LOGIN_MAX_FAILS), (f"{email}:*", LOGIN_MAX_FAILS * 4)]
    atts = await check_lockout(limits)
    u = await db.users.find_one({"email": email}, {"_id": 0})
    ok = check_pw(body.password, u["password_hash"] if u else DUMMY_HASH)
    if not u or not ok or u.get("disabled") or demo_secret_blocked(request, email, body.password):
        await record_failure(limits, atts)
        raise HTTPException(401, "Wrong email or password")
    await clear_lockout(limits)
    await merchant_routes.remember_public_base(request)
    user = {k: v for k, v in u.items() if k not in ("password_hash", "pw_nonce")}
    if user.get("merchant_id"):
        user["merchant_name"] = (await merchant_routes.get_merchant_or_404(user["merchant_id"]))["name"]
    return {"token": issue_token(u["id"], u["role"]), "user": user}


@router.get("/auth/me")
async def auth_me(u: dict = Depends(current_user)):
    if u.get("driver_id"):
        d = await db.drivers.find_one({"id": u["driver_id"]}, {"_id": 0})
        u["driver_name"] = d["name"] if d else None
    if u.get("merchant_id"):
        m = await db.merchants.find_one({"id": u["merchant_id"]}, {"_id": 0})
        u["merchant_name"] = m["name"] if m else None
    return u


class MerchantAccountIn(BaseModel):
    store_name: str
    email: str
    password: str
    business_type: str = "store"


class PasswordIn(BaseModel):
    password: str


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_password(p: str):
    if len(p) < 8 or len(p.encode()) > 72:
        raise HTTPException(400, "Password must be 8–72 characters")


@router.get("/admin/merchant-accounts")
async def merchant_accounts(_: dict = Depends(admin_user)):
    users = await db.users.find({"role": "merchant"}, {"_id": 0, "password_hash": 0, "pw_nonce": 0}).to_list(500)
    out = []
    for m in await db.merchants.find({}, {"_id": 0}).sort("id", 1).to_list(500):
        out.append({**m, "accounts": [x for x in users if x["merchant_id"] == m["id"]],
                    "orders": await db.stops.count_documents({"merchant_id": m["id"]})})
    return out


@router.post("/admin/merchant-accounts")
async def create_merchant_account(body: MerchantAccountIn, _: dict = Depends(admin_user)):
    name, email = body.store_name.strip(), body.email.strip().lower()
    if not name:
        raise HTTPException(400, "Store name is required")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email")
    check_password(body.password)
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "An account with this email already exists")
    if body.business_type not in onboarding.BUSINESS_TYPES:
        raise HTTPException(400, "Unknown business type")
    m = {"id": f"mer-{uuid.uuid4().hex[:8]}", "name": name, "contact": email, "business_type": body.business_type,
         "signature_required": body.business_type == "law_office"}  # law offices: signature at the door by default
    await db.merchants.insert_one(dict(m))
    user = {"id": str(uuid.uuid4()), "email": email, "role": "merchant", "name": None, "merchant_id": m["id"], "disabled": False, "created_at": now_iso()}
    try:
        await db.users.insert_one({**user, "password_hash": hash_pw(body.password)})
    except DuplicateKeyError:
        await db.merchants.delete_one({"id": m["id"]})
        raise HTTPException(409, "An account with this email already exists")
    return {**m, "accounts": [user], "orders": 0}


@router.put("/admin/merchant-accounts/{user_id}/password")
async def reset_merchant_password(user_id: str, body: PasswordIn, _: dict = Depends(admin_user)):
    check_password(body.password)
    res = await db.users.update_one({"id": user_id, "role": "merchant"}, {"$set": {"password_hash": hash_pw(body.password)}})
    if not res.matched_count:
        raise HTTPException(404, "Merchant account not found")
    return {"ok": True}


# ---- Terms acceptance: store users accept the current Terms of Service (and drivers the independent-contractor terms)
#      on their next sign-in. Admin › Terms shows who accepted and sends reminder emails.
DRIVER_TERMS_VERSION = "2026-10"
REMIND_COOLDOWN_H = 20


def terms_fields(role: str):
    if role == "merchant":
        return "terms_version", "terms_consent_at", "terms_history", onboarding.TERMS_VERSION
    if role == "driver":
        return "driver_terms_version", "driver_terms_at", "driver_terms_history", DRIVER_TERMS_VERSION
    return None


@router.get("/auth/terms-status")
async def terms_status(u: dict = Depends(current_user)):
    f = terms_fields(u.get("role"))
    if not f:
        return {"required": False, "version": onboarding.TERMS_VERSION}
    doc = await db.users.find_one({"id": u["id"]}, {"_id": 0}) or {}
    return {"required": doc.get(f[0]) != f[3], "version": f[3], "kind": u["role"], "accepted_version": doc.get(f[0]), "accepted_at": doc.get(f[1])}


@router.post("/auth/accept-terms")
async def accept_terms(request: Request, u: dict = Depends(current_user)):
    f = terms_fields(u.get("role"))
    if not f:
        raise HTTPException(403, "Only store and driver accounts accept terms here")
    rec = {"version": f[3], "at": now_iso(), "ip": client_ip(request), "user_agent": (request.headers.get("user-agent") or "")[:200]}
    upd = {f[0]: rec["version"], f[1]: rec["at"]}
    if u["role"] == "merchant":
        upd["terms_consent"] = True
    await db.users.update_one({"id": u["id"]}, {"$set": upd, "$push": {f[2]: rec}})
    return {"ok": True, "version": rec["version"], "accepted_at": rec["at"]}


@router.get("/admin/terms-report", dependencies=ADMIN_ONLY)
async def terms_report():
    users = await db.users.find({"role": {"$in": ["merchant", "driver"]}, "disabled": {"$ne": True}}, {"_id": 0, "password_hash": 0}).to_list(5000)
    stores = []
    for m in await db.merchants.find({"disabled": {"$ne": True}}, {"_id": 0, "id": 1, "name": 1, "business_type": 1, "terms_reminded_at": 1}).sort("name", 1).to_list(1000):
        us = [{"email": x.get("email") or "(no email)", "accepted": x.get("terms_version") == onboarding.TERMS_VERSION, "version": x.get("terms_version"),
               "accepted_at": x.get("terms_consent_at"), "last_login_at": x.get("last_login_at")} for x in users if x["role"] == "merchant" and x.get("merchant_id") == m["id"]]
        stores.append({"id": m["id"], "name": m["name"], "business_type": m.get("business_type"), "users": us,
                       "accepted": bool(us) and all(x["accepted"] for x in us), "reminded_at": m.get("terms_reminded_at")})
    drivers = {d["id"]: d for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1, "phone": 1}).to_list(1000)}
    drv = [{"id": x["driver_id"], "name": drivers[x["driver_id"]]["name"], "phone": drivers[x["driver_id"]]["phone"],
            "accepted": x.get("driver_terms_version") == DRIVER_TERMS_VERSION, "accepted_at": x.get("driver_terms_at"), "last_login_at": x.get("last_login_at")}
           for x in users if x["role"] == "driver" and x.get("driver_id") in drivers]
    return {"version": onboarding.TERMS_VERSION, "driver_version": DRIVER_TERMS_VERSION, "stores": stores, "drivers": sorted(drv, key=lambda d: d["name"])}


@router.post("/admin/terms-report/{merchant_id}/remind", dependencies=ADMIN_ONLY)
async def terms_remind(merchant_id: str, request: Request):
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    last = m.get("terms_reminded_at")
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=REMIND_COOLDOWN_H):
        raise HTTPException(429, "A reminder was already sent today")
    pending = await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}, "terms_version": {"$ne": onboarding.TERMS_VERSION}},
                                  {"_id": 0, "email": 1}).to_list(50)
    if not pending:
        raise HTTPException(409, "Everyone at this store already accepted the latest terms")
    from routes import emails  # lazy: keeps auth imports light
    base = await onboarding.app_base(request)
    demo = {em for _, em, _ in DEMO_MERCHANT_USERS}
    html = onboarding.brand_email("Please review our updated Terms of Service",
                                  f"<p>Hi {escape(m['name'])}, we updated the DashLocal Terms of Service ({escape(onboarding.TERMS_VERSION)}), including how payments work. "
                                  "Please sign in to your Merchant Portal and accept them to keep sending deliveries.</p>",
                                  ("Review & accept", f"{base}/merchant"))
    sent = []
    for to in dict.fromkeys(emails.TEST_INBOX if x["email"] in demo else x["email"] for x in pending):
        try:
            await emails.send_email(to=to, subject="Action needed: accept DashLocal's updated Terms of Service", html=html)
            sent.append(to)
        except Exception as e:
            logger.warning(f"Terms reminder to {to}: {e}")
    if not sent:
        raise HTTPException(502, "The reminder email couldn't be sent — try again in a minute")
    at = now_iso()
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"terms_reminded_at": at}})
    return {"ok": True, "sent": len(sent), "reminded_at": at}
