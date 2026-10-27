from fastapi import APIRouter, HTTPException, Depends, Request
from pymongo.errors import DuplicateKeyError
import os
from html import escape
import asyncio
import uuid
import secrets
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone, timedelta
from core import ADMIN_ONLY, CONSENT_VERSION, db, decode_token, hash_pw, issue_token, logger, merchant_access, now_iso
from routes import auth
from routes import emails
from routes import merchants as merchant_routes

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- review requests + merchant sign-up requests
REVIEW_LINK_DAYS = 14
SETPW_LINK_HOURS = 72


def demo_inbox(email: str) -> str:
    """Seeded demo logins are not real inboxes; route their mail to the provider's test inbox."""
    return emails.TEST_INBOX if email in {em for _, em, _ in auth.DEMO_MERCHANT_USERS} else email


async def app_base(request: Request) -> str:
    await merchant_routes.remember_public_base(request)
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    if not base:
        raise HTTPException(400, "App address unknown yet — open the app from its public link and try again")
    return base


def brand_email(title: str, body_html: str, cta: Optional[tuple] = None) -> str:
    btn = (f'<p style="margin:24px 0"><a href="{escape(cta[1])}" style="background:#2B8932;color:#fff;padding:14px 22px;'
           f'border-radius:10px;text-decoration:none;font-family:Arial,sans-serif;font-weight:bold">{escape(cta[0])}</a></p>') if cta else ""
    return (f'<!doctype html><html><body style="margin:0;background:#F5F5F5;padding:24px">'
            f'<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:16px;padding:28px;color:#333335">'
            f'<div style="font-family:Arial,sans-serif;font-weight:bold;font-size:20px"><span style="color:#333335">DASH</span><span style="color:#D7262E">LOCAL</span></div>'
            f'<h1 style="font-family:Arial,sans-serif;font-size:22px;margin:20px 0 8px">{escape(title)}</h1>'
            f'<div style="font-family:Georgia,serif;font-size:16px;line-height:1.6">{body_html}</div>{btn}'
            f'<p style="font-family:Georgia,serif;font-size:13px;color:#5C5C5E">Same-day local delivery for small businesses.</p></div></body></html>')


async def notify_admin(subject: str, body_html: str, base: Optional[str]):
    """Best effort: email the dispatch admin; never blocks the request."""
    to = os.environ.get("ADMIN_NOTIFY_EMAIL") or os.environ.get("ADMIN_EMAIL")
    if not to:
        return
    try:
        await emails.send_email(to=to, subject=subject, html=brand_email(subject, body_html, ("Open the admin panel", f"{base}/admin") if base else None))
    except Exception as e:
        logger.warning(f"Admin notify failed: {e}")


class ReviewRequestIn(BaseModel):
    merchant_id: str


@router.post("/admin/review-requests", dependencies=ADMIN_ONLY)
async def send_review_request(body: ReviewRequestIn, request: Request):
    """Email a store a private link to write its own quote; it lands hidden until the admin publishes it."""
    m = await merchant_routes.get_merchant_or_404(body.merchant_id)
    users = await db.users.find({"merchant_id": m["id"], "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0}).to_list(20)
    if not users:
        raise HTTPException(400, "This store has no login email yet")
    base = await app_base(request)
    rid = str(uuid.uuid4())
    link = f"{base}/review/{issue_token(rid, 'merchant', purpose='review', minutes=REVIEW_LINK_DAYS * 24 * 60)}"
    html = brand_email(f"How is DashLocal working for {m['name']}?",
                       f"<p>We'd love a short quote about delivering with DashLocal. With your OK, it may appear on our website for other small businesses to read.</p>"
                       f"<p>The link works once and lasts {REVIEW_LINK_DAYS} days.</p>", ("Write a quick review", link))
    sent, errors = [], []
    for u in users:
        try:
            await emails.send_email(to=demo_inbox(u["email"]), subject=f"A quick review of DashLocal? — {m['name']}", html=html)
            sent.append(u["email"])
        except Exception as e:
            errors.append(str(e))
    if not sent:
        raise HTTPException(424, f"Email could not be sent: {errors[0] if errors else 'unknown error'}")
    doc = {"id": rid, "merchant_id": m["id"], "shop": m["name"], "emails": sent, "status": "sent", "created_at": now_iso(), "submitted_at": None, "reminded_at": None}
    await db.review_requests.insert_one(dict(doc))
    return doc


@router.get("/admin/review-requests", dependencies=ADMIN_ONLY)
async def list_review_requests():
    return await db.review_requests.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)


def link_payload(token: str, purpose: str) -> dict:
    """Decode an emailed link token with a message that makes sense on a public page."""
    try:
        return decode_token(token, purpose)
    except HTTPException as e:
        raise HTTPException(400, "This link has expired — ask DashLocal for a new one" if "expired" in str(e.detail) else "This link isn't valid")


async def open_review_request(token: str) -> dict:
    rid = link_payload(token, "review")["sub"]
    rr = await db.review_requests.find_one({"id": rid}, {"_id": 0})
    if not rr:
        raise HTTPException(404, "This review link is no longer valid")
    return rr


@router.get("/review-requests/{token}")
async def get_review_request(token: str):
    rr = await open_review_request(token)
    return {"shop": rr["shop"], "status": rr["status"]}


class ReviewSubmitIn(BaseModel):
    quote: str
    person: Optional[str] = None


@router.post("/review-requests/{token}")
async def submit_review(token: str, body: ReviewSubmitIn, request: Request):
    rr = await open_review_request(token)
    if rr["status"] != "sent":
        raise HTTPException(400, "Thanks — a review was already sent with this link")
    t = auth.clean_testimonial(auth.TestimonialIn(quote=body.quote, person=body.person, shop=rr["shop"], published=False))
    res = await db.review_requests.update_one({"id": rr["id"], "status": "sent"}, {"$set": {"status": "submitted", "submitted_at": now_iso()}})
    if not res.modified_count:
        raise HTTPException(400, "Thanks — a review was already sent with this link")
    doc = {"id": str(uuid.uuid4()), **t, "from_shop": True, "merchant_id": rr["merchant_id"], "created_at": now_iso(), "updated_at": now_iso()}
    await db.testimonials.insert_one(dict(doc))
    await notify_admin(f"New review from {rr['shop']} to approve", f"<p>“{escape(t['quote'])}”</p><p>Publish it from Shop reviews in the admin panel.</p>",
                       (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value"))
    return {"ok": True}


REVIEW_REMINDER_DAYS = 7


async def send_review_reminders() -> int:
    """One gentle reminder per review link still unused after 7 days (the link keeps its original expiry)."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=REVIEW_REMINDER_DAYS)).isoformat()
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    if not base:
        return 0
    sent = 0
    for rr in await db.review_requests.find({"status": "sent", "created_at": {"$lte": cutoff}, "reminded_at": None}, {"_id": 0}).to_list(100):
        left = timedelta(days=REVIEW_LINK_DAYS) - (now - datetime.fromisoformat(rr["created_at"]))
        if left <= timedelta(hours=12):
            continue  # too close to expiry to be useful
        # claim first so two workers never double-send
        if not (await db.review_requests.update_one({"id": rr["id"], "reminded_at": None}, {"$set": {"reminded_at": now_iso()}})).modified_count:
            continue
        link = f"{base}/review/{issue_token(rr['id'], 'merchant', purpose='review', minutes=int(left.total_seconds() // 60))}"
        days_left = max(1, round(left.total_seconds() / 86400))
        html = brand_email(f"Still happy to share a few words, {rr['shop']}?",
                           "<p>Just a friendly nudge: a sentence or two about delivering with DashLocal helps other small businesses find us. "
                           f"No pressure at all — the link stays open for {days_left} more day{'s' if days_left != 1 else ''}.</p>",
                           ("Write a quick review", link))
        ok = False
        for em in rr.get("emails", []):
            try:
                await emails.send_email(to=demo_inbox(em), subject=f"A quick review of DashLocal? — {rr['shop']}", html=html)
                ok = True
            except Exception as e:
                logger.warning(f"Review reminder failed for {rr['id']}: {e}")
        attempts = rr.get("reminder_attempts", 0) + 1
        # failed sends (e.g. provider rate limit) are retried on the next hourly run, up to 3 tries
        upd = {"reminder_status": "sent" if ok else "failed", "reminder_attempts": attempts}
        if not ok and attempts < 3:
            upd["reminded_at"] = None
        await db.review_requests.update_one({"id": rr["id"]}, {"$set": upd})
        sent += ok
    return sent


async def review_reminder_watcher():
    await asyncio.sleep(30)
    while True:
        try:
            await send_review_reminders()
        except Exception as e:
            logger.warning(f"Review reminder loop error: {e}")
        await asyncio.sleep(3600)


@router.post("/admin/review-requests/run-reminders", dependencies=ADMIN_ONLY)
async def run_review_reminders():
    """Manual trigger of the hourly reminder check (testing/ops)."""
    return {"sent": await send_review_reminders()}


GROWTH_TIPS = [
    ("Upload orders every day", "Shops that send a CSV each morning get the smoothest routes. The template in your portal has every column."),
    ("Save notes for your regulars", "Gate codes and drop-off tips you save are filled in on every new order for that customer."),
    ("Share your tracking links", "Customers who can follow their order live call less and come back more."),
    ("Use priority for rush orders", "Mark urgent orders as priority so they go out first (a small surcharge applies)."),
    ("Watch your weekly email", "Your Monday report and statement show deliveries, on-time rate and what you owe."),
]


async def send_first_delivery_email(merchant_id: str):
    """One-time follow-up after a newly approved shop's first delivery, with tips to grow."""
    m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0})
    if not m:
        return
    users = await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0, "email": 1}).to_list(20)
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    tips = "".join(f"<li style=\"margin-bottom:10px\"><b>{escape(t)}</b><br>{escape(b)}</li>" for t, b in GROWTH_TIPS)
    html = brand_email(f"Your first DashLocal delivery is done 🎉",
                       f"<p>Congratulations, {escape(m['name'])}: your first order just reached its customer. A few tips to get the most from DashLocal:</p>"
                       f"<ol style=\"padding-left:20px\">{tips}</ol><p>Questions? Just reply to this email.</p>",
                       ("Open your portal", f"{base}/sign-in/merchant") if base else None)
    ok = False
    for u in users:
        try:
            await emails.send_email(to=demo_inbox(u["email"]), subject=f"First delivery done — tips to grow, {m['name']}", html=html)
            ok = True
        except Exception as e:
            logger.warning(f"First-delivery email failed for {merchant_id}: {e}")
    # failed sends are retried on the shop's next delivery (first_delivery_email_at reopened)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"first_delivery_email_status": "sent" if ok else "failed",
                                                                  **({} if ok else {"first_delivery_email_at": None})}})


@router.get("/merchants/{merchant_id}/checklist")
async def merchant_checklist(merchant_id: str, _: dict = Depends(merchant_access)):
    """First-steps checklist for newly approved shops; each step ticks itself from real activity."""
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    steps = [
        {"key": "password", "label": "Choose your password", "done": True},
        {"key": "upload", "label": "Upload your first orders (CSV)", "done": bool(await db.import_jobs.find_one({"merchant_id": merchant_id}, {"_id": 1}))},
        {"key": "note", "label": "Save a delivery note for a regular customer", "done": bool(await db.saved_notes.find_one({"merchant_id": merchant_id}, {"_id": 1}))},
        {"key": "delivered", "label": "Get your first delivery completed", "done": bool(await db.stops.find_one({"merchant_id": merchant_id, "status": "delivered"}, {"_id": 1}))},
    ]
    return {"show": bool(m.get("onboarding")), "steps": steps, "done": sum(s["done"] for s in steps), "total": len(steps)}


@router.post("/merchants/{merchant_id}/checklist/dismiss")
async def dismiss_checklist(merchant_id: str, _: dict = Depends(merchant_access)):
    await merchant_routes.get_merchant_or_404(merchant_id)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"onboarding": False}})
    return {"ok": True}


class SignupIn(BaseModel):
    store_name: str
    contact_name: str
    email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    message: Optional[str] = None
    business_type: str = "store"  # store | law_office | flower_shop | pharmacy | other
    privacy_consent: bool = False  # pharmacies: PIPEDA / PHIPA privacy terms
    terms_consent: bool = False  # everyone else: Terms of Service
    consent_timestamp: Optional[str] = None  # when the person tapped Accept in the consent modal (device clock)


BUSINESS_TYPES = {"store": "Store", "law_office": "Law office", "flower_shop": "Flower shop", "pharmacy": "Pharmacy", "other": "Other"}
TERMS_VERSION = "2026-06"


@router.post("/signup-requests")
async def request_signup(body: SignupIn, request: Request):
    """Public: a shop asks for a DashLocal account. The admin approves it from the admin panel."""
    clip = lambda v, n: " ".join((v or "").split())[:n]
    doc = {"store_name": clip(body.store_name, 80), "contact_name": clip(body.contact_name, 60), "email": body.email.strip().lower()[:120],
           "phone": clip(body.phone, 30) or None, "address": clip(body.address, 160) or None, "message": clip(body.message, 500) or None}
    if not doc["store_name"] or not doc["contact_name"]:
        raise HTTPException(400, "Store name and your name are required")
    if not auth.EMAIL_RE.match(doc["email"]):
        raise HTTPException(400, "Enter a valid email")
    if body.business_type not in BUSINESS_TYPES:
        raise HTTPException(400, "Choose a type of business")
    doc["business_type"] = body.business_type
    ts = (body.consent_timestamp or "")[:40] or None
    if body.business_type == "pharmacy":
        if not body.privacy_consent:
            raise HTTPException(400, "Pharmacies must accept the PIPEDA / PHIPA privacy terms to continue")
        doc.update(privacy_consent=True, privacy_consent_at=now_iso(), phipa_consent_given=True,
                   consent_timestamp=ts, consent_version=CONSENT_VERSION)
    else:
        if not body.terms_consent:
            raise HTTPException(400, "Please accept the Terms of Service to continue")
        doc.update(terms_consent=True, terms_consent_at=now_iso(), terms_version=TERMS_VERSION, consent_timestamp=ts)
    ip = auth.client_ip(request)
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    if await db.signup_requests.count_documents({"ip": ip, "created_at": {"$gte": hour_ago}}) >= 5:
        raise HTTPException(429, "Too many requests — try again in an hour")
    if await db.signup_requests.find_one({"email": doc["email"], "status": "pending"}) or await db.users.find_one({"email": doc["email"]}):
        return {"ok": True}  # same answer either way: don't reveal which emails already have accounts
    await db.signup_requests.insert_one({**doc, "id": str(uuid.uuid4()), "status": "pending", "ip": ip, "created_at": now_iso()})
    base = None
    try:
        base = await app_base(request)
    except HTTPException:
        pass
    asyncio.create_task(notify_admin(f"New shop sign-up request: {doc['store_name']}",
                       f"<p><b>{escape(doc['store_name'])}</b> — {escape(doc['contact_name'])}</p><p>{escape(doc['message'] or '')}</p>"
                       "<p>Approve or decline it from Sign-up requests in the admin panel.</p>", base))
    return {"ok": True}


@router.get("/admin/signup-requests", dependencies=ADMIN_ONLY)
async def list_signups():
    return await db.signup_requests.find({}, {"_id": 0, "ip": 0}).sort("created_at", -1).to_list(200)


@router.post("/admin/signup-requests/{sid}/approve", dependencies=ADMIN_ONLY)
async def approve_signup(sid: str, request: Request):
    """Create the store + its login (no password yet) and email a one-time set-password link."""
    sr = await db.signup_requests.find_one({"id": sid}, {"_id": 0})
    if not sr:
        raise HTTPException(404, "Request not found")
    if sr["status"] != "pending":
        raise HTTPException(400, f"Request already {sr['status']}")
    if await db.users.find_one({"email": sr["email"]}):
        raise HTTPException(409, "An account with this email already exists")
    base = await app_base(request)
    m = {"id": f"mer-{uuid.uuid4().hex[:8]}", "name": sr["store_name"], "contact": sr["email"], "onboarding": True,
         "approved_via_signup": True, "first_delivery_email_at": None, "address": sr.get("address"),
         "business_type": sr.get("business_type", "store")}  # pharmacy partner pricing is still switched on by the admin
    nonce = secrets.token_urlsafe(12)
    user = {"id": str(uuid.uuid4()), "email": sr["email"], "role": "merchant", "name": sr["contact_name"], "merchant_id": m["id"],
            "disabled": False, "created_at": now_iso(), "pw_nonce": nonce, "password_hash": hash_pw(secrets.token_urlsafe(24))}
    if sr.get("phipa_consent_given"):  # consent given in the sign-up modal carries over to the new login
        user.update(phipa_consent_given=True, consent_timestamp=sr.get("consent_timestamp"),
                    consent_recorded_at=sr.get("privacy_consent_at"), consent_version=sr.get("consent_version"))
    if sr.get("terms_consent"):  # Terms of Service accepted at sign-up carries over too
        user.update(terms_consent=True, terms_consent_at=sr.get("terms_consent_at"), terms_version=sr.get("terms_version"),
                    consent_timestamp=sr.get("consent_timestamp"))
    await db.merchants.insert_one(dict(m))
    try:
        await db.users.insert_one(user)
    except DuplicateKeyError:
        await db.merchants.delete_one({"id": m["id"]})
        raise HTTPException(409, "An account with this email already exists")
    link = f"{base}/set-password/{issue_token(user['id'], 'merchant', purpose='setpw', minutes=SETPW_LINK_HOURS * 60, n=nonce)}"
    status = "sent"
    try:
        await emails.send_email(to=demo_inbox(sr["email"]), subject="Your DashLocal store is ready",
                         html=brand_email(f"Welcome to DashLocal, {sr['contact_name'].split(' ')[0]}",
                                          f"<p>Your store <b>{escape(sr['store_name'])}</b> is approved. Choose a password to sign in and upload your first orders.</p>"
                                          f"<p>The link works once and lasts {SETPW_LINK_HOURS} hours.</p>", ("Choose your password", link)))
    except Exception as e:
        status = f"failed: {e}"
    await db.signup_requests.update_one({"id": sid}, {"$set": {"status": "approved", "merchant_id": m["id"], "decided_at": now_iso(), "email_status": status}})
    return {"ok": True, "merchant_id": m["id"], "email_status": status}


@router.post("/admin/signup-requests/{sid}/decline", dependencies=ADMIN_ONLY)
async def decline_signup(sid: str):
    res = await db.signup_requests.update_one({"id": sid, "status": "pending"}, {"$set": {"status": "declined", "decided_at": now_iso()}})
    if not res.matched_count:
        raise HTTPException(404, "No pending request with this id")
    return {"ok": True}


async def setpw_user(token: str) -> dict:
    p = link_payload(token, "setpw")
    u = await db.users.find_one({"id": p["sub"], "disabled": {"$ne": True}}, {"_id": 0})
    if not u or not u.get("pw_nonce") or not secrets.compare_digest(u["pw_nonce"], str(p.get("n", ""))):
        raise HTTPException(400, "This link was already used or has been replaced — ask DashLocal for a new one")
    return u


@router.get("/auth/set-password/{token}")
async def setpw_info(token: str):
    u = await setpw_user(token)
    m = await db.merchants.find_one({"id": u.get("merchant_id")}, {"_id": 0}) or {}
    return {"email": u["email"], "store_name": m.get("name")}


class SetPasswordIn(BaseModel):
    token: str
    password: str


@router.post("/auth/set-password")
async def setpw(body: SetPasswordIn, request: Request):
    u = await setpw_user(body.token)
    auth.check_password(body.password)
    await db.users.update_one({"id": u["id"]}, {"$set": {"password_hash": hash_pw(body.password), "last_login_at": now_iso()}, "$unset": {"pw_nonce": ""}})
    await merchant_routes.remember_public_base(request)
    user = {k: v for k, v in u.items() if k not in ("password_hash", "pw_nonce")}
    user["merchant_name"] = (await db.merchants.find_one({"id": u.get("merchant_id")}, {"_id": 0}) or {}).get("name")
    return {"token": issue_token(u["id"], u["role"]), "user": user}
