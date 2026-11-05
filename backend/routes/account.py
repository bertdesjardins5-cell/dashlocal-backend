"""Self-service account deletion for stores and drivers (App Store guideline 5.1.1(v)).
Deletion is immediate: the login stops working and personal details are wiped. Delivery and billing records stay
(anonymised) because tax law requires them. Nothing is hard-deleted."""
from fastapi import APIRouter, HTTPException, Request
from html import escape
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import os
from fastapi.concurrency import run_in_threadpool
from twilio.rest import Client as TwilioClient
from core import check_pw, current_user, db, logger, now_iso
from routes import emails, onboarding
from routes import square
from routes import stops as stop_routes
from services import realtime

router = APIRouter(prefix="/api")

OPEN_STOP = {"status": {"$in": ["pending", "arrived"]}}
OWED = {"status": {"$in": ["unpaid", "reported"]}}
MAX_FAILS, LOCK_MIN = 5, 15


class DeleteIn(BaseModel):
    secret: str  # store password or driver PIN, to confirm it's really them


async def confirm_store(name: str, to_list: list, at: str):
    """Confirmation to the store's own inboxes (read before they were wiped)."""
    html = onboarding.brand_email("Your DashLocal account was deleted",
                                  f"<p>As you asked, the DashLocal account for <b>{escape(name)}</b> was deleted on {at[:10]}. "
                                  "Every login for the store has stopped working, and your contact details and saved card were removed.</p>"
                                  "<p>We keep past invoices and delivery records, without personal contact details, because Canadian tax law requires it.</p>"
                                  "<p style='color:#5C5C5E;font-size:12px'>Didn't do this? Reply to this email right away.</p>")
    for to in to_list:
        try:
            await emails.send_email(to=to, subject="Your DashLocal account was deleted", html=html)
        except Exception as e:
            logger.warning(f"Deletion confirmation email to {to}: {e}")


async def confirm_driver(phone: str):
    """Drivers sign in by phone and have no email, so they get a text."""
    if not phone or stop_routes.FICTIONAL_NUMBER.match(phone) or not all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER")):
        return
    body = "DashLocal: Your driver account was deleted as you asked. Your PIN no longer works and your name and phone number were removed."
    try:
        tw = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        await run_in_threadpool(lambda: tw.messages.create(to=phone, from_=stop_routes.twilio_number(), body=body))
    except Exception as e:
        logger.warning(f"Deletion confirmation SMS: {e}")


async def blockers(u: dict) -> list:
    out = []
    if u["role"] == "merchant":
        mid = u["merchant_id"]
        owed = await db.invoices.find({"merchant_id": mid, **OWED}, {"_id": 0, "total": 1}).to_list(5000)
        if owed:
            out.append(f"Pay your {len(owed)} open bill{'s' if len(owed) != 1 else ''} (CA${sum(i['total'] for i in owed):,.2f}) first, in Billing.")
        n = await db.stops.count_documents({"merchant_id": mid, **OPEN_STOP})
        if n:
            out.append(f"{n} deliver{'ies are' if n != 1 else 'y is'} still open. Wait until they're delivered, or ask dispatch to cancel them.")
    elif u["role"] == "driver":
        if await db.routes.find_one({"driver_id": u["driver_id"], "status": {"$in": ["planned", "active"]}}, {"_id": 1}):
            out.append("You still have a route assigned. Finish it, or ask dispatch to reassign it.")
    else:
        out.append("Only store and driver accounts can be deleted here.")
    return out


@router.get("/me/delete-account")
async def delete_check(request: Request):
    u = await current_user(request)  # not terms-gated: anyone signed in may delete their account
    b = await blockers(u)
    return {"can_delete": not b, "blockers": b, "role": u["role"]}


@router.post("/me/delete-account")
async def delete_account(body: DeleteIn, request: Request):
    u = await current_user(request)
    full = await db.users.find_one({"id": u["id"]}, {"_id": 0})
    fails = full.get("delete_fails") or {}
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LOCK_MIN)).isoformat()
    if fails.get("n", 0) >= MAX_FAILS and fails.get("since", "") >= cutoff:
        raise HTTPException(429, f"Too many wrong tries. Wait {LOCK_MIN} minutes and try again.")
    h = full.get("password_hash") if u["role"] == "merchant" else full.get("pin_hash")
    if not h or not check_pw(body.secret.strip(), h):
        # count wrong tries in a 15-minute window (atomic $inc; an older window starts again at 1)
        r = await db.users.update_one({"id": u["id"], "delete_fails.since": {"$gte": cutoff}}, {"$inc": {"delete_fails.n": 1}})
        if not r.matched_count:
            await db.users.update_one({"id": u["id"]}, {"$set": {"delete_fails": {"n": 1, "since": now_iso()}}})
        # 403, not 401: a wrong password here must not sign the user out
        raise HTTPException(403, "That password is wrong" if u["role"] == "merchant" else "That PIN is wrong")
    b = await blockers(u)
    if b:
        raise HTTPException(409, " ".join(b))
    at = now_iso()
    gone = {"disabled": True, "disabled_reason": "deleted_by_user", "deleted_at": at, "name": "Deleted account"}
    unset = {"password_hash": "", "pin_hash": "", "push_tokens": "", "pw_nonce": ""}
    if u["role"] == "merchant":
        mid = u["merchant_id"]
        m = await db.merchants.find_one({"id": mid}, {"_id": 0}) or {}
        cid = (m.get("square") or {}).get("card_id")
        if cid:
            try:
                await square.sq("POST", f"/cards/{cid}/disable")
            except Exception as e:
                logger.warning(f"Square disable card on account deletion: {e}")
        to_list = await square.merchant_recipients(mid)  # read before the emails are wiped
        for x in await db.users.find({"merchant_id": mid, "role": "merchant"}, {"_id": 0, "id": 1}).to_list(50):  # the whole store closes
            await db.users.update_one({"id": x["id"]}, {"$set": {**gone, "email": f"deleted+{x['id']}@deleted.dashlocal.invalid"}, "$unset": unset})
        # keep the business name for past invoices; wipe contact details and the saved card
        await db.merchants.update_one({"id": mid}, {"$set": {"disabled": True, "deleted": True, "deleted_at": at, "contact": "", "address": ""},
                                                   "$unset": {"square": "", "billing_address": "", "problem_alerts": ""}})
        who = f"Store {escape(m.get('name', mid))} ({escape(u.get('email') or '')})"
        realtime.spawn(confirm_store(m.get("name", ""), to_list, at))
    else:
        did = u["driver_id"]
        d = await db.drivers.find_one({"id": did}, {"_id": 0, "name": 1, "phone": 1}) or {}
        await db.users.update_one({"id": u["id"]}, {"$set": gone, "$unset": unset})
        await db.drivers.update_one({"id": did}, {"$set": {"deleted": True, "deleted_at": at, "name": "Deleted driver", "phone": f"deleted-{did}", "vehicle": ""}})
        who = f"Driver {escape(d.get('name', did))}"
        realtime.spawn(confirm_driver(d.get("phone") or ""))
    await db.audit_log.insert_one({"at": at, "user_id": u["id"], "role": u["role"], "action": "account_deleted", "path": "/api/me/delete-account"})
    realtime.spawn(notify_deleted(who, at))
    return {"ok": True}


async def notify_deleted(who: str, at: str):
    try:
        base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
        await onboarding.notify_admin("Account deleted", f"<p>{who} deleted their DashLocal account on {at[:10]}. "
                                      "Billing and delivery records were kept without personal details.</p>", base)
    except Exception as e:
        logger.warning(f"Account deletion admin email: {e}")
