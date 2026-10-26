from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
import qrcode
import qrcode.image.svg
from xml.sax.saxutils import escape as xml_escape
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone, timedelta, date as date_cls, time as time_cls
from core import ADMIN_ONLY, DEPOT, DRIVER_SELF, MIN_PACE_HOURS, TZ, db, decode_token, issue_token, path_length_m, today_str
from models import Driver, Route, Stop, get_driver_or_404, get_route_or_404, get_stops

router = APIRouter(prefix="/api")


def delivered_on_time(st: Stop) -> Optional[bool]:
    """True/False for windowed deliveries, None when the stop had no window."""
    if not st.delivered_at or not st.window_end:
        return None
    local = datetime.fromisoformat(st.delivered_at).astimezone(TZ)
    day = date_cls.fromisoformat(st.window_date) if st.window_date else local.date()
    h, m = map(int, st.window_end.split(":"))
    return local <= datetime.combine(day, time_cls(h, m), TZ)


@router.get("/drivers/{driver_id}/summary", dependencies=DRIVER_SELF)
async def driver_summary(driver_id: str, date: Optional[str] = None):
    """End-of-day scorecard for a driver: drops, on-time rate, km driven, pace."""
    driver = await get_driver_or_404(driver_id)
    return await day_stats(driver, date or today_str())


def avg(xs):
    return round(sum(xs) / len(xs), 1) if xs else None


async def day_stats(driver: Driver, day: str) -> dict:
    routes = [Route(**d) for d in await db.routes.find({"driver_id": driver.id, "service_date": day, "started_at": {"$ne": None}}, {"_id": 0}).to_list(50)]
    stops: List[Stop] = []
    hours = 0.0
    for r in routes:
        stops += await get_stops(r.id)
        end = datetime.fromisoformat(r.completed_at) if r.completed_at else datetime.now(timezone.utc)
        hours += max((end - datetime.fromisoformat(r.started_at)).total_seconds() / 3600, MIN_PACE_HOURS)
    delivered = [x for x in stops if x.status == "delivered"]
    failed = [x for x in stops if x.status == "failed"]
    windowed = [x for x in delivered if x.window_end]
    on_time = sum(1 for x in delivered if delivered_on_time(x) is not False)
    km = sum(r.actual_distance_m for r in routes) / 1000
    planned_km = sum(r.planned_distance_m for r in routes) / 1000
    return {
        "driver": driver.model_dump(), "date": day,
        "routes": [{"id": r.id, "name": r.name, "status": r.status} for r in routes],
        "all_completed": bool(routes) and all(r.status == "completed" for r in routes),
        "total_stops": len(stops), "delivered": len(delivered), "failed": len(failed),
        "remaining": len(stops) - len(delivered) - len(failed),
        "on_time": on_time,
        # on-time rate counts only deliveries that had a customer window
        "on_time_rate": round(sum(1 for x in windowed if delivered_on_time(x)) / len(windowed) * 100) if windowed else None,
        "windowed_delivered": len(windowed), "windowed_on_time": sum(1 for x in windowed if delivered_on_time(x)),
        "km_driven": round(km, 2), "planned_km": round(planned_km, 2),
        "hours": round(hours, 2), "drops_per_hour": round(len(delivered) / hours, 1) if hours else 0,
        "geofence_arrivals": sum(1 for x in stops if x.arrival_method == "geofence"),
        "avg_rating": avg([x.rating for x in delivered if x.rating]), "ratings": sum(1 for x in delivered if x.rating),
        "pod": {"photo": sum(1 for x in delivered if x.pod_photo_path), "signature": sum(1 for x in delivered if x.pod_signature),
                "qr": sum(1 for x in delivered if x.pod_qr_verified)},
        "stops": [{"seq": x.seq, "route_id": x.route_id, "customer_name": x.customer_name, "tracking_code": x.tracking_code, "status": x.status,
                   "delivered_at": x.delivered_at, "window": f"{x.window_start}-{x.window_end}" if x.window_end else None,
                   "on_time": delivered_on_time(x), "fail_reason": x.fail_reason} for x in stops],
    }


@router.get("/drivers/{driver_id}/scorecard", dependencies=DRIVER_SELF)
async def driver_scorecard(driver_id: str, days: int = 7):
    """Rolling weekly scorecard: one row per day (oldest first), week totals and this week's badges."""
    sc = await scorecard_data(driver_id, days)
    board = await all_scorecards(days)
    sc["badges"] = next((c["badges"] for c in board if c["driver"]["id"] == driver_id), [])
    sc["rank"] = next((i + 1 for i, c in enumerate(board) if c["driver"]["id"] == driver_id), None)
    return sc


async def scorecard_data(driver_id: str, days: int = 7) -> dict:
    driver = await get_driver_or_404(driver_id)
    days = max(1, min(days, 31))
    today = datetime.now(TZ).date()
    rows = []
    for back in range(days - 1, -1, -1):
        d = await day_stats(driver, (today - timedelta(days=back)).isoformat())
        rows.append({k: d[k] for k in ("date", "delivered", "failed", "on_time_rate", "windowed_delivered", "windowed_on_time",
                                        "drops_per_hour", "km_driven", "hours", "avg_rating", "ratings")} | {"routes": len(d["routes"])})
    delivered = sum(r["delivered"] for r in rows)
    hours = sum(r["hours"] for r in rows)
    windowed = sum(r["windowed_delivered"] for r in rows)
    win_ok = sum(r["windowed_on_time"] for r in rows)
    rated = [(r["avg_rating"], r["ratings"]) for r in rows if r["ratings"]]
    return {
        "driver": driver.model_dump(), "from": rows[0]["date"], "to": rows[-1]["date"], "days": rows,
        "totals": {
            "delivered": delivered, "failed": sum(r["failed"] for r in rows), "days_worked": sum(1 for r in rows if r["routes"]),
            "on_time_rate": round(win_ok / windowed * 100) if windowed else None, "windowed_delivered": windowed,
            "ratings": sum(n for _, n in rated),
            "drops_per_hour": round(delivered / hours, 1) if hours else 0, "hours": round(hours, 2),
            "km_driven": round(sum(r["km_driven"] for r in rows), 1),
            "avg_rating": round(sum(a * n for a, n in rated) / sum(n for _, n in rated), 1) if rated else None,
        },
    }


@router.get("/drivers/{driver_id}/badges/new", dependencies=DRIVER_SELF)
async def new_badges(driver_id: str):
    """Badges the driver currently holds but hasn't celebrated yet."""
    doc = await db.drivers.find_one({"id": driver_id}, {"_id": 0, "seen_badges": 1})
    if doc is None:
        raise HTTPException(404, "Driver not found")
    seen = set(doc.get("seen_badges") or [])
    board = await all_scorecards(7)
    held = next((c["badges"] for c in board if c["driver"]["id"] == driver_id), [])
    lost = seen - {b["key"] for b in held}
    if lost:  # badge lost this week: forget it so winning it back is celebrated again
        await db.drivers.update_one({"id": driver_id}, {"$pull": {"seen_badges": {"$in": list(lost)}}})
    return {"badges": [b for b in held if b["key"] not in seen]}


class SeenIn(BaseModel):
    keys: List[str]


@router.post("/drivers/{driver_id}/badges/seen", dependencies=DRIVER_SELF)
async def mark_badges_seen(driver_id: str, body: SeenIn):
    await get_driver_or_404(driver_id)
    await db.drivers.update_one({"id": driver_id}, {"$addToSet": {"seen_badges": {"$each": body.keys}}})
    return {"ok": True}


@router.get("/scorecards", dependencies=ADMIN_ONLY)
async def all_scorecards(days: int = 7):
    """Week totals for every driver, best on-time rate first (admin dashboard)."""
    out = []
    for d in await db.drivers.find({}, {"_id": 0}).to_list(100):
        sc = await scorecard_data(d["id"], days)
        out.append({"driver": sc["driver"], **sc["totals"], "trend": [r["delivered"] for r in sc["days"]]})
    award_badges(out)
    return sorted(out, key=lambda x: (-(x["on_time_rate"] if x["on_time_rate"] is not None else -1), -x["drops_per_hour"]))


BADGES = [
    # key, label, metric, minimum sample (field, n)
    ("on_time", "ON-TIME CHAMP", "on_time_rate", ("windowed_delivered", 3)),
    ("top_rated", "TOP RATED", "avg_rating", ("delivered", 3)),
    ("speed", "FASTEST PACE", "drops_per_hour", ("delivered", 5)),
    ("volume", "MOST DROPS", "delivered", ("delivered", 1)),
]


def award_badges(cards: list):
    """Weekly badges: best driver per metric (ties share the badge), minimum sample required."""
    for c in cards:
        c["badges"] = []
    for key, label, metric, (field, n) in BADGES:
        eligible = [c for c in cards if c.get(metric) is not None and c.get(field, 0) >= n]
        if not eligible:
            continue
        best = max(c[metric] for c in eligible)
        for c in eligible:
            if c[metric] == best:
                c["badges"].append({"key": key, "label": label, "value": best})


def qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=1)
    return img.to_string(encoding="unicode")


@router.post("/routes/{route_id}/labels-link", dependencies=ADMIN_ONLY)
async def labels_link(route_id: str):
    """Short-lived (10 min) print link so the label sheet can open in a new tab / print dialog."""
    await get_route_or_404(route_id)
    tok = issue_token("labels", "admin", purpose="labels", minutes=10, rid=route_id)
    return {"path": f"/api/routes/{route_id}/labels.html?token={tok}"}


@router.get("/routes/{route_id}/labels.html", response_class=HTMLResponse)
async def route_labels(route_id: str, token: str = "", autoprint: int = 0):
    """Printable A4 sheet: one QR parcel label per open stop, 8 per page, in LOADING order
    (reverse drop order) so the first delivery is loaded last and sits by the door."""
    if decode_token(token, "labels").get("rid") != route_id:
        raise HTTPException(403, "This print link is for a different route")
    route = await get_route_or_404(route_id)
    stops = [x for x in await get_stops(route_id) if x.status not in ("delivered", "failed")][::-1]
    esc = xml_escape
    cards = []
    for load_no, x in enumerate(stops, 1):
        win = f"{x.window_date or ''} {x.window_start}-{x.window_end}".strip() if x.window_end else "ANY TIME"
        note = f'<div class="note">{esc(x.notes)}</div>' if x.notes else ''
        cards.append(f"""<div class="label"><div class="qr">{qr_svg('DASHLOCAL:' + x.tracking_code)}</div>
<div class="info"><div class="tags"><span class="load">LOAD {load_no:02d}</span><span class="seq">DROP #{x.seq:02d}</span></div><div class="code">{esc(x.tracking_code)}</div>
<div class="name">{esc(x.customer_name)}</div><div class="addr">{esc(x.address)}</div>
<div class="meta">{esc(x.postal_code)} &middot; {esc(x.zone.upper())}</div><div class="win">WINDOW {esc(win)}</div>{note}</div></div>""")
    script = '<script>window.onload = function () { setTimeout(function () { window.print(); }, 300); };</script>' if autoprint else ''
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(route.name)} labels</title>
<style>
@page {{ size: A4; margin: 8mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Courier New', monospace; margin: 0; color: #0A0A0A; }}
h1 {{ font-family: Arial, sans-serif; font-size: 16px; margin: 0 0 6px; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }}
.label {{ border: 2px solid #0A0A0A; height: 64mm; padding: 3mm; display: flex; gap: 3mm; page-break-inside: avoid; break-inside: avoid; }}
.qr svg {{ width: 34mm; height: 34mm; }}
.info {{ flex: 1; display: flex; flex-direction: column; gap: 1mm; overflow: hidden; }}
.tags {{ display: flex; gap: 2mm; }}
.seq {{ background: #0A0A0A; color: #fff; font-weight: bold; padding: 1mm 2mm; font-size: 12px; }}
.load {{ border: 2px solid #E60000; color: #E60000; font-weight: bold; padding: 0.5mm 2mm; font-size: 12px; }}
.hint {{ font-size: 11px; margin: 0 0 6px; }}
.code {{ font-family: Arial, sans-serif; font-weight: 900; font-size: 22px; }}
.name {{ font-weight: bold; font-size: 13px; }}
.addr, .meta {{ font-size: 11px; }}
.win {{ font-weight: bold; font-size: 11px; border-top: 2px solid #0A0A0A; padding-top: 1mm; }}
.note {{ font-size: 10px; background: #FFD700; padding: 1mm; }}
</style></head><body>
<h1>{esc(route.name)} &middot; {esc(route.service_date)} &middot; {len(stops)} PARCELS</h1>
<p class="hint">LOAD IN LABEL ORDER (LOAD 01 FIRST, DEEPEST IN THE VAN). THE FIRST DROP IS LOADED LAST, NEAREST THE DOOR.</p>
<div class="grid">{''.join(cards) or '<p>No open parcels on this route.</p>'}</div>
{script}
</body></html>"""
    return HTMLResponse(html)


@router.get("/analytics", dependencies=ADMIN_ONLY)
async def analytics():
    """Today's operations (history routes are excluded; see /scorecards for the week)."""
    routes = [Route(**d) for d in await db.routes.find({"service_date": today_str()}, {"_id": 0}).to_list(500)]
    ids = [r.id for r in routes]
    scope = {"$or": [{"route_id": {"$in": ids}}, {"route_id": None}]}
    delivered = await db.stops.count_documents({"route_id": {"$in": ids}, "status": "delivered"})
    failed = await db.stops.count_documents({"route_id": {"$in": ids}, "status": "failed"})
    total = await db.stops.count_documents(scope)
    unassigned = await db.stops.count_documents({"route_id": None})
    geofenced = await db.stops.count_documents({"route_id": {"$in": ids}, "arrival_method": "geofence"})
    arrivals = await db.stops.count_documents({"route_id": {"$in": ids}, "arrived_at": {"$ne": None}})
    rated = [d["rating"] for d in await db.stops.find({"route_id": {"$in": ids}, "rating": {"$ne": None}}, {"_id": 0, "rating": 1}).to_list(1000)]
    hours, drops, devs, per_route = 0.0, 0, [], []
    for r in routes:
        if not r.started_at:
            continue
        end = datetime.fromisoformat(r.completed_at) if r.completed_at else datetime.now(timezone.utc)
        h = max((end - datetime.fromisoformat(r.started_at)).total_seconds() / 3600, MIN_PACE_HOURS)
        rd = await db.stops.count_documents({"route_id": r.id, "status": "delivered"})
        hours += h
        drops += rd
        dev = None
        # compare driven distance with the planned distance for the portion of the route reached so far
        reached = [s.model_dump() for s in await get_stops(r.id) if s.status != "pending"]
        planned_so_far = path_length_m(DEPOT, reached) if reached else 0
        if planned_so_far > 0 and r.actual_distance_m > 0:
            dev = round((r.actual_distance_m - planned_so_far) / planned_so_far * 100, 1)
            devs.append(abs(dev))
        per_route.append({"id": r.id, "name": r.name, "status": r.status, "drops": rd, "drops_per_hour": round(rd / h, 1),
                          "deviation_pct": dev, "planned_km": round(r.planned_distance_m / 1000, 2), "actual_km": round(r.actual_distance_m / 1000, 2)})
    return {
        "drops_per_hour": round(drops / hours, 1) if hours else 0,
        "avg_deviation_pct": round(sum(devs) / len(devs), 1) if devs else 0,
        "delivered": delivered, "failed": failed, "total_orders": total, "unassigned": unassigned,
        "success_rate": round(delivered / (delivered + failed) * 100, 1) if delivered + failed else 100,
        "geofence_arrival_pct": round(geofenced / arrivals * 100) if arrivals else 0,
        "active_routes": sum(1 for r in routes if r.status == "active"),
        "avg_rating": round(sum(rated) / len(rated), 1) if rated else None,
        "routes": per_route,
    }
