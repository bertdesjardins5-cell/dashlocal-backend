from fastapi import APIRouter, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Depends, Request
import jwt as pyjwt
from fastapi.responses import Response
from fastapi.concurrency import run_in_threadpool
import os
import uuid
import re
import requests
import secrets
from xml.sax.saxutils import escape as xml_escape
from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from core import ADMIN_ONLY, ANY_DRIVER, APP_NAME, DRIVER_SELF, JWT_ALG, JWT_SECRET, ORDER_ACCESS, STOP_DRIVER, TZ, check_order_token, current_user, db, dec_bytes, decode_token, enc_bytes, get_object, issue_token, logger, now_iso, put_object, require_order_access, require_stop_driver, today_str
from models import ContactIn, DeliverIn, FailIn, Stop, apply_sequence, get_driver_or_404, get_stops, pickup_run_labels
from routes import alerts
from routes import auth
from routes import dispatch
from routes import invoices
from routes import onboarding
from routes import scorecards
from services import realtime
from services import seed

router = APIRouter(prefix="/api")
SIG_RE = re.compile(r"^\s*M[\d\s.,MLQCZmlqcz-]+$")  # drawn signature = SVG path data only


@router.get("/stops/{stop_id}")
async def get_stop(stop_id: str, u: dict = Depends(require_stop_driver)):
    """Stop for its driver/admin, plus a doorstep hint: the latest proof photo from an earlier drop to the same customer."""
    stop = await dispatch.get_stop_or_404(stop_id)
    prev = await db.stops.find_one({"id": {"$ne": stop.id}, "customer_phone": stop.customer_phone, "address": stop.address,
                                    "merchant_id": stop.merchant_id, "status": "delivered", "pod_photo_path": {"$ne": None}},
                                   {"_id": 0, "pod_photo_path": 1, "delivered_at": 1}, sort=[("delivered_at", -1)])
    hint = {"photo_url": realtime.file_url(prev["pod_photo_path"]), "delivered_at": prev.get("delivered_at")} if prev else None
    out = {**stop.model_dump(), "doorstep_hint": hint, "pickup_run": (await pickup_run_labels([stop])).get(stop.id),
           "pharmacy_drop": await dispatch.is_pharmacy_drop(stop)}
    return dispatch.driver_stop_view(out) if u.get("role") == "driver" else out


class QrIn(BaseModel):
    code: str


@router.post("/stops/{stop_id}/verify-qr")
async def verify_qr(stop_id: str, body: QrIn, u: dict = Depends(require_stop_driver)):
    """Parcel label QR encodes the tracking code; scanning the right parcel at the door is proof of delivery."""
    stop = await dispatch.get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    await seed.ensure_stop_actionable_today(stop)
    scanned = body.code.strip().upper()
    if scanned.startswith(("DASHLOCAL:", "MULTIDROP:")):  # labels printed before the rename still scan
        scanned = scanned.split(":", 1)[1]
    if scanned != stop.tracking_code:
        raise HTTPException(400, f"Wrong parcel — scanned {scanned[:20]}, expected {stop.tracking_code}")
    await db.stops.update_one({"id": stop_id}, {"$set": {"pod_qr_verified": True}})
    return dispatch.stop_for(u, (await dispatch.get_stop_or_404(stop_id)).model_dump())


HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class WindowIn(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    date: Optional[str] = None  # YYYY-MM-DD depot local, defaults to today




FICTIONAL_NUMBER = re.compile(r"^\+1\d{3}55501\d{2}$")  # 555-0100..0199 are reserved fictional numbers


async def send_booking_sms(stop: "Stop", kind: str) -> str:
    """Text the customer when their delivery day/window is booked, changed or cleared.
    Never blocks the booking: returns sent | skipped_fictional | not_configured | failed."""
    if FICTIONAL_NUMBER.match(stop.customer_phone):
        return "skipped_fictional"
    if not (os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_PHONE_NUMBER")):
        return "not_configured"
    if stop.window_end:
        when = f"{seed.nice_date(stop.window_date or today_str())}, {stop.window_start}-{stop.window_end}"
        verb = "is booked for" if kind == "booked" else "has moved to"
        body = f"DashLocal: your delivery {stop.tracking_code} {verb} {when} (Eastern time). Reply or track your order in the app."
    else:
        body = f"DashLocal: the delivery window for {stop.tracking_code} was cleared. We'll deliver at any time on your route day."
    try:
        tw = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        res = await run_in_threadpool(lambda: tw.messages.create(to=stop.customer_phone, from_=twilio_number(), body=body))
        await db.contact_logs.insert_one({"id": str(uuid.uuid4()), "stop_id": stop.id, "channel": "sms", "kind": f"booking_{kind}",
                                          "message": body, "twilio_sid": res.sid, "status": res.status, "created_at": now_iso()})
        return "sent"
    except Exception as e:
        logger.error(f"Booking SMS failed for {stop.tracking_code}: {e}")
        return "failed"


@router.put("/stops/{stop_id}/window")
async def set_window(stop_id: str, body: WindowIn, who: dict = Depends(require_order_access)):
    """Set (or clear with nulls) the customer's requested delivery window, HH:MM depot local time."""
    stop = await dispatch.get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    day = None
    if body.start or body.end:
        if not (body.start and body.end and HHMM.match(body.start) and HHMM.match(body.end)) or body.start >= body.end:
            raise HTTPException(400, "Window needs start and end in HH:MM with start before end")
        day = seed.check_service_date(body.date)
        if day == today_str() and body.end <= datetime.now(TZ).strftime("%H:%M"):
            raise HTTPException(400, "That window has already passed today")
    moved_off = None
    if stop.route_id and day:
        r = await db.routes.find_one({"id": stop.route_id}, {"_id": 0})
        if r and r.get("service_date", today_str()) != day:
            if stop.status != "pending":
                raise HTTPException(400, "Driver has already arrived — the day can't be changed now")
            moved_off = r["name"]
    upd = {"window_start": body.start or None, "window_end": body.end or None, "window_date": day}
    if moved_off:
        upd.update({"route_id": None, "seq": 0})
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if stop.route_id:
        if moved_off:
            rest = await get_stops(stop.route_id)
            await apply_sequence(stop.route_id, [x.id for x in rest])
            r = await db.routes.find_one({"id": stop.route_id}, {"_id": 0})
            if not rest and r["status"] == "planned":
                await db.routes.delete_one({"id": stop.route_id})
            else:
                await realtime.maybe_complete_route(stop.route_id)
                await realtime.broadcast_route(stop.route_id)
        else:
            await realtime.broadcast_route(stop.route_id)
    if realtime.hub.rooms.get(f"track:{stop.tracking_code}"):
        await realtime.hub.send(f"track:{stop.tracking_code}", await realtime.tracking_payload(stop.tracking_code))
    updated = await dispatch.get_stop_or_404(stop_id)
    kind = "cleared" if not updated.window_end else ("booked" if not stop.window_end else "changed")
    unchanged = (stop.window_start, stop.window_end, stop.window_date) == (updated.window_start, updated.window_end, updated.window_date)
    sms = "unchanged" if unchanged else await send_booking_sms(updated, kind)
    base = realtime.public_stop(updated) if who["role"] == "customer" else updated.model_dump()
    return {**base, "moved_off_route": moved_off, "sms_status": sms}


@router.websocket("/ws/admin")
async def ws_admin(ws: WebSocket, token: str = ""):
    try:
        p = decode_token(token, "session")
        u = await db.users.find_one({"id": p["sub"], "role": "admin", "disabled": {"$ne": True}}, {"_id": 0, "id": 1})
    except HTTPException:
        u = None
    if not u:
        await ws.close(code=4401)
        return
    await realtime.hub.join("admin", ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        realtime.hub.leave("admin", ws)


@router.post("/stops/{stop_id}/arrive")
async def arrive(stop_id: str, u: dict = Depends(require_stop_driver)):
    stop = await dispatch.get_stop_or_404(stop_id)
    if stop.status != "pending":
        raise HTTPException(400, f"Stop is {stop.status}")
    await seed.ensure_stop_actionable_today(stop)
    await db.stops.update_one({"id": stop_id}, {"$set": {"status": "arrived", "arrived_at": now_iso(), "arrival_method": "manual"}})
    if stop.route_id:
        await realtime.broadcast_route(stop.route_id)
    return dispatch.stop_for(u, (await dispatch.get_stop_or_404(stop_id)).model_dump())


@router.post("/stops/{stop_id}/deliver")
async def deliver(stop_id: str, body: DeliverIn, u: dict = Depends(require_stop_driver)):
    stop = await dispatch.get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    await seed.ensure_stop_actionable_today(stop)
    if await dispatch.is_pharmacy_drop(stop):
        if not (body.photo_path and body.signature):
            raise HTTPException(400, "Prescription hand-off needs both a doorstep photo and the recipient's signature")
        if not await db.files.find_one({"storage_path": body.photo_path}, {"_id": 1}) or not SIG_RE.match(body.signature):
            raise HTTPException(400, "Prescription hand-off needs a real uploaded photo and a drawn signature")
    elif not body.photo_path and not body.signature and not stop.pod_qr_verified:
        raise HTTPException(400, "Proof of delivery requires a photo, signature or parcel QR scan")
    if body.signature and (len(body.signature) > 200000 or not SIG_RE.match(body.signature)):
        raise HTTPException(400, "The signature must be drawn on the signature pad")
    if body.photo_path and u["role"] != "admin" and (
            not await db.files.find_one({"storage_path": body.photo_path, "uploaded_by": u["id"]}, {"_id": 1})
            or await db.stops.find_one({"pod_photo_path": body.photo_path, "id": {"$ne": stop_id}}, {"_id": 1})):
        raise HTTPException(400, "Use a photo you just took for this delivery")
    upd = {"status": "delivered", "delivered_at": now_iso(), "pod_photo_path": body.photo_path, "pod_signature": body.signature}
    if not stop.arrived_at:
        upd.update({"arrived_at": now_iso(), "arrival_method": "manual"})
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if stop.merchant_id and not stop.merchant_late_alert_at:
        done = await dispatch.get_stop_or_404(stop_id)
        if scorecards.delivered_on_time(done) is False and (await db.stops.update_one(
                {"id": stop_id, "merchant_late_alert_at": None}, {"$set": {"merchant_late_alert_at": now_iso()}})).modified_count:
            realtime.spawn(alerts.send_merchant_alert(done, "delivered_late"))
    if stop.merchant_id and (await db.merchants.update_one(
            {"id": stop.merchant_id, "approved_via_signup": True, "first_delivery_email_at": None},
            {"$set": {"first_delivery_email_at": now_iso()}})).modified_count:
        realtime.spawn(onboarding.send_first_delivery_email(stop.merchant_id))
    if stop.merchant_id:
        await invoices.create_invoice(stop_id)
    if stop.route_id:
        await realtime.maybe_complete_route(stop.route_id)
        await realtime.broadcast_route(stop.route_id)
    return dispatch.stop_for(u, (await dispatch.get_stop_or_404(stop_id)).model_dump())


@router.post("/stops/{stop_id}/fail")
async def fail(stop_id: str, body: FailIn, u: dict = Depends(require_stop_driver)):
    stop = await dispatch.get_stop_or_404(stop_id)
    if stop.status in ("delivered", "failed"):
        raise HTTPException(400, f"Stop is already {stop.status}")
    await seed.ensure_stop_actionable_today(stop)
    await db.stops.update_one({"id": stop_id}, {"$set": {"status": "failed", "fail_reason": body.reason, "delivered_at": now_iso()}})
    if stop.merchant_id and (await db.stops.update_one(
            {"id": stop_id, "merchant_fail_alert_at": None}, {"$set": {"merchant_fail_alert_at": now_iso()}})).modified_count:
        realtime.spawn(alerts.send_merchant_alert(await dispatch.get_stop_or_404(stop_id), "failed"))
    if stop.route_id:
        await realtime.maybe_complete_route(stop.route_id)
        await realtime.broadcast_route(stop.route_id)
    return dispatch.stop_for(u, (await dispatch.get_stop_or_404(stop_id)).model_dump())


def twilio_client() -> TwilioClient:
    sid, token = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
    if not sid or not token or not os.environ.get("TWILIO_PHONE_NUMBER"):
        raise HTTPException(503, "Twilio is not configured")
    return TwilioClient(sid, token)


E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def twilio_number() -> str:
    raw = os.environ["TWILIO_PHONE_NUMBER"].strip()
    digits = re.sub(r"\D", "", raw)
    return "+" + (digits if raw.startswith("+") or len(digits) > 10 else "1" + digits)


@router.post("/stops/{stop_id}/contact", dependencies=STOP_DRIVER)
async def contact(stop_id: str, body: ContactIn):
    """Masked communication via Twilio: both parties only ever see the Twilio number.
    call -> Twilio rings the driver, then bridges to the customer with the Twilio number as caller ID.
    sms  -> SMS sent to the customer from the Twilio number."""
    if body.channel not in ("call", "sms"):
        raise HTTPException(400, "channel must be call or sms")
    stop = await dispatch.get_stop_or_404(stop_id)
    if not E164.match(stop.customer_phone):
        raise HTTPException(400, "Customer phone must be in +countrycode format")
    tw = twilio_client()
    proxy = twilio_number()
    driver = None
    if stop.route_id:
        r = await db.routes.find_one({"id": stop.route_id}, {"_id": 0})
        if r and r.get("driver_id"):
            driver = await get_driver_or_404(r["driver_id"])
    try:
        if body.channel == "call":
            if not driver or not E164.match(driver.phone):
                raise HTTPException(400, "Driver phone must be set in +countrycode format")
            twiml = (f"<Response><Say>Connecting you to {xml_escape(stop.customer_name)}.</Say>"
                     f"<Dial callerId=\"{proxy}\">{stop.customer_phone}</Dial></Response>")
            res = await run_in_threadpool(lambda: tw.calls.create(to=driver.phone, from_=proxy, twiml=twiml))
        else:
            text = (body.message or "").strip() or f"DashLocal: your driver {driver.name if driver else ''} is on the way with order {stop.tracking_code}."
            res = await run_in_threadpool(lambda: tw.messages.create(to=stop.customer_phone, from_=proxy, body=text[:600]))
    except TwilioRestException as e:
        logger.error(f"Twilio error: {e}")
        raise HTTPException(502, f"Twilio: {e.msg}")
    log = {"id": str(uuid.uuid4()), "stop_id": stop_id, "channel": body.channel, "message": body.message,
           "proxy_number": proxy, "twilio_sid": res.sid, "status": res.status, "created_at": now_iso()}
    await db.contact_logs.insert_one({**log})
    log.pop("_id", None)
    return log


class PhoneIn(BaseModel):
    phone: str


@router.put("/stops/{stop_id}/phone", dependencies=ADMIN_ONLY)  # drivers never see or edit customer numbers
async def set_customer_phone(stop_id: str, body: PhoneIn):
    phone = re.sub(r"[\s\-()]", "", body.phone)
    if not E164.match(phone):
        raise HTTPException(400, "Use +countrycode format, e.g. +15195550123")
    stop = await dispatch.get_stop_or_404(stop_id)
    upd = {"customer_phone": phone}
    if phone != stop.customer_phone and stop.nearby_sms_status in ("skipped_fictional", "not_configured", "failed"):
        upd["nearby_sms_status"] = None  # new number: allow the "2 stops away" text again
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if stop.route_id and "nearby_sms_status" in upd:
        await realtime.broadcast_route(stop.route_id)
    return (await dispatch.get_stop_or_404(stop_id)).model_dump()


@router.put("/drivers/{driver_id}/phone", dependencies=DRIVER_SELF)
async def set_driver_phone(driver_id: str, body: PhoneIn):
    phone = re.sub(r"[\s\-()]", "", body.phone)
    if not E164.match(phone):
        raise HTTPException(400, "Use +countrycode format, e.g. +15195550123")
    await get_driver_or_404(driver_id)
    await db.drivers.update_one({"id": driver_id}, {"$set": {"phone": phone}})
    return (await get_driver_or_404(driver_id)).model_dump()


@router.post("/upload", dependencies=ANY_DRIVER)
async def upload(file: UploadFile = File(...), u: dict = Depends(current_user)):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "File too large (10MB max)")
    ext = (file.filename or "photo.jpg").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "jpg"
    path = f"{APP_NAME}/uploads/pod/{uuid.uuid4()}.{ext}"
    try:
        res = await run_in_threadpool(put_object, path, enc_bytes(data), "application/octet-stream")
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 500
        raise HTTPException(402 if code == 402 else 502, "Storage out of credits" if code == 402 else "Upload failed")
    await db.files.insert_one({"id": str(uuid.uuid4()), "storage_path": res["path"], "original_filename": file.filename,
                               "content_type": file.content_type, "size": res.get("size"), "uploaded_by": u["id"], "created_at": now_iso()})
    return {"path": res["path"]}


async def file_allowed(path: str, request: Request, t: str) -> bool:
    """A signed link for this exact file, or a signed-in session (header or ?t=) that owns it:
    admin, the uploader, the driver of the stop's route, or the stop's store."""
    h = request.headers.get("authorization", "")
    bearer = h[7:].strip() if h.lower().startswith("bearer ") else ""
    for tok in (t, bearer):
        if not tok:
            continue
        try:
            p = pyjwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALG], options={"require": ["exp", "iat", "sub", "role", "purpose"]})
        except pyjwt.InvalidTokenError:
            continue
        if p["purpose"] == "file" and p["sub"] == path:
            return True
        if p["purpose"] == "session":
            u = await db.users.find_one({"id": p["sub"], "role": p["role"], "disabled": {"$ne": True}}, {"_id": 0, "id": 1, "role": 1, "driver_id": 1, "merchant_id": 1})
            if u and await owns_file(u, path):
                return True
    return False


async def owns_file(u: dict, path: str) -> bool:
    if u["role"] == "admin" or await db.files.find_one({"storage_path": path, "uploaded_by": u["id"]}, {"_id": 1}):
        return True
    s = await db.stops.find_one({"pod_photo_path": path}, {"_id": 0, "route_id": 1, "merchant_id": 1})
    if not s:
        return False
    if u["role"] == "merchant":
        return bool(u.get("merchant_id")) and s.get("merchant_id") == u["merchant_id"]
    if u["role"] == "driver" and s.get("route_id"):
        return bool(await db.routes.find_one({"id": s["route_id"], "driver_id": u.get("driver_id")}, {"_id": 1}))
    return False


@router.get("/files/{path:path}")
async def download(path: str, request: Request, t: str = ""):
    if not await file_allowed(path, request, t):
        raise HTTPException(401, "This file needs a valid link or sign-in")
    rec = await db.files.find_one({"storage_path": path}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "File not found")
    if rec.get("is_deleted"):
        raise HTTPException(404, "This photo was erased under the privacy policy")
    data, ct = await run_in_threadpool(get_object, path)
    data = dec_bytes(data)
    return Response(content=data, media_type=rec.get("content_type") or ct, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/track/{code}")
async def track(code: str):
    return await realtime.tracking_payload(code)


class Last4In(BaseModel):
    last4: str


ORDER_TOKEN_MIN = 30 * 24 * 60  # remembered on the phone; also ends 7 days after delivery (check_order_token)


@router.post("/track/{code}/verify")
async def verify_order(code: str, body: Last4In, request: Request):
    """Customer proves the order is theirs with the last 4 digits of the phone on it. 5 tries per code per IP,
    10 per code, 30 per IP (15-min lock) so the 10,000 combinations can't be brute-forced."""
    code, ip, last4 = code.strip().upper(), auth.client_ip(request), body.last4.strip()
    limits = [(f"trk:{code}:{ip}", 5), (f"trk:{code}:*", 10), (f"trkip:{ip}", 30)]
    atts = await auth.check_lockout(limits)
    sdoc = await db.stops.find_one({"tracking_code": code}, {"_id": 0})
    digits = re.sub(r"\D", "", (sdoc or {}).get("customer_phone", ""))
    if not (sdoc and len(digits) >= 4 and re.fullmatch(r"\d{4}", last4) and secrets.compare_digest(digits[-4:], last4)):
        await auth.record_failure(limits, atts)
        raise HTTPException(401, "Those digits don't match the phone on this order")
    await auth.clear_lockout(limits[:2])
    return {"token": issue_token(sdoc["id"], "customer", purpose="order", minutes=ORDER_TOKEN_MIN), **realtime.order_private(Stop(**sdoc))}


@router.get("/track/{code}/private")
async def track_private(code: str, request: Request):
    """Refresh the verified customer's order details (e.g. proof photo after delivery)."""
    sdoc = await db.stops.find_one({"tracking_code": code.strip().upper()}, {"_id": 0})
    if not sdoc:
        raise HTTPException(404, "Tracking code not found")
    await check_order_token(request.headers.get("x-order-token", "").strip(), sdoc["id"])
    return realtime.order_private(Stop(**sdoc))


class CustomerNoteIn(BaseModel):
    note: Optional[str] = None


@router.put("/stops/{stop_id}/customer-note", dependencies=ORDER_ACCESS)
async def set_customer_note(stop_id: str, body: CustomerNoteIn):
    """Customer's delivery note for the driver (empty clears it). Locked once the driver has arrived."""
    stop = await dispatch.get_stop_or_404(stop_id)
    if stop.status != "pending":
        raise HTTPException(400, "The driver is already at your door — the note can't be changed now")
    note = " ".join((body.note or "").split())[:200] or None
    await db.stops.update_one({"id": stop_id}, {"$set": {"customer_note": note, "customer_note_at": now_iso() if note else None,
                                                          "customer_note_seen": not note, "customer_note_read_at": None,
                                                          "customer_note_source": "customer" if note else None}})
    if stop.merchant_id:  # remembered for this customer's next order from the same store
        key = {"merchant_id": stop.merchant_id, "phone": stop.customer_phone}
        if note:
            await db.last_customer_notes.update_one(key, {"$set": {**key, "note": note, "updated_at": now_iso()}}, upsert=True)
        else:
            await db.last_customer_notes.delete_one(key)
    if stop.route_id:
        await realtime.broadcast_route(stop.route_id)
    return {"customer_note": note}


@router.post("/stops/{stop_id}/customer-note/seen", dependencies=STOP_DRIVER)
async def seen_customer_note(stop_id: str):
    stop = await dispatch.get_stop_or_404(stop_id)
    upd = {"customer_note_seen": True}
    if stop.customer_note and not stop.customer_note_read_at:
        upd["customer_note_read_at"] = now_iso()
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if stop.route_id:
        await realtime.broadcast_route(stop.route_id)
    elif realtime.hub.rooms.get(f"track:{stop.tracking_code}"):
        await realtime.hub.send(f"track:{stop.tracking_code}", await realtime.tracking_payload(stop.tracking_code))
    return {"ok": True}


async def remembered_note(merchant_id: str, phone: str) -> tuple:
    """(note, source) for a regular customer: the store's saved note wins over the customer's last note."""
    saved = await db.saved_notes.find_one({"merchant_id": merchant_id, "phone": phone}, {"_id": 0})
    if saved:
        return saved["note"], "saved"
    last = await db.last_customer_notes.find_one({"merchant_id": merchant_id, "phone": phone}, {"_id": 0})
    return (last["note"], "last") if last else (None, None)
