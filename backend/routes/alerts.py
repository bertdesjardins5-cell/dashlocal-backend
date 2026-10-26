from fastapi import APIRouter, Depends
from html import escape
import asyncio
import uuid
from pydantic import BaseModel
from typing import List, Optional
from datetime import timedelta
from core import DEPOT, db, logger, merchant_access, now_iso, plan_base, schedule, today_str
from models import Route, Stop, get_stops
from routes import auth
from routes import emails
from routes import merchants as merchant_routes
from services import realtime

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- merchant problem alerts (failed / running late)
LATE_GRACE_MIN = 5  # predicted arrival must be this far past the window end before we alert


async def maybe_late_alerts(route: Route, stops: List[Stop]):
    """Once per stop: email the merchant when the live ETA passes the end of the delivery window."""
    if route.status != "active" or route.service_date != today_str() or not route.driver_id:
        return
    open_ = [s for s in stops if s.status in ("pending", "arrived")]
    cand = {s.id for s in open_ if s.merchant_id and s.window_end and not s.merchant_late_alert_at}
    if not cand:
        return
    d = await db.drivers.find_one({"id": route.driver_id}, {"_id": 0})
    start = {"lat": d["lat"], "lng": d["lng"]} if d else DEPOT
    base = plan_base(route.service_date)
    for s, x in zip(open_, schedule(start, [o.model_dump() for o in open_], base)):
        if s.id in cand and x["late_min"] > LATE_GRACE_MIN:
            claimed = await db.stops.update_one({"id": s.id, "merchant_late_alert_at": None}, {"$set": {"merchant_late_alert_at": now_iso()}})
            if claimed.modified_count:
                eta = (base + timedelta(minutes=x["arrive_min"])).isoformat()
                realtime.spawn(send_merchant_alert(s, "late", eta=eta, late_min=round(x["late_min"])))


async def late_alert_watcher():
    """Every minute: re-check today's active routes so late alerts go out even when the driver app is idle."""
    while True:
        try:
            for r in await db.routes.find({"status": "active", "service_date": today_str()}, {"_id": 0}).to_list(200):
                route = Route(**r)
                await maybe_late_alerts(route, await get_stops(route.id))
        except Exception as e:
            logger.error(f"Late alert watcher error: {e}")
        await asyncio.sleep(60)


ALERT_COPY = {
    "late": ("is running late", "Our live ETA is past the delivery window. The driver is still on the way."),
    "delivered_late": ("was delivered late", "The drop was completed after the end of the delivery window."),
    "failed": ("could not be delivered", "The driver could not complete this drop."),
}


def merchant_alert_html(store: str, st: Stop, kind: str, driver: str, eta: Optional[str], late_min: Optional[int]) -> str:
    e = escape
    head, lead = ALERT_COPY[kind]
    rows = [("Tracking code", st.tracking_code), ("Customer", st.customer_name), ("Address", st.address),
            ("Window", f"{st.window_start}–{st.window_end}" if st.window_end else "—"), ("Driver", driver)]
    if kind == "late":
        rows.append(("New ETA", f"{merchant_routes.local_fmt(eta)[-5:]} (about {late_min} min late)"))
    if kind == "delivered_late":
        rows.append(("Delivered at", merchant_routes.local_fmt(st.delivered_at)[-5:]))
    if kind == "failed":
        rows.append(("Reason", st.fail_reason or "Not given"))
    trs = "".join(f'<tr><td style="padding:6px 0;border-bottom:1px solid #e5e5e5;color:#555">{e(k)}</td>'
                  f'<td style="padding:6px 0;border-bottom:1px solid #e5e5e5;text-align:right;font-weight:bold">{e(str(v))}</td></tr>' for k, v in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4"><tr><td align="center" style="padding:24px">'
            f'<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border:2px solid #0a0a0a;font-family:Arial,sans-serif;color:#0a0a0a">'
            f'<tr><td style="background:#e11d1d;color:#ffffff;padding:16px 24px;font-size:18px;font-weight:bold;letter-spacing:1px">DROP {e(st.tracking_code)} {e(head.upper())}</td></tr>'
            f'<tr><td style="padding:24px"><p style="margin:0 0 4px;font-size:16px;font-weight:bold">{e(store)}</p>'
            f'<p style="margin:0 0 16px;color:#555">{e(lead)}</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{trs}</table>'
            f'<p style="margin:16px 0 0;color:#777;font-size:12px">Times are Eastern (Kitchener-Waterloo) time. You get one email per affected drop — switch these alerts off in the Merchant console.</p>'
            f'</td></tr></table></td></tr></table>')


async def send_merchant_alert(st: Stop, kind: str, eta: Optional[str] = None, late_min: Optional[int] = None):
    """Email every login of the store about one problem drop. Logged in db.merchant_alerts."""
    log = {"id": str(uuid.uuid4()), "merchant_id": st.merchant_id, "stop_id": st.id, "tracking_code": st.tracking_code,
           "customer_name": st.customer_name, "kind": kind, "eta": eta, "late_min": late_min, "reason": st.fail_reason,
           "created_at": now_iso(), "status": "sent", "error": None, "recipients": []}
    try:
        m = await merchant_routes.get_merchant_or_404(st.merchant_id)
        if m.get("problem_alerts") is False:
            log["status"] = "off"
        else:
            users = await db.users.find({"merchant_id": st.merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0}).to_list(20)
            r = await db.routes.find_one({"id": st.route_id}, {"_id": 0, "driver_id": 1}) if st.route_id else None
            d = await db.drivers.find_one({"id": (r or {}).get("driver_id")}, {"_id": 0, "name": 1}) if r else None
            driver = d["name"].split(" ")[0] if d else "—"
            html = merchant_alert_html(m["name"], st, kind, driver, eta, late_min)
            subject = f"{m['name']}: drop {st.tracking_code} {ALERT_COPY[kind][0]}"
            demo = {em for _, em, _ in auth.DEMO_MERCHANT_USERS}
            errors = []
            for u in users:
                to = emails.TEST_INBOX if u["email"] in demo else u["email"]
                try:
                    eid = await emails.send_email(to=to, subject=subject, html=html)
                    log["recipients"].append({"login": u["email"], "to": to, "email_id": eid, "status": "sent"})
                except Exception as ex:
                    errors.append(str(ex))
                    log["recipients"].append({"login": u["email"], "to": to, "email_id": None, "status": "failed"})
            if not users:
                log.update(status="failed", error="Store has no login email")
            elif errors:
                log.update(status="failed" if len(errors) == len(users) else "partial", error=errors[0])
    except Exception as ex:
        logger.error(f"Merchant alert {kind} for {st.tracking_code} failed: {ex}")
        log.update(status="failed", error=str(ex)[:200])
    await db.merchant_alerts.insert_one(dict(log))


class AlertsIn(BaseModel):
    enabled: bool


async def merchant_alerts_status(merchant_id: str) -> dict:
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    recent = await db.merchant_alerts.find({"merchant_id": merchant_id}, {"_id": 0, "recipients": 0}).sort("created_at", -1).to_list(10)
    return {"enabled": m.get("problem_alerts", True), "recent": recent}


@router.get("/merchants/{merchant_id}/alerts")
async def get_merchant_alerts(merchant_id: str, _: dict = Depends(merchant_access)):
    return await merchant_alerts_status(merchant_id)


@router.put("/merchants/{merchant_id}/alerts")
async def set_merchant_alerts(merchant_id: str, body: AlertsIn, _: dict = Depends(merchant_access)):
    await merchant_routes.get_merchant_or_404(merchant_id)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"problem_alerts": body.enabled}})
    return await merchant_alerts_status(merchant_id)
