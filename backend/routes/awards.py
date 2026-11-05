"""Monthly 'Driver of the Month' email to the admin: top-rated driver last month + their best customer comments."""
from fastapi import APIRouter, HTTPException, Request
from html import escape
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone, date as date_cls, time as time_cls
from typing import Optional
from core import ADMIN_ONLY, TZ, db, logger, now_iso
from routes import emails, onboarding
from routes import merchants as merchant_routes

router = APIRouter(prefix="/api")

AWARD_MIN_RATINGS = 5
AWARD_SEND_HOUR = 8  # 1st of the month, 08:00 Eastern
AWARD_COMMENTS = 3
SEND_NOW_COOLDOWN_MIN = 5


def last_month(now: Optional[datetime] = None) -> tuple:
    """(label 'YYYY-MM', start UTC iso, end UTC iso) of the previous calendar month in Eastern time."""
    now = now or datetime.now(TZ)
    first_this = now.date().replace(day=1)
    first_prev = (first_this - timedelta(days=1)).replace(day=1)
    start = datetime.combine(first_prev, time_cls(0, 0), TZ).astimezone(timezone.utc).isoformat()
    end = datetime.combine(first_this, time_cls(0, 0), TZ).astimezone(timezone.utc).isoformat()
    return first_prev.strftime("%Y-%m"), start, end


async def month_award(month: str, start: str, end: str) -> dict:
    """Top-rated driver (min ratings, highest average, then most ratings) and their best comments."""
    docs = await db.stops.find({"rating": {"$ne": None}, "rated_at": {"$gte": start, "$lt": end}},
                               {"_id": 0, "route_id": 1, "rating": 1, "rating_comment": 1, "rating_tags": 1, "rated_at": 1}).to_list(10000)
    rids = list({d["route_id"] for d in docs if d.get("route_id")})
    route_driver = {r["id"]: r.get("driver_id") for r in await db.routes.find({"id": {"$in": rids}}, {"_id": 0, "id": 1, "driver_id": 1}).to_list(10000)}
    per: dict = {}
    for d in docs:
        did = route_driver.get(d.get("route_id"))
        if did:
            per.setdefault(did, []).append(d)
    names = {x["id"]: x["name"] for x in await db.drivers.find({"deleted": {"$ne": True}}, {"_id": 0, "id": 1, "name": 1}).to_list(500)}
    board = sorted([{"driver_id": k, "name": names[k], "count": len(v), "avg": round(sum(x["rating"] for x in v) / len(v), 2)}
                    for k, v in per.items() if k in names and len(v) >= AWARD_MIN_RATINGS], key=lambda x: (-x["avg"], -x["count"]))
    winner = board[0] if board else None
    comments = []
    if winner:
        good = [x for x in per[winner["driver_id"]] if x["rating"] >= 4 and (x.get("rating_comment") or "").strip()]
        good.sort(key=lambda x: (-x["rating"], -len(x["rating_comment"])))
        comments = [{"stars": x["rating"], "comment": x["rating_comment"].strip()} for x in good[:AWARD_COMMENTS]]
        compliments: dict = {}
        for x in per[winner["driver_id"]]:
            for t in x.get("rating_tags") or []:
                if t in ("ON TIME", "FRIENDLY DRIVER", "HANDLED WITH CARE", "EASY TO FOLLOW", "DISCREET"):
                    compliments[t] = compliments.get(t, 0) + 1
        winner = {**winner, "comments": comments, "compliments": sorted(compliments.items(), key=lambda kv: -kv[1])[:3]}
    label = date_cls.fromisoformat(month + "-01").strftime("%B %Y")
    return {"month": month, "label": label, "winner": winner, "runners_up": board[1:3], "eligible": len(board), "min_ratings": AWARD_MIN_RATINGS}


def award_html(a: dict) -> tuple:
    e = escape
    w = a["winner"]
    if not w:
        return (f"Driver of the Month — {a['label']}: no winner",
                f"<p>No driver had {a['min_ratings']} or more customer ratings in {e(a['label'])}, so there's no Driver of the Month this time.</p>")
    quotes = "".join(f'<p style="margin:0 0 10px;padding:8px 12px;border-left:4px solid #E60000;font-style:italic">'
                     f'{"★" * c["stars"]} &ldquo;{e(c["comment"])}&rdquo;</p>' for c in w["comments"]) or "<p>No written comments this month.</p>"
    tags = ", ".join(f"{e(t.lower())} ({n})" for t, n in w["compliments"])
    runners = "".join(f"<li>{e(r['name'])} · {r['avg']}★ from {r['count']} ratings</li>" for r in a["runners_up"])
    body = (f"<p><b style=\"font-size:20px\">🏆 {e(w['name'])}</b><br>{w['avg']}★ average from {w['count']} customer ratings in {e(a['label'])}.</p>"
            + (f"<p>Top compliments: {tags}</p>" if tags else "")
            + "<p><b>Best customer comments</b></p>" + quotes
            + (f"<p><b>Runners-up</b></p><ul>{runners}</ul>" if runners else "")
            + "<p>Consider giving them a shout-out or a small thank-you.</p>")
    return f"Driver of the Month — {a['label']}: {w['name']} ({w['avg']}★)", body


async def send_award(trigger: str, base: Optional[str] = None) -> dict:
    month, start, end = last_month()
    a = await month_award(month, start, end)
    to = os.environ.get("ADMIN_NOTIFY_EMAIL") or os.environ.get("ADMIN_EMAIL")
    base = base or (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    log = {"id": str(uuid.uuid4()), "month": month, "trigger": trigger, "to": to, "created_at": now_iso(),
           "winner": (a["winner"] or {}).get("name"), "status": "sent", "error": None}
    subject, body = award_html(a)
    try:
        if not to:
            raise RuntimeError("No admin email configured")
        await emails.send_email(to=to, subject=subject, html=onboarding.brand_email(subject, body, ("Open the admin panel", f"{base}/admin") if base else None))
    except Exception as ex:
        log.update(status="failed", error=str(ex))
    await db.award_emails.insert_one(dict(log))
    log.pop("_id", None)
    return log


async def award_scheduler():
    """Every 10 min: on the 1st from 08:00 Eastern, email last month's Driver of the Month once (max 3 tries)."""
    while True:
        try:
            now = datetime.now(TZ)
            if now.day == 1 and now.hour >= AWARD_SEND_HOUR:
                month = last_month(now)[0]
                cur = await db.settings.find_one({"key": "award_month"}, {"_id": 0, "value": 1})
                if cur is None:
                    await db.settings.insert_one({"key": "award_month", "value": None})
                claimed = await db.settings.update_one({"key": "award_month", "value": {"$ne": month}}, {"$set": {"value": month}})
                if claimed.modified_count:
                    log = await send_award("scheduled")
                    tries = await db.award_emails.count_documents({"month": month, "trigger": "scheduled"})
                    if log["status"] == "failed" and tries < 3:  # retry on the next check, at most 3 tries a month
                        await db.settings.update_one({"key": "award_month"}, {"$set": {"value": None}})
        except Exception as e:
            logger.error(f"Driver award scheduler error: {e}")
        await asyncio.sleep(600)


@router.get("/admin/driver-award", dependencies=ADMIN_ONLY)
async def get_driver_award():
    month, start, end = last_month()
    last = await db.award_emails.find({}, {"_id": 0}).sort("created_at", -1).to_list(1)
    return {**(await month_award(month, start, end)), "last": last[0] if last else None,
            "to": os.environ.get("ADMIN_NOTIFY_EMAIL") or os.environ.get("ADMIN_EMAIL")}


@router.post("/admin/driver-award/send-now", dependencies=ADMIN_ONLY)
async def send_driver_award_now(request: Request):
    await merchant_routes.remember_public_base(request)
    since = (datetime.now(timezone.utc) - timedelta(minutes=SEND_NOW_COOLDOWN_MIN)).isoformat()
    if await db.award_emails.find_one({"trigger": "manual", "status": "sent", "created_at": {"$gt": since}}):
        raise HTTPException(429, "Award email was just sent — try again in a few minutes")
    return await send_award("manual", await onboarding.app_base(request))
