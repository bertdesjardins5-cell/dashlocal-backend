from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import Response
from html import escape
import io
import csv
import re
from pydantic import BaseModel
from typing import Optional, Dict
from datetime import datetime, timezone, timedelta, date as date_cls, time as time_cls
from core import ADMIN_ONLY, DEPOT, MIN_PACE_HOURS, RUNS, TZ, admin_user, current_user, db, decode_token, haversine_m, hhmm12, issue_token, logger, merchant_access, now_iso, today_str
from models import Route, Stop, get_stops
from routes import dispatch
from routes import emails
from routes import merchants as merchant_routes
from routes import onboarding
from routes import pickups as pickup_routes
from routes import scorecards
from services import realtime
from services import seed

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- pricing & weekly merchant bills (CAD, collected outside the app)
VOLUME_TIERS = [  # (name, min delivered drops per shop per day, rate per drop; None = standard per-delivery fee)
    ("Pay-As-You-Go", 1, None), ("Growth tier", 5, 7.49), ("High-Volume", 10, 6.49), ("High-Volume", 15, 5.99)]


def volume_tier(count: int, base_fee: float) -> dict:
    name, rate = VOLUME_TIERS[0][0], base_fee
    for n, lo, r in VOLUME_TIERS:
        if count >= lo:
            name, rate = n, (base_fee if r is None else min(r, base_fee))
    return {"name": name, "rate": round(rate, 2)}


PRICING_DEFAULT = {"currency": "CAD", "base_fee": 8.99, "included_km": 5.0, "per_km": 1.25, "priority_fee": 16.00,
                   "charge_failed": True, "tax_pct": 0.0}
PRICING_LIMITS = {"base_fee": 1000, "included_km": 500, "per_km": 100, "priority_fee": 1000, "tax_pct": 30}
PRICING_NAMES = {"base_fee": "Per-delivery fee", "included_km": "Km included", "per_km": "Extra per km",
                 "priority_fee": "Priority surcharge", "tax_pct": "Sales tax %"}
ROAD_FACTOR = 1.3  # straight-line depot→drop distance × this ≈ road distance


async def get_pricing(merchant_id: Optional[str] = None) -> dict:
    """Shared price list, with a store's custom rates layered on top when merchant_id is given."""
    doc = await db.settings.find_one({"key": "pricing"}, {"_id": 0}) or {}
    p = {**PRICING_DEFAULT, **(doc.get("value") or {})}
    if merchant_id:
        m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0, "pricing_override": 1, "pharmacy_partner": 1, "pickup": 1}) or {}
        p.update(m.get("pricing_override") or {})
        if m.get("pharmacy_partner"):  # Addendum A contract replaces the shared / custom per-drop rates
            p.update(base_fee=PHARMACY["standard_rate"], included_km=PHARMACY["included_km"], per_km=PHARMACY["per_km"],
                     pharmacy=True, origin=m.get("pickup") or DEPOT)
    return p


# ---------------------------------------------------------------- pharmacy batch contract (Addendum A)
PHARMACY = {"batch_rate": 6.25, "standard_rate": 8.99, "min_drops": 10, "included_km": 8.0, "per_km": 1.25, "monthly_fee": 25.00}


def runs_today() -> list:
    """Each run's next pickup: today until its cut-off, then tomorrow's run of the same name."""
    now = datetime.now(TZ)
    out = []
    for k, r in RUNS.items():
        today = now.strftime("%H:%M") < r["cutoff"]
        out.append({"key": k, **r, "open": True, "today": today,
                    "date": (now.date() if today else now.date() + timedelta(days=1)).isoformat(), "cutoff_label": hhmm12(r["cutoff"]),
                    "pickup_label": f"{hhmm12(r['pickup'][0])} – {hhmm12(r['pickup'][1])}",
                    "window_label": f"{hhmm12(r['window'][0])} – {hhmm12(r['window'][1])}"})
    return out


def compute_fee(stop: dict, p: dict) -> dict:
    km = round(haversine_m(p.get("origin") or DEPOT, stop) / 1000 * ROAD_FACTOR, 1)
    dist = round(max(0.0, km - p["included_km"]) * p["per_km"], 2)
    pri = round(p["priority_fee"], 2) if stop.get("priority") else 0.0
    return {"base": round(p["base_fee"], 2), "km": km, "distance_fee": dist, "priority_fee": pri,
            "total": round(p["base_fee"] + dist + pri, 2), "currency": p["currency"], "priced_at": now_iso()}


class PricingIn(BaseModel):
    base_fee: float
    included_km: float
    per_km: float
    priority_fee: float
    charge_failed: bool
    tax_pct: float


@router.get("/pricing")
async def read_pricing(u: dict = Depends(current_user)):
    if u["role"] not in ("admin", "merchant"):
        raise HTTPException(403, "Not allowed")
    return await get_pricing(u.get("merchant_id") if u["role"] == "merchant" else None)


@router.put("/admin/pricing", dependencies=ADMIN_ONLY)
async def update_pricing(body: PricingIn):
    """New prices apply to orders created (or re-priced) from now on; existing orders keep their price."""
    v = body.model_dump()
    for k, cap in PRICING_LIMITS.items():
        if not (0 <= v[k] <= cap) or v[k] != v[k]:
            raise HTTPException(400, f"{PRICING_NAMES[k]} must be between 0 and {cap}")
        v[k] = round(v[k], 2)
    await db.settings.update_one({"key": "pricing"}, {"$set": {"key": "pricing", "value": v, "updated_at": now_iso()}}, upsert=True)
    return await get_pricing()


def week_bounds(week: Optional[str]) -> tuple:
    """Monday..Sunday (Eastern) containing `week` (any date), default this week."""
    today = datetime.now(TZ).date()
    try:
        d = date_cls.fromisoformat(week) if week else today
    except ValueError:
        raise HTTPException(400, "week must be YYYY-MM-DD")
    start = d - timedelta(days=d.weekday())
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


async def pharmacy_summary(merchant_id: str, week: Optional[str]) -> dict:
    """Pharmacy weekly summary: drops, on-time pickups, batch-rate savings and patient feedback (Mon..Sun Eastern)."""
    since, until = week_bounds(week)
    days = [(date_cls.fromisoformat(since) + timedelta(days=i)).isoformat() for i in range(7)]
    manifests = [f"{merchant_id}:{d}:{k}" for d in days for k in RUNS]
    stops = await db.stops.find({"merchant_id": merchant_id, "manifest_id": {"$in": manifests}},
                                {"_id": 0, "status": 1, "rating": 1, "rating_tags": 1, "manifest_id": 1}).to_list(5000)
    pickups = await db.pickups.find({"manifest_id": {"$in": manifests}}, {"_id": 0, "on_time": 1}).to_list(500)
    invs = await db.invoices.find({"merchant_id": merchant_id, "date": {"$gte": since, "$lte": until}, "kind": {"$ne": "platform"}},
                                  {"_id": 0, "tier": 1}).to_list(5000)
    batch_drops = sum(1 for i in invs if i.get("tier") == "Pharmacy batch")
    rated = [s["rating"] for s in stops if s.get("rating")]
    tags: Dict[str, int] = {}
    for s in stops:
        for t in s.get("rating_tags") or []:
            tags[t] = tags.get(t, 0) + 1
    on_time = sum(1 for p in pickups if p.get("on_time"))
    return {"week_start": since, "week_end": until, "drops": len(stops),
            "delivered": sum(1 for s in stops if s["status"] == "delivered"), "failed": sum(1 for s in stops if s["status"] == "failed"),
            "pickups": len({s["manifest_id"] for s in stops}), "pickups_checked_in": len(pickups), "pickups_on_time": on_time,
            "on_time_pct": round(100 * on_time / len(pickups)) if pickups else None,
            "batch_rate_drops": batch_drops, "savings": round(batch_drops * (PHARMACY["standard_rate"] - PHARMACY["batch_rate"]), 2),
            "avg_rating": round(sum(rated) / len(rated), 1) if rated else None, "ratings": len(rated),
            "top_tags": sorted(tags.items(), key=lambda x: -x[1])[:5]}


@router.get("/merchants/{merchant_id}/pharmacy-summary")
async def get_pharmacy_summary(merchant_id: str, week: Optional[str] = None, _: dict = Depends(merchant_access)):
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    if not m.get("pharmacy_partner"):
        raise HTTPException(400, "Weekly summary is for pharmacy partners")
    return await pharmacy_summary(merchant_id, week)


@router.post("/merchants/{merchant_id}/pharmacy-summary/email")
async def send_pharmacy_summary_now(merchant_id: str, week: Optional[str] = None, _: dict = Depends(merchant_access)):
    """Email this week's (or `week`'s) summary to the pharmacy's logins now."""
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    if not m.get("pharmacy_partner"):
        raise HTTPException(400, "Weekly summary is for pharmacy partners")
    if not (await pharmacy_summary(merchant_id, week))["drops"]:
        raise HTTPException(400, "No pharmacy drops that week — nothing to send")
    await email_pharmacy_summary(merchant_id, week or today_str())
    return {"sent_to": await pickup_routes.merchant_alert_recipients(merchant_id)}


async def email_pharmacy_summary(merchant_id: str, week: str):
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    s = await pharmacy_summary(merchant_id, week)
    if not s["drops"]:
        return
    tags = ", ".join(f"{t.title()} ({n})" for t, n in s["top_tags"]) or "none yet"
    pct = f" ({s['on_time_pct']}%)" if s["on_time_pct"] is not None else ""
    body = (f"<p>Week of <b>{seed.nice_date(s['week_start'])}</b> – {seed.nice_date(s['week_end'])}</p><ul>"
            f"<li><b>{s['drops']}</b> drops · {s['delivered']} delivered · {s['failed']} not delivered</li>"
            f"<li><b>{s['pickups_on_time']}/{s['pickups_checked_in']}</b> pickups on time{pct}</li>"
            f"<li><b>{s['batch_rate_drops']}</b> drops at the batch rate · <b>${s['savings']:.2f}</b> saved</li>"
            f"<li>Patient rating: <b>{s['avg_rating'] or '—'}</b> from {s['ratings']} ratings · {escape(tags)}</li></ul>")
    subject = f"{m['name']}: your DashLocal week — {s['drops']} drops, ${s['savings']:.2f} saved"
    for to in await pickup_routes.merchant_alert_recipients(merchant_id):
        try:
            await emails.send_email(to=to, subject=subject, html=onboarding.brand_email("Your week with DashLocal", body))
        except Exception as e:
            logger.warning(f"Pharmacy summary email failed: {e}")


async def merchant_bill(merchant_id: str, week: Optional[str]) -> dict:
    p = await get_pricing(merchant_id)
    since, until = week_bounds(week)
    lines, open_count, open_est = [], 0, 0.0
    # only open orders + drops completed around this week (UTC bounds padded a day; exact Eastern day filter below)
    lo = (date_cls.fromisoformat(since) - timedelta(days=1)).isoformat()
    hi = (date_cls.fromisoformat(until) + timedelta(days=2)).isoformat()
    q = {"merchant_id": merchant_id, "$or": [{"status": {"$in": ["pending", "arrived"]}}, {"delivered_at": {"$gte": lo, "$lt": hi}}]}
    for d in await db.stops.find(q, {"_id": 0}).sort("delivered_at", 1).to_list(20000):
        st = Stop(**d)
        fee = st.fee or compute_fee(d, p)
        if st.status in ("pending", "arrived"):
            open_count += 1
            open_est += fee["total"]
            continue
        if st.status == "failed" and not p["charge_failed"]:
            continue
        day = datetime.fromisoformat(st.delivered_at).astimezone(TZ).date().isoformat() if st.delivered_at else None
        if day and since <= day <= until:
            lines.append({"stop_id": st.id, "tracking_code": st.tracking_code, "customer_name": st.customer_name, "day": day,
                          "status": st.status, "priority": st.priority, **{k: fee[k] for k in ("km", "base", "distance_fee", "priority_fee", "total")}})
    sub = round(sum(x["total"] for x in lines), 2)
    tax = round(sub * p["tax_pct"] / 100, 2)
    this_monday = date_cls.fromisoformat(week_bounds(None)[0])
    pay = await db.bill_payments.find_one({"merchant_id": merchant_id, "week_start": since}, {"_id": 0})
    return {"merchant_id": merchant_id, "week_start": since, "week_end": until, "current_week": since == this_monday.isoformat(),
            "can_mark_paid": until < datetime.now(TZ).date().isoformat(), "payment": pay,
            "currency": p["currency"], "tax_pct": p["tax_pct"], "lines": lines, "drops": len(lines),
            "base_total": round(sum(x["base"] for x in lines), 2), "distance_total": round(sum(x["distance_fee"] for x in lines), 2),
            "priority_total": round(sum(x["priority_fee"] for x in lines), 2), "subtotal": sub, "tax": tax, "total": round(sub + tax, 2),
            "open_orders": {"count": open_count, "estimate": round(open_est, 2)},
            "weeks": [(this_monday - timedelta(weeks=i)).isoformat() for i in range(8)]}


@router.get("/merchants/{merchant_id}/bill")
async def get_bill(merchant_id: str, week: Optional[str] = None, _: dict = Depends(merchant_access)):
    await merchant_routes.get_merchant_or_404(merchant_id)
    return {**(await merchant_bill(merchant_id, week)), "outstanding": await merchant_outstanding(merchant_id)}


OUTSTANDING_WEEKS = 26


async def merchant_outstanding(merchant_id: str) -> dict:
    """Finished weeks (up to 26 back) with a bill > 0 that are not marked paid."""
    first = await db.stops.find({"merchant_id": merchant_id, "delivered_at": {"$ne": None}}, {"_id": 0, "delivered_at": 1}).sort("delivered_at", 1).to_list(1)
    if not first:
        return {"total": 0.0, "weeks": []}
    this_monday = date_cls.fromisoformat(week_bounds(None)[0])
    start = max(date_cls.fromisoformat(week_bounds(datetime.fromisoformat(first[0]["delivered_at"]).astimezone(TZ).date().isoformat())[0]),
                this_monday - timedelta(weeks=OUTSTANDING_WEEKS))
    paid = {x["week_start"] for x in await db.bill_payments.find({"merchant_id": merchant_id}, {"_id": 0, "week_start": 1}).to_list(1000)}
    weeks, wk = [], start
    while wk < this_monday:
        if wk.isoformat() not in paid:
            b = await merchant_bill(merchant_id, wk.isoformat())
            if b["total"] > 0:
                weeks.append({"week_start": b["week_start"], "week_end": b["week_end"], "total": b["total"]})
        wk += timedelta(weeks=1)
    return {"total": round(sum(x["total"] for x in weeks), 2), "weeks": weeks}


class StatementIn(BaseModel):
    week: Optional[str] = None


@router.post("/merchants/{merchant_id}/statement-link")
async def statement_link(merchant_id: str, body: StatementIn, u: dict = Depends(merchant_access)):
    await merchant_routes.get_merchant_or_404(merchant_id)
    since, _ = week_bounds(body.week)
    tok = issue_token(u["id"], u["role"], purpose="statement", minutes=5, mid=merchant_id, wk=since)
    return {"path": f"/api/merchants/{merchant_id}/statement.csv?token={tok}"}


@router.get("/merchants/{merchant_id}/statement.csv")
async def statement_csv(merchant_id: str, token: str):
    p = decode_token(token, "statement")
    if p.get("mid") != merchant_id:
        raise HTTPException(403, "This link is for a different store")
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    b = await merchant_bill(merchant_id, p.get("wk"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{m['name']} — weekly statement {b['week_start']} to {b['week_end']} ({b['currency']})"])
    w.writerow(["date", "tracking_code", "customer_name", "status", "priority", "km", "base_fee", "distance_fee", "priority_fee", "total"])
    for x in b["lines"]:
        w.writerow([x["day"], x["tracking_code"], merchant_routes.csv_safe(x["customer_name"]), x["status"], "yes" if x["priority"] else "",
                    x["km"], f"{x['base']:.2f}", f"{x['distance_fee']:.2f}", f"{x['priority_fee']:.2f}", f"{x['total']:.2f}"])
    w.writerow([])
    w.writerow(["", "", "", "", "", "", "", "", "Subtotal", f"{b['subtotal']:.2f}"])
    w.writerow(["", "", "", "", "", "", "", "", f"Tax {b['tax_pct']}%", f"{b['tax']:.2f}"])
    w.writerow(["", "", "", "", "", "", "", "", "Total", f"{b['total']:.2f}"])
    slug = re.sub(r"[^a-z0-9]+", "-", m["name"].lower()).strip("-")
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-statement-{b["week_start"]}.csv"'})


@router.get("/admin/billing", dependencies=ADMIN_ONLY)
async def admin_billing(week: Optional[str] = None):
    out = []
    since, until = week_bounds(week)
    for m in await db.merchants.find({}, {"_id": 0}).sort("id", 1).to_list(500):
        b = await merchant_bill(m["id"], week)
        owed = await merchant_outstanding(m["id"])
        out.append({"id": m["id"], "name": m["name"], "drops": b["drops"], "subtotal": b["subtotal"], "tax": b["tax"], "total": b["total"],
                    "open_orders": b["open_orders"], "payment": b["payment"], "can_mark_paid": b["can_mark_paid"],
                    "outstanding": owed["total"], "unpaid_weeks": len(owed["weeks"]), "custom_rates": bool(m.get("pricing_override"))})
    this_monday = date_cls.fromisoformat(week_bounds(None)[0])
    return {"week_start": since, "week_end": until, "stores": out, "total": round(sum(x["total"] for x in out), 2),
            "drops": sum(x["drops"] for x in out), "weeks": [(this_monday - timedelta(weeks=i)).isoformat() for i in range(8)],
            "outstanding": round(sum(x["outstanding"] for x in out), 2), "currency": (await get_pricing())["currency"]}


class PaidIn(BaseModel):
    paid: bool
    note: Optional[str] = None


@router.put("/admin/merchants/{merchant_id}/bills/{week}/paid")
async def mark_bill_paid(merchant_id: str, week: str, body: PaidIn, u: dict = Depends(admin_user)):
    """Mark a finished week's bill as paid (payment collected outside the app) or back to unpaid."""
    await merchant_routes.get_merchant_or_404(merchant_id)
    since, until = week_bounds(week)
    if until >= datetime.now(TZ).date().isoformat():
        raise HTTPException(400, "This week isn't over yet — bills can be marked paid from Monday")
    if body.paid:
        note = (body.note or "").strip()
        if not note:
            raise HTTPException(400, "Add a payment note (e.g. e-Transfer reference or cheque number)")
        b = await merchant_bill(merchant_id, since)
        await db.bill_payments.update_one({"merchant_id": merchant_id, "week_start": since}, {"$set": {
            "merchant_id": merchant_id, "week_start": since, "amount": b["total"], "paid_at": now_iso(), "by": u["email"],
            "note": note[:200]}}, upsert=True)
    else:
        await db.bill_payments.delete_one({"merchant_id": merchant_id, "week_start": since})
    return await merchant_bill(merchant_id, since)


class StoreRatesIn(BaseModel):
    base_fee: Optional[float] = None
    included_km: Optional[float] = None
    per_km: Optional[float] = None
    priority_fee: Optional[float] = None
    charge_failed: Optional[bool] = None
    tax_pct: Optional[float] = None


async def store_rates(merchant_id: str) -> dict:
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    return {"merchant_id": merchant_id, "name": m["name"], "shared": await get_pricing(), "override": m.get("pricing_override") or {},
            "effective": await get_pricing(merchant_id), "pharmacy_partner": bool(m.get("pharmacy_partner")), "pickup": m.get("pickup")}


@router.get("/admin/merchants/{merchant_id}/pricing", dependencies=ADMIN_ONLY)
async def get_store_rates(merchant_id: str):
    return await store_rates(merchant_id)


@router.put("/admin/merchants/{merchant_id}/pricing", dependencies=ADMIN_ONLY)
async def set_store_rates(merchant_id: str, body: StoreRatesIn):
    """Custom rates for one store. Fields left empty (null) follow the shared price list. New orders only."""
    await merchant_routes.get_merchant_or_404(merchant_id)
    v = {k: x for k, x in body.model_dump().items() if x is not None}
    for k, cap in PRICING_LIMITS.items():
        if k in v:
            if not (0 <= v[k] <= cap) or v[k] != v[k]:
                raise HTTPException(400, f"{PRICING_NAMES[k]} must be between 0 and {cap}")
            v[k] = round(v[k], 2)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"pricing_override": v}})
    return await store_rates(merchant_id)


class PriorityIn(BaseModel):
    priority: bool


@router.put("/merchants/{merchant_id}/orders/{stop_id}/priority")
async def set_order_priority(merchant_id: str, stop_id: str, body: PriorityIn, _: dict = Depends(merchant_access)):
    """Merchant marks an order as priority (or not) before dispatch; the order is re-priced."""
    st = await dispatch.get_stop_or_404(stop_id)
    if st.merchant_id != merchant_id:
        raise HTTPException(404, "Order not found")
    if st.route_id or st.status != "pending":
        raise HTTPException(400, "Order is already dispatched — ask dispatch to change priority")
    d = {**st.model_dump(), "priority": body.priority}
    await db.stops.update_one({"id": stop_id}, {"$set": {"priority": body.priority, "fee": compute_fee(d, await get_pricing(merchant_id))}})
    return (await dispatch.get_stop_or_404(stop_id)).model_dump()


@router.get("/digest", dependencies=ADMIN_ONLY)
async def daily_digest(date: Optional[str] = None):
    """End-of-day ops digest for one depot day: pace, deliveries by hour, late drops, failures and complaints."""
    day = date or today_str()
    try:
        date_cls.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    routes = [Route(**d) for d in await db.routes.find({"service_date": day, "started_at": {"$ne": None}}, {"_id": 0}).to_list(500)]
    drivers = {d["id"]: d["name"] for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)}
    hours, all_stops, per_driver = 0.0, [], {}
    for r in routes:
        end = datetime.fromisoformat(r.completed_at) if r.completed_at else datetime.now(timezone.utc)
        h = max((end - datetime.fromisoformat(r.started_at)).total_seconds() / 3600, MIN_PACE_HOURS)
        hours += h
        stops = await get_stops(r.id)
        name = drivers.get(r.driver_id, "Unassigned")
        pd = per_driver.setdefault(r.driver_id or "-", {"driver_id": r.driver_id, "name": name, "delivered": 0, "failed": 0, "late": 0, "complaints": 0, "hours": 0.0})
        pd["hours"] += h
        for s in stops:
            all_stops.append((s, name, pd))

    def late_min(s: Stop) -> int:
        local = datetime.fromisoformat(s.delivered_at).astimezone(TZ)
        hh, mm = map(int, s.window_end.split(":"))
        wday = date_cls.fromisoformat(s.window_date) if s.window_date else local.date()
        return max(1, round((local - datetime.combine(wday, time_cls(hh, mm), TZ)).total_seconds() / 60))

    by_hour: Dict[int, int] = {}
    late, failed, complaints, ratings = [], [], [], []
    delivered = 0
    for s, name, pd in all_stops:
        if s.status == "delivered":
            delivered += 1
            pd["delivered"] += 1
            hr = datetime.fromisoformat(s.delivered_at).astimezone(TZ).hour
            by_hour[hr] = by_hour.get(hr, 0) + 1
            if scorecards.delivered_on_time(s) is False:
                pd["late"] += 1
                late.append({"stop_id": s.id, "tracking_code": s.tracking_code, "customer_name": s.customer_name, "driver": name,
                             "window": f"{s.window_start}-{s.window_end}", "delivered_at": s.delivered_at, "late_min": late_min(s)})
            if s.rating:
                ratings.append(s.rating)
                if s.rating <= 2:
                    pd["complaints"] += 1
                    complaints.append({"stop_id": s.id, "tracking_code": s.tracking_code, "customer_name": s.customer_name, "driver": name,
                                       "rating": s.rating, "comment": s.rating_comment, "follow_up_status": s.follow_up_status})
        elif s.status == "failed":
            pd["failed"] += 1
            failed.append({"stop_id": s.id, "tracking_code": s.tracking_code, "customer_name": s.customer_name, "driver": name, "reason": s.fail_reason})
    hourly = [{"hour": h, "count": by_hour.get(h, 0)} for h in range(min(by_hour), max(by_hour) + 1)] if by_hour else []
    windowed = sum(1 for s, _, _ in all_stops if s.status == "delivered" and s.window_end)
    drivers_out = sorted(
        [{**p, "hours": round(p["hours"], 2), "drops_per_hour": round(p["delivered"] / p["hours"], 1) if p["hours"] else 0} for p in per_driver.values()],
        key=lambda p: -p["drops_per_hour"])
    return {
        "date": day, "routes": len(routes), "total_stops": len(all_stops), "delivered": delivered, "failed": len(failed),
        "remaining": len(all_stops) - delivered - len(failed), "hours": round(hours, 2),
        "drops_per_hour": round(delivered / hours, 1) if hours else 0,
        "late_count": len(late), "windowed_delivered": windowed,
        "on_time_rate": round((windowed - len(late)) / windowed * 100) if windowed else None,
        "complaint_count": len(complaints), "avg_rating": scorecards.avg(ratings), "ratings": len(ratings),
        "hourly": hourly, "late": sorted(late, key=lambda x: -x["late_min"]), "failed_stops": failed,
        "complaints": sorted(complaints, key=lambda x: x["rating"]), "drivers": drivers_out,
    }


@router.websocket("/ws/track/{code}")
async def ws_track(ws: WebSocket, code: str):
    room = f"track:{code.upper()}"
    await realtime.hub.join(room, ws)
    try:
        await ws.send_json(await realtime.tracking_payload(code))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, HTTPException):
        pass
    finally:
        realtime.hub.leave(room, ws)


@router.websocket("/ws/route/{route_id}")
async def ws_route(ws: WebSocket, route_id: str, token: str = ""):
    try:
        p = decode_token(token, "session")
        u = await db.users.find_one({"id": p["sub"], "disabled": {"$ne": True}}, {"_id": 0})
        r = await db.routes.find_one({"id": route_id}, {"_id": 0, "driver_id": 1})
        ok = bool(u) and (u["role"] == "admin" or (u["role"] == "driver" and r and r.get("driver_id") == u.get("driver_id")))
    except HTTPException:
        ok = False
    if not ok:
        await ws.close(code=4401)
        return
    room = f"route:{route_id}"
    await realtime.hub.join(room, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        realtime.hub.leave(room, ws)
