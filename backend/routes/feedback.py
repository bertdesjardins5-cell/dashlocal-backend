from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.concurrency import run_in_threadpool
from pymongo.errors import DuplicateKeyError
import os
import uuid
import secrets
import string
from xml.sax.saxutils import escape as xml_escape
from twilio.rest import Client as TwilioClient
from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime, timezone, timedelta
from core import ADMIN_ONLY, DRIVER_SELF, ORDER_ACCESS, TZ, current_user, db, logger, merchant_access, now_iso, require_order_access
from models import Stop, get_driver_or_404
from routes import dispatch
from routes import linkpages
from routes import stops as stop_routes
from services import realtime

router = APIRouter(prefix="/api")


class RatingIn(BaseModel):
    stars: int
    comment: Optional[str] = None
    tags: List[str] = []


RATING_TAGS = {"ON TIME", "FRIENDLY DRIVER", "HANDLED WITH CARE", "EASY TO FOLLOW", "DISCREET",
               "LATE", "HARD TO FIND PARCEL", "DAMAGED PARCEL", "RUDE DRIVER", "WRONG ADDRESS", "NOT DISCREET"}
POSITIVE_TAGS = {"ON TIME", "FRIENDLY DRIVER", "HANDLED WITH CARE", "EASY TO FOLLOW", "DISCREET"}


async def create_shoutout(stop_id: str, tags: list):
    """5-star rating with a compliment → an in-app thank-you for the driver (no patient details)."""
    st = await db.stops.find_one({"id": stop_id}, {"_id": 0, "route_id": 1})
    r = await db.routes.find_one({"id": (st or {}).get("route_id")}, {"_id": 0, "driver_id": 1}) if st else None
    if not r or not r.get("driver_id"):
        return
    try:
        await db.driver_shoutouts.insert_one({"id": str(uuid.uuid4()), "driver_id": r["driver_id"], "stop_id": stop_id, "tags": tags,
                                              "created_at": now_iso(), "seen_at": None})
    except DuplicateKeyError:
        return


@router.get("/drivers/{driver_id}/shoutouts/new", dependencies=DRIVER_SELF)
async def new_shoutouts(driver_id: str):
    rows = await db.driver_shoutouts.find({"driver_id": driver_id, "seen_at": None}, {"_id": 0, "stop_id": 0}).sort("created_at", -1).to_list(20)
    return {"shoutouts": rows}


@router.post("/drivers/{driver_id}/shoutouts/seen", dependencies=DRIVER_SELF)
async def seen_shoutouts(driver_id: str, body: Optional[dict] = None):
    """Mark the shout-outs the driver actually saw (`{"ids": [...]}`); no ids = all unseen."""
    flt = {"driver_id": driver_id, "seen_at": None}
    if body and isinstance(body.get("ids"), list):
        flt["id"] = {"$in": [str(i) for i in body["ids"][:50]]}
    res = await db.driver_shoutouts.update_many(flt, {"$set": {"seen_at": now_iso()}})
    return {"marked": res.modified_count}


@router.post("/stops/{stop_id}/rating")
async def rate_delivery(stop_id: str, body: RatingIn, who: dict = Depends(require_order_access)):
    """Customer rates a delivered order once (1-5 stars, optional comment)."""
    stop = await dispatch.get_stop_or_404(stop_id)
    if stop.status != "delivered":
        raise HTTPException(400, "Only delivered orders can be rated")
    if stop.rating:
        raise HTTPException(400, "This delivery has already been rated")
    if not 1 <= body.stars <= 5:
        raise HTTPException(400, "Stars must be between 1 and 5")
    comment = (body.comment or "").strip()[:300] or None
    tags = [t for t in dict.fromkeys(x.strip().upper()[:40] for x in body.tags[:20]) if t in RATING_TAGS][:6]
    upd = {"rating": body.stars, "rating_comment": comment, "rating_tags": tags, "rated_at": now_iso()}
    if body.stars <= LOW_RATING:
        upd["follow_up_status"] = "open"
        upd["apology_code"] = "SORRY-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
    await db.stops.update_one({"id": stop_id}, {"$set": upd})
    if body.stars == 5 and any(t in POSITIVE_TAGS for t in tags):
        await create_shoutout(stop_id, [t for t in tags if t in POSITIVE_TAGS])
    rated = await dispatch.get_stop_or_404(stop_id)
    if body.stars <= LOW_RATING:
        status = await send_apology_sms(rated)
        await db.stops.update_one({"id": stop_id}, {"$set": {"apology_sms_status": status}})
        rated = await dispatch.get_stop_or_404(stop_id)
    if body.stars <= LOW_RATING and realtime.hub.rooms.get("admin"):
        await realtime.hub.send("admin", {"type": "low_rating", "alert": await low_rating_row(rated)})
    if who["role"] == "customer":
        return {**realtime.public_stop(rated), "apology_code": rated.apology_code, "apology_sms_status": rated.apology_sms_status}
    return rated.model_dump()


LOW_RATING = 2
PROOF_LINK_DAYS = 30
POSITIVE_REASON_TAGS = POSITIVE_TAGS | {"CAREFUL WITH PARCEL"}  # older chip name; never counted as complaint reasons
APOLOGY_DISCOUNT_PCT = 15


async def send_apology_sms(stop: "Stop") -> str:
    """Text a sorry note + discount code. Never blocks the rating: sent | skipped_fictional | not_configured | failed."""
    if stop_routes.FICTIONAL_NUMBER.match(stop.customer_phone):
        return "skipped_fictional"
    if not (os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_PHONE_NUMBER")):
        return "not_configured"
    first = stop.customer_name.split(" ")[0]
    body = (f"LocalDash: Sorry {first}, delivery {stop.tracking_code} wasn't up to standard. "
            f"Here's {APOLOGY_DISCOUNT_PCT}% off your next delivery with code {stop.apology_code}. Our team will follow up.")
    try:
        tw = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        res = await run_in_threadpool(lambda: tw.messages.create(to=stop.customer_phone, from_=stop_routes.twilio_number(), body=body))
        await db.contact_logs.insert_one({"id": str(uuid.uuid4()), "stop_id": stop.id, "channel": "sms", "kind": "apology",
                                          "message": body, "twilio_sid": res.sid, "status": res.status, "created_at": now_iso()})
        return "sent"
    except Exception as e:
        logger.error(f"Apology SMS failed for {stop.tracking_code}: {e}")
        return "failed"


async def ratings_summary(q: dict) -> dict:
    """Average, star split, per-driver averages and the latest ratings with comments for the given stop filter."""
    docs = await db.stops.find({**q, "rating": {"$ne": None}}, {"_id": 0, "id": 1, "tracking_code": 1, "customer_name": 1, "route_id": 1, "merchant_id": 1,
                                                                 "rating": 1, "rating_tags": 1, "rating_comment": 1, "rated_at": 1}).sort("rated_at", -1).to_list(2000)
    rids = list({d["route_id"] for d in docs if d.get("route_id")})
    route_driver = {r["id"]: r.get("driver_id") for r in await db.routes.find({"id": {"$in": rids}}, {"_id": 0, "id": 1, "driver_id": 1}).to_list(5000)}
    names = {d["id"]: d["name"] for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(500)}
    shops = {m["id"]: m["name"] for m in await db.merchants.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(500)}
    per: Dict[str, dict] = {}
    for d in docs:
        did = route_driver.get(d.get("route_id"))
        d["driver"] = names.get(did) if did else None
        if did:
            p = per.setdefault(did, {"driver_id": did, "name": d["driver"] or did, "count": 0, "total": 0})
            p["count"] += 1
            p["total"] += d["rating"]
    drivers = sorted([{"driver_id": p["driver_id"], "name": p["name"], "count": p["count"], "avg": round(p["total"] / p["count"], 2)} for p in per.values()],
                     key=lambda x: (-x["avg"], -x["count"]))
    recent = [{"stop_id": d["id"], "tracking_code": d["tracking_code"], "customer_name": d["customer_name"], "driver": d["driver"],
               "shop": shops.get(d.get("merchant_id")), "stars": d["rating"], "tags": d.get("rating_tags") or [],
               "comment": d.get("rating_comment"), "rated_at": d.get("rated_at")} for d in docs[:30]]
    return {"count": len(docs), "avg": round(sum(d["rating"] for d in docs) / len(docs), 2) if docs else None,
            "stars": {str(k): sum(1 for d in docs if d["rating"] == k) for k in range(5, 0, -1)}, "drivers": drivers, "recent": recent}


@router.get("/store-screenshots.zip")
async def store_screenshots():
    """Store-listing screenshots (Play 1080x1920 + App Store 1290x2796) made by scripts/store_screenshots.py."""
    path = "/app/store_screenshots/LocalDash_store_screenshots.zip"
    if not os.path.exists(path):
        raise HTTPException(404, "Screenshots not generated yet")
    return FileResponse(path, media_type="application/zip", filename="LocalDash_store_screenshots.zip")


@router.get("/merchants/{merchant_id}/ratings")
async def merchant_ratings(merchant_id: str, _: dict = Depends(merchant_access)):
    return await ratings_summary({"merchant_id": merchant_id})


@router.get("/admin/ratings", dependencies=ADMIN_ONLY)
async def admin_ratings():
    return await ratings_summary({})


@router.get("/alerts/trends", dependencies=ADMIN_ONLY)
async def alert_trends(days: int = 7):
    """Low-rating hot spots over the last N days, by driver and by zone, with the most common reasons."""
    days = max(1, min(days, 31))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    docs = [Stop(**d) for d in await db.stops.find({"rating": {"$ne": None}, "rated_at": {"$gte": since}}, {"_id": 0}).to_list(5000)]
    route_driver = {r["id"]: r.get("driver_id") for r in await db.routes.find({}, {"_id": 0, "id": 1, "driver_id": 1}).to_list(5000)}
    names = {d["id"]: d["name"] for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)}

    def group(key_fn, label_fn):
        g: Dict[str, dict] = {}
        for st in docs:
            k = key_fn(st)
            if not k:
                continue
            row = g.setdefault(k, {"key": k, "label": label_fn(k), "ratings": 0, "low": 0, "reasons": {}})
            row["ratings"] += 1
            if st.rating <= LOW_RATING:
                row["low"] += 1
                for r in st.rating_tags:
                    r = r.strip().upper()
                    if r and r not in POSITIVE_REASON_TAGS:
                        row["reasons"][r] = row["reasons"].get(r, 0) + 1
        out = []
        for row in g.values():
            reasons = sorted(row.pop("reasons").items(), key=lambda kv: -kv[1])
            out.append({**row, "low_pct": round(row["low"] / row["ratings"] * 100) if row["ratings"] else 0,
                        "top_reasons": [{"reason": r, "count": c} for r, c in reasons[:3]]})
        return sorted(out, key=lambda x: (-x["low"], -x["low_pct"]))

    today = datetime.now(TZ).date()
    daily = []
    for back in range(days - 1, -1, -1):
        d = (today - timedelta(days=back)).isoformat()
        daily.append({"date": d, "low": sum(1 for st in docs if st.rating <= LOW_RATING and datetime.fromisoformat(st.rated_at).astimezone(TZ).date().isoformat() == d)})
    return {
        "days": days, "total_ratings": len(docs), "total_low": sum(1 for st in docs if st.rating <= LOW_RATING),
        "by_driver": group(lambda st: route_driver.get(st.route_id), lambda k: names.get(k, k)),
        "by_zone": group(lambda st: st.zone, lambda k: k),
        "daily": daily,
    }


class RedeemIn(BaseModel):
    code: str
    order_value: float
    order_ref: Optional[str] = None


@router.post("/codes/redeem", dependencies=ADMIN_ONLY)
async def redeem_code(body: RedeemIn):
    """Record an apology code being used on a new order (called by checkout, or entered by an admin)."""
    code = body.code.strip().upper()
    stop = await db.stops.find_one({"apology_code": code}, {"_id": 0, "id": 1})
    if not stop:
        raise HTTPException(404, "Unknown code")
    if await db.code_redemptions.find_one({"code": code}):
        raise HTTPException(400, "Code already used")
    if not 0 < body.order_value <= 10000:
        raise HTTPException(400, "Order value must be between 0 and 10,000")
    rec = {"id": str(uuid.uuid4()), "code": code, "stop_id": stop["id"], "order_value": round(body.order_value, 2),
           "discount": round(body.order_value * APOLOGY_DISCOUNT_PCT / 100, 2), "order_ref": (body.order_ref or "").strip()[:40] or None,
           "redeemed_at": now_iso()}
    try:
        await db.code_redemptions.insert_one({**rec})
    except DuplicateKeyError:
        raise HTTPException(400, "Code already used")
    return rec


@router.get("/codes/apology", dependencies=ADMIN_ONLY)
async def apology_codes():
    """Every apology code issued, whether it was used, and what those customers spent afterwards."""
    stops = [Stop(**d) for d in await db.stops.find({"apology_code": {"$ne": None}}, {"_id": 0}).sort("rated_at", -1).to_list(1000)]
    red = {r["code"]: r for r in await db.code_redemptions.find({}, {"_id": 0}).to_list(5000)}
    rows = [{"code": st.apology_code, "stop_id": st.id, "tracking_code": st.tracking_code, "customer_name": st.customer_name,
             "rating": st.rating, "issued_at": st.rated_at, "sms_status": st.apology_sms_status, "redemption": red.get(st.apology_code)} for st in stops]
    used = [r["redemption"] for r in rows if r["redemption"]]
    revenue = round(sum(r["order_value"] for r in used), 2)
    discount = round(sum(r["discount"] for r in used), 2)
    return {
        "discount_pct": APOLOGY_DISCOUNT_PCT, "issued": len(rows), "redeemed": len(used),
        "redemption_rate": round(len(used) / len(rows) * 100) if rows else 0,
        "revenue": revenue, "discount_given": discount, "net_revenue": round(revenue - discount, 2),
        "avg_order": round(revenue / len(used), 2) if used else 0, "codes": rows,
    }


class CoachingIn(BaseModel):
    note: str
    reason: Optional[str] = None


@router.post("/drivers/{driver_id}/coaching", dependencies=ADMIN_ONLY)
async def send_coaching(driver_id: str, body: CoachingIn):
    await get_driver_or_404(driver_id)
    note = body.note.strip()
    if not note:
        raise HTTPException(400, "Write a note first")
    rec = {"id": str(uuid.uuid4()), "driver_id": driver_id, "note": note[:600], "reason": (body.reason or "").strip()[:80] or None,
           "created_at": now_iso(), "read_at": None}
    await db.coaching_notes.insert_one({**rec})
    return rec


@router.get("/drivers/{driver_id}/feedback", dependencies=DRIVER_SELF)
async def driver_feedback(driver_id: str):
    """The driver's own patient ratings: average, star split, top compliments and recent ratings (no patient details)."""
    if not await db.drivers.find_one({"id": driver_id}, {"_id": 1}):
        raise HTTPException(404, "Driver not found")
    rids = [r["id"] for r in await db.routes.find({"driver_id": driver_id}, {"_id": 0, "id": 1}).to_list(5000)]
    rows = await db.stops.find({"route_id": {"$in": rids}, "rating": {"$ne": None}}, {"_id": 0, "rating": 1, "rating_tags": 1, "rating_comment": 1, "rated_at": 1}).sort("rated_at", -1).to_list(2000)
    positive = {"ON TIME", "FRIENDLY DRIVER", "HANDLED WITH CARE", "EASY TO FOLLOW", "DISCREET"}
    praise: Dict[str, int] = {}
    issues: Dict[str, int] = {}
    for r in rows:
        for t in r.get("rating_tags") or []:
            bucket = praise if t in positive else issues
            bucket[t] = bucket.get(t, 0) + 1
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent30 = [r["rating"] for r in rows if (r.get("rated_at") or "") >= since]
    return {"count": len(rows), "avg": round(sum(r["rating"] for r in rows) / len(rows), 2) if rows else None,
            "avg_30d": round(sum(recent30) / len(recent30), 2) if recent30 else None, "count_30d": len(recent30),
            "stars": {str(k): sum(1 for r in rows if r["rating"] == k) for k in range(5, 0, -1)},
            "compliments": sorted(praise.items(), key=lambda x: -x[1])[:5], "issues": sorted(issues.items(), key=lambda x: -x[1])[:3],
            "recent": [{"stars": r["rating"], "tags": r.get("rating_tags") or [], "comment": r.get("rating_comment"), "rated_at": r.get("rated_at")} for r in rows[:8]]}


@router.get("/drivers/{driver_id}/coaching", dependencies=DRIVER_SELF)
async def list_coaching(driver_id: str, unread: bool = False):
    await get_driver_or_404(driver_id)
    q = {"driver_id": driver_id, **({"read_at": None} if unread else {})}
    return await db.coaching_notes.find(q, {"_id": 0}).sort("created_at", -1).to_list(100)


@router.post("/coaching/{note_id}/read")
async def read_coaching(note_id: str, u: dict = Depends(current_user)):
    note = await db.coaching_notes.find_one({"id": note_id}, {"_id": 0, "driver_id": 1})
    if note and u["role"] != "admin" and note.get("driver_id") != u.get("driver_id"):
        raise HTTPException(403, "This note is for another driver")
    res = await db.coaching_notes.update_one({"id": note_id, "read_at": None}, {"$set": {"read_at": now_iso()}})
    if not res.matched_count and not await db.coaching_notes.find_one({"id": note_id}):
        raise HTTPException(404, "Note not found")
    return {"ok": True}


# ---- public proof-of-delivery share link
@router.post("/stops/{stop_id}/share", dependencies=ORDER_ACCESS)
async def share_proof(stop_id: str):
    stop = await dispatch.get_stop_or_404(stop_id)
    if stop.status != "delivered":
        raise HTTPException(400, "Proof can be shared after delivery")
    created = datetime.fromisoformat(stop.share_created_at) if stop.share_created_at else None
    expired = not created or datetime.now(timezone.utc) - created >= timedelta(days=PROOF_LINK_DAYS)
    token = stop.share_token
    if not token or expired:  # new link (old one stops working) when missing or expired
        token, created = secrets.token_urlsafe(16), datetime.now(timezone.utc)
        await db.stops.update_one({"id": stop_id}, {"$set": {"share_token": token, "share_created_at": created.isoformat()}})
    return {"token": token, "path": f"/api/proof/{token}", "expires_at": (created + timedelta(days=PROOF_LINK_DAYS)).isoformat(), "valid_days": PROOF_LINK_DAYS}


@router.get("/proof/{token}", response_class=HTMLResponse)
async def proof_page(token: str):
    doc = await db.stops.find_one({"share_token": token}, {"_id": 0})
    if not doc:
        raise linkpages.LinkPageError("proof", "invalid", 404)
    st = Stop(**doc)
    if not st.share_created_at or datetime.now(timezone.utc) - datetime.fromisoformat(st.share_created_at) >= timedelta(days=PROOF_LINK_DAYS):
        raise linkpages.LinkPageError("proof", "expired", 410)
    esc = lambda v: xml_escape(str(v), {'"': "&quot;", "'": "&#x27;"})  # noqa: E731 — attribute-safe
    when = datetime.fromisoformat(st.delivered_at).astimezone(TZ).strftime("%a %d %b %Y, %H:%M") if st.delivered_at else ""
    photo = f'<img src="{esc(realtime.file_url(st.pod_photo_path))}" alt="Delivery photo">' if st.pod_photo_path else ""
    sig = (f'<svg viewBox="0 0 360 200" preserveAspectRatio="xMidYMid meet"><path d="{esc(st.pod_signature)}" stroke="#0A0A0A" stroke-width="4" '
           f'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>') if st.pod_signature else ""
    qr = '<div class="ok">&#10003; PARCEL QR SCANNED AT THE DOOR</div>' if st.pod_qr_verified else ""
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proof of delivery {esc(st.tracking_code)}</title><meta name="robots" content="noindex">
<style>body{{font-family:'Courier New',monospace;margin:0;color:#0A0A0A;background:#fff}}
header{{background:#0A0A0A;color:#fff;padding:20px}}h1{{font-family:Arial,sans-serif;margin:4px 0 0;font-size:28px}}
.k{{color:#E60000;font-weight:bold;letter-spacing:2px;font-size:12px}}main{{padding:20px;max-width:560px}}
.box{{border:2px solid #0A0A0A;margin:12px 0;padding:12px}}img{{width:100%;display:block}}svg{{width:100%;height:180px}}
.ok{{background:#008000;color:#fff;padding:12px;font-weight:bold}}.m{{font-size:13px;margin:4px 0}}</style></head><body>
<header><div class="k">LOCALDASH · PROOF OF DELIVERY</div><h1>{esc(st.tracking_code)}</h1></header><main>
<p class="m"><b>{esc(st.customer_name)}</b></p><p class="m">{esc(st.address)}</p><p class="m">DELIVERED {esc(when)} (EASTERN TIME)</p>
{f'<div class="box">{photo}</div>' if photo else ''}{f'<div class="box"><div class="m">SIGNATURE</div>{sig}</div>' if sig else ''}{qr}
</main></body></html>"""
    return HTMLResponse(html)


async def low_rating_row(st: "Stop") -> dict:
    driver, route_name = None, None
    if st.route_id:
        r = await db.routes.find_one({"id": st.route_id}, {"_id": 0})
        if r:
            route_name = r["name"]
            if r.get("driver_id"):
                d = await db.drivers.find_one({"id": r["driver_id"]}, {"_id": 0, "id": 1, "name": 1})
                driver = d
    return {"stop": st.model_dump(), "driver": driver, "route_name": route_name}


@router.get("/alerts/low-ratings", dependencies=ADMIN_ONLY)
async def low_ratings(status: str = "open"):
    """1-2 star deliveries for admin follow-up. status: open | resolved | all."""
    q = {"rating": {"$lte": LOW_RATING, "$gte": 1}}
    if status not in ("open", "resolved", "all"):
        raise HTTPException(400, "status must be open, resolved or all")
    if status in ("open", "resolved"):
        q["follow_up_status"] = status
    docs = await db.stops.find(q, {"_id": 0}).sort("rated_at", -1).to_list(200)
    return [await low_rating_row(Stop(**d)) for d in docs]


class FollowUpIn(BaseModel):
    note: Optional[str] = None
    resolved: bool = True


@router.post("/stops/{stop_id}/follow-up", dependencies=ADMIN_ONLY)
async def follow_up(stop_id: str, body: FollowUpIn):
    stop = await dispatch.get_stop_or_404(stop_id)
    if not stop.rating or stop.rating > LOW_RATING:
        raise HTTPException(400, "Only 1-2 star deliveries need follow-up")
    note = (body.note or "").strip()[:500] or stop.follow_up_note
    await db.stops.update_one({"id": stop_id}, {"$set": {"follow_up_status": "resolved" if body.resolved else "open",
                                                         "follow_up_note": note, "follow_up_at": now_iso()}})
    return await low_rating_row(await dispatch.get_stop_or_404(stop_id))
