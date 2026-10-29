from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Request
from fastapi.responses import Response
import os
import io
import csv
import time
import asyncio
import httpx
import uuid
import re
import secrets
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, date as date_cls, time as time_cls
from core import RUNS, TZ, admin_user, check_merchant_access, current_user, merchant_terms_ok, db, haversine_m, issue_token, logger, merchant_access, now_iso, today_str
from models import Stop, merchant_pickup
from routes import billing
from routes import feedback
from routes import linkpages
from routes import privacy
from routes import scorecards
from routes import stops as stop_routes
from services import realtime
from services import seed

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- merchants & CSV order import
MERCHANTS = [{"id": "mer-1", "name": "Mission Bakery Co.", "contact": "orders@missionbakery.example"},
             {"id": "mer-2", "name": "Bay Books", "contact": "hello@baybooks.example"}]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = os.environ.get("NOMINATIM_USER_AGENT", "dashlocal-merchant-import/1.0 (ops@dashlocal.app)")
KW_VIEWBOX = "-80.66,43.56,-80.36,43.36"  # lookups are bounded to Kitchener-Waterloo
IMPORT_MAX_ROWS = 200
FLAGGED = ("invalid", "not_found", "lookup_failed")
COLUMNS = {
    "customer_name": {"customer_name", "name", "customer", "recipient", "full_name"},
    "phone": {"phone", "customer_phone", "phone_number", "mobile", "tel"},
    "address": {"address", "street", "street_address", "delivery_address", "address_line"},
    "notes": {"notes", "note", "instructions", "delivery_notes"},
    "window_date": {"window_date", "date", "delivery_date"},
    "window_start": {"window_start", "from", "start", "window_from"},
    "window_end": {"window_end", "to", "end", "window_to"},
    "priority": {"priority", "priority_delivery", "rush", "express"},
    "delivery_note": {"delivery_note", "gate_code", "customer_note", "dropoff_note", "door_note"},
    "matter_ref": {"matter_ref", "matter", "matter_number", "matter_no", "file_number", "file_no", "file", "client_matter", "reference"},
    "amount_due": {"amount_due", "amount", "customer_pays", "balance_due", "order_total", "cod"},
}


def parse_amount(v) -> Optional[float]:
    """Customer pays this in the app (before HST); '' = nothing to pay. Raises ValueError when not a sensible amount."""
    t = str(v or "").replace("$", "").replace(",", "").strip()
    if not t:
        return None
    a = round(float(t), 2)
    if not 1 <= a <= 10000:
        raise ValueError
    return a
TRUTHY = {"yes", "y", "true", "1", "x", "priority", "rush", "express"}
TEMPLATE_CSV = ("customer_name,phone,address,notes,window_date,window_start,window_end,priority,delivery_note,matter_ref\n"
                "Jane Doe,519 555 0123,200 King St W Kitchener,Leave at reception,,,,,,\n"
                "Sam Lee,+15195550188,75 King St S Waterloo,Ring twice,,14:00,16:00,yes,Gate code 2468,2026-0142\n")
MATTER_MAX = 60


def clean_matter(v) -> Optional[str]:
    return " ".join(str(v or "").split())[:MATTER_MAX] or None
_geo_lock = asyncio.Lock()
_geo_last = [0.0]


async def geocode(address: str) -> Optional[dict]:
    """OpenStreetMap Nominatim lookup (cached, max 1 request/second). Raises on network/HTTP errors."""
    key = re.sub(r"\s+", " ", address.strip().lower())
    cached = await db.geocodes.find_one({"key": key}, {"_id": 0})
    if cached:
        return cached["result"]
    async with _geo_lock:
        wait = 1.05 - (time.monotonic() - _geo_last[0])
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": NOMINATIM_UA}) as c:
                r = await c.get(NOMINATIM_URL, params={"q": address if re.search(r"kitchener|waterloo", address, re.I) else f"{address}, Kitchener, ON, Canada", "format": "jsonv2", "limit": 1,
                                                       "addressdetails": 1, "countrycodes": "ca", "viewbox": KW_VIEWBOX, "bounded": 1})
        finally:
            _geo_last[0] = time.monotonic()
    r.raise_for_status()
    rows = r.json()
    result = None
    if rows:
        a = rows[0].get("address", {})
        result = {"lat": float(rows[0]["lat"]), "lng": float(rows[0]["lon"]), "postcode": a.get("postcode"),
                  "display_name": rows[0].get("display_name")}
    await db.geocodes.update_one({"key": key}, {"$set": {"key": key, "result": result, "cached_at": now_iso()}}, upsert=True)
    return result


def normalize_phone(p: str) -> Optional[str]:
    p = p.strip()
    digits = re.sub(r"\D", "", p)
    if p.startswith("+") and 8 <= len(digits) <= 15:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def validate_row(d: dict) -> Optional[str]:
    """Returns an error message, or None when the row is usable. Normalises phone in place."""
    missing = [k.replace("customer_", "") for k in ("customer_name", "phone", "address") if not d.get(k)]
    if missing:
        return f"Missing {', '.join(missing)}"
    phone = normalize_phone(d["phone"])
    if not phone:
        return f"Invalid phone number '{d['phone'][:20]}'"
    d["phone"] = phone
    ws, we, wd = d.get("window_start"), d.get("window_end"), d.get("window_date")
    if bool(ws) != bool(we):
        return "Give both window_start and window_end, or neither"
    if ws and (not stop_routes.HHMM.match(ws) or not stop_routes.HHMM.match(we) or ws >= we):
        return "Window must be HH:MM to HH:MM, start before end"
    if wd:
        if not seed.ISO_DATE.match(wd):
            return "window_date must be YYYY-MM-DD"
        if wd < today_str():
            return "window_date is in the past"
    try:
        parse_amount(d.get("amount_due"))
    except ValueError:
        return "amount_due must be a dollar amount between 1.00 and 10000.00"
    return None


async def new_tracking_code() -> str:
    while True:
        code = f"MC{secrets.randbelow(90000) + 10000}"
        if not await db.stops.find_one({"tracking_code": code}, {"_id": 1}):
            return code


async def process_row(row: dict) -> dict:
    d = row["data"]
    upd = {"error": None, "stop_id": None, "tracking_code": None}
    err = validate_row(d)
    if err:
        upd.update(status="invalid", error=err)
    else:
        try:
            geo = await geocode(d["address"])
        except Exception as e:
            logger.error(f"Geocode failed for import row {row['id']}: {e}")
            geo, upd["status"], upd["error"] = None, "lookup_failed", "Address lookup is unavailable right now — retry shortly"
        if geo is None and "status" not in upd:
            upd.update(status="not_found", error="Address not found in Kitchener-Waterloo — check the street and number")
        elif geo:
            zone = min(seed.ZONES, key=lambda z: haversine_m({"lat": geo["lat"], "lng": geo["lng"]}, {"lat": seed.ZONES[z]["center"][0], "lng": seed.ZONES[z]["center"][1]}))
            stop = Stop(id=str(uuid.uuid4()), customer_name=d["customer_name"], customer_phone=d["phone"],
                        address=d["address"] if re.search(r"kitchener|waterloo", d["address"], re.I) else f"{d['address']}, Kitchener",
                        postal_code=geo.get("postcode") or seed.ZONES[zone]["postal"], zone=zone, lat=geo["lat"], lng=geo["lng"],
                        tracking_code=await new_tracking_code(), notes=d.get("notes") or "",
                        window_start=d.get("window_start") or None, window_end=d.get("window_end") or None,
                        window_date=d.get("window_date") or None, merchant_id=row["merchant_id"], created_at=now_iso(),
                        priority=(d.get("priority") or "").strip().lower() in TRUTHY, matter_ref=clean_matter(d.get("matter_ref")),
                        amount_due=parse_amount(d.get("amount_due")))
            if row.get("run") in RUNS:  # pharmacy batch: the run sets the day and the patient delivery window
                r = RUNS[row["run"]]
                stop.window_start, stop.window_end, stop.window_date = r["window"][0], r["window"][1], row["manifest_id"].split(":")[1]
                stop.batch_run, stop.manifest_id = row["run"], row["manifest_id"]
            for k, v in (row.get("consent") or {}).items():
                setattr(stop, k, v)
            stop.fee = billing.compute_fee(stop.model_dump(), await billing.get_pricing(row["merchant_id"]))
            note, source = await stop_routes.remembered_note(row["merchant_id"], stop.customer_phone)
            csv_note = " ".join((d.get("delivery_note") or "").split())[:200]
            if csv_note:  # a delivery_note column also updates the store's saved note for this customer
                key = {"merchant_id": row["merchant_id"], "phone": stop.customer_phone}
                await db.saved_notes.update_one(key, {"$set": {"note": csv_note, "updated_at": now_iso()},
                                                      "$setOnInsert": {**key, "id": str(uuid.uuid4()), "created_at": now_iso()}}, upsert=True)
                note, source = csv_note, "saved"
            if note:  # regular customer: prefill their delivery note (they can still change it)
                stop.customer_note, stop.customer_note_source, stop.customer_note_at = note, source, now_iso()
            await db.stops.insert_one(stop.model_dump())
            upd.update(status="created", stop_id=stop.id, tracking_code=stop.tracking_code)
    await db.import_rows.update_one({"id": row["id"]}, {"$set": upd})
    return {**row, **upd}


async def refresh_job(job_id: str, finished: bool = False):
    rows = await db.import_rows.find({"job_id": job_id}, {"_id": 0, "status": 1}).to_list(IMPORT_MAX_ROWS + 1)
    upd = {"processed": sum(1 for r in rows if r["status"] != "queued"),
           "created": sum(1 for r in rows if r["status"] == "created"),
           "flagged": sum(1 for r in rows if r["status"] in FLAGGED)}
    if finished:
        upd.update(status="done", finished_at=now_iso())
    await db.import_jobs.update_one({"id": job_id}, {"$set": upd})


async def process_import(job_id: str):
    try:
        for row in await db.import_rows.find({"job_id": job_id, "status": "queued"}, {"_id": 0}).sort("line", 1).to_list(IMPORT_MAX_ROWS + 1):
            await process_row(row)
            await refresh_job(job_id)
    except Exception as e:
        logger.error(f"Import {job_id} crashed: {e}")
    await refresh_job(job_id, finished=True)


async def get_merchant_or_404(merchant_id: str) -> dict:
    m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0})
    if not m:
        raise HTTPException(404, "Merchant not found")
    return m


# ---------------------------------------------------------------- merchant delivery report (CSV)
REPORT_RANGES = {"today": 0, "7d": 6, "30d": 29, "all": None, "lastweek": "lastweek"}
REPORT_COLUMNS = ["tracking_code", "customer_name", "customer_phone", "address", "zone", "status", "order_created", "delivery_day",
                  "window", "driver", "arrived_at", "delivered_at", "on_time", "fail_reason", "proof_types", "proof_link",
                  "rating", "rating_comment"]


class ReportIn(BaseModel):
    range: str = "7d"


@router.post("/merchants/{merchant_id}/report-link")
async def report_link(merchant_id: str, body: ReportIn, request: Request, u: dict = Depends(merchant_access)):
    """Short-lived download link (5 min) so the CSV can open in a browser tab without a sign-in header."""
    await get_merchant_or_404(merchant_id)
    if body.range not in REPORT_RANGES:
        raise HTTPException(400, f"range must be one of {', '.join(REPORT_RANGES)}")
    tok = issue_token(u["id"], u["role"], purpose="report", minutes=5, mid=merchant_id, rng=body.range)
    await remember_public_base(request)
    return {"path": f"/api/merchants/{merchant_id}/report.csv?token={tok}", "expires_in": 300}


def local_fmt(iso: Optional[str]) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y-%m-%d %H:%M") if iso else ""


def report_window(rng: Optional[str]):
    """(since, until) depot-local YYYY-MM-DD bounds for a report range; None = open-ended."""
    today = datetime.now(TZ).date()
    if rng == "lastweek":  # previous Monday..Sunday
        start = today - timedelta(days=today.weekday() + 7)
        return start.isoformat(), (start + timedelta(days=6)).isoformat()
    back = REPORT_RANGES.get(rng, 6)
    return ((today - timedelta(days=back)).isoformat() if back is not None else None), None


def trusted_bases() -> set:
    return {o.strip().rstrip("/").lower() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()}


async def remember_public_base(request: Request):
    """Scheduled emails have no request to read the app's public URL from, so keep the latest one.
    Only the app's own addresses (CORS_ORIGINS) are accepted, so a forged Host header can't redirect emailed links."""
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host", "")).split(",")[0].strip().lower()
    base = f"https://{host}"
    if host and base in trusted_bases():
        await db.settings.update_one({"key": "public_base"}, {"$set": {"key": "public_base", "value": base}}, upsert=True)


def csv_safe(v) -> str:
    """Stop spreadsheet formula injection from customer-written text."""
    v = "" if v is None else str(v)
    return f"'{v}" if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@router.get("/merchants/{merchant_id}/report.csv")
async def report_csv(merchant_id: str, request: Request, token: str = ""):
    p = linkpages.download_payload(token, "report")
    if p.get("mid") != merchant_id:
        raise linkpages.LinkPageError("report", "wrong", 403)
    m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0})
    if not m:
        raise linkpages.LinkPageError("report", "missing", 404)
    since, until = report_window(p.get("rng"))
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto") or ("http" if host.startswith(("localhost", "127.")) else "https")
    base = f"{proto}://{host}"
    if base.lower() not in trusted_bases():  # forged host header: fall back to the app's known address
        base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value") or ""
    routes = {r["id"]: r for r in await db.routes.find({}, {"_id": 0, "id": 1, "driver_id": 1, "service_date": 1}).to_list(2000)}
    drivers = {d["id"]: d["name"] for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(REPORT_COLUMNS)
    for d in await db.stops.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", 1).to_list(5000):
        st = Stop(**d)
        activity = st.delivered_at or st.created_at
        unfinished = st.status in ("pending", "arrived")  # open orders always appear, whatever the range
        act_day = datetime.fromisoformat(activity).astimezone(TZ).date().isoformat() if activity else None
        if since and not unfinished and (not act_day or act_day < since or (until and act_day > until)):
            continue
        r = routes.get(st.route_id) if st.route_id else None
        link = ""
        if st.status == "delivered":
            link = base + (await feedback.share_proof(st.id))["path"]
        on_time = scorecards.delivered_on_time(st)
        proofs = [k for k, v in (("photo", st.pod_photo_path), ("signature", st.pod_signature), ("qr", st.pod_qr_verified)) if v]
        status = st.status if st.route_id else ("scheduled" if st.window_date and st.window_date > today_str() else "awaiting dispatch")
        w.writerow([st.tracking_code, csv_safe(st.customer_name), st.customer_phone, csv_safe(st.address), st.zone, status, local_fmt(st.created_at),
                    (r or {}).get("service_date") or st.window_date or "", f"{st.window_start}-{st.window_end}" if st.window_end else "",
                    drivers.get((r or {}).get("driver_id"), ""), local_fmt(st.arrived_at), local_fmt(st.delivered_at),
                    "" if on_time is None else ("yes" if on_time else "no"), csv_safe(st.fail_reason), "+".join(proofs), link,
                    st.rating or "", csv_safe(st.rating_comment)])
    slug = re.sub(r"[^a-z0-9]+", "-", m["name"].lower()).strip("-")
    fname = f"{slug}-deliveries-{p.get('rng')}-{today_str()}.csv"
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/merchant-template.csv")
async def merchant_template():
    return Response(TEMPLATE_CSV, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="dashlocal-orders-template.csv"'})


@router.get("/merchants")
async def list_merchants(_: dict = Depends(admin_user)):
    out = []
    for m in await db.merchants.find({}, {"_id": 0}).sort("id", 1).to_list(100):
        out.append({**m, "orders": await db.stops.count_documents({"merchant_id": m["id"]}),
                    "delivered": await db.stops.count_documents({"merchant_id": m["id"], "status": "delivered"}),
                    "flagged": await db.import_rows.count_documents({"merchant_id": m["id"], "status": {"$in": list(FLAGGED)}})})
    return out


@router.get("/merchants/{merchant_id}")
async def merchant_detail(merchant_id: str, _: dict = Depends(merchant_access)):
    m = await get_merchant_or_404(merchant_id)
    orders = await db.stops.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).to_list(500)
    flagged = await db.import_rows.find({"merchant_id": merchant_id, "status": {"$in": list(FLAGGED)}}, {"_id": 0}).sort([("created_at", -1), ("line", 1)]).to_list(500)
    jobs = await db.import_jobs.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).to_list(5)
    if m.get("pharmacy_partner"):
        runs = billing.runs_today()
        for r in runs:
            r["drops_today"] = await db.stops.count_documents({"merchant_id": merchant_id, "manifest_id": f"{merchant_id}:{r['date']}:{r['key']}"})
            r["cutoff_at"] = datetime.combine(date_cls.fromisoformat(r["date"]), time_cls(*map(int, r["cutoff"].split(":"))), TZ).isoformat()
            r["pickup"] = await merchant_pickup(merchant_id, r)
        recent = await db.pickups.find({"merchant_id": merchant_id}, {"_id": 0}).sort("picked_up_at", -1).to_list(5)
        m = {**m, "runs": runs, "pharmacy_terms": billing.PHARMACY, "recent_pickups": recent}
    return {**m, "orders": [Stop(**o).model_dump() for o in orders], "flagged": flagged, "jobs": jobs,
            "counts": {"orders": len(orders), "pending": sum(1 for o in orders if o["status"] in ("pending", "arrived")),
                       "delivered": sum(1 for o in orders if o["status"] == "delivered"), "failed": sum(1 for o in orders if o["status"] == "failed"),
                       "flagged": len(flagged)}}


class SavedNoteIn(BaseModel):
    phone: str
    note: str


@router.get("/merchants/{merchant_id}/saved-notes")
async def list_saved_notes(merchant_id: str, _: dict = Depends(merchant_access)):
    return await db.saved_notes.find({"merchant_id": merchant_id, "erased_at": {"$exists": False}}, {"_id": 0}).sort("updated_at", -1).to_list(1000)


@router.put("/merchants/{merchant_id}/saved-notes")
async def upsert_saved_note(merchant_id: str, body: SavedNoteIn, _: dict = Depends(merchant_access)):
    """Save (or replace) a regular customer's delivery note, keyed by phone. Filled into every new order for them."""
    await get_merchant_or_404(merchant_id)
    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Enter a valid phone number")
    note = " ".join(body.note.split())[:200]
    if not note:
        raise HTTPException(400, "Write the note to save")
    key = {"merchant_id": merchant_id, "phone": phone}
    await db.saved_notes.update_one(key, {"$set": {"note": note, "updated_at": now_iso()},
                                          "$setOnInsert": {**key, "id": str(uuid.uuid4()), "created_at": now_iso()}}, upsert=True)
    return await db.saved_notes.find_one(key, {"_id": 0})


@router.delete("/merchants/{merchant_id}/saved-notes/{note_id}")
async def delete_saved_note(merchant_id: str, note_id: str, _: dict = Depends(merchant_access)):
    res = await db.saved_notes.delete_one({"merchant_id": merchant_id, "id": note_id})
    if not res.deleted_count:
        raise HTTPException(404, "Saved note not found")
    return {"ok": True}


@router.post("/merchants/{merchant_id}/import")
async def import_orders(merchant_id: str, request: Request, file: UploadFile = File(...), run: Optional[str] = None, consent: bool = False,
                        u: dict = Depends(merchant_access)):
    """Upload a CSV of orders. Rows are validated now and geocoded in the background (about 1 per second).
    Pharmacy partners pick a pickup run (afternoon | evening); uploads close at that run's cut-off.
    `consent=true` confirms the privacy consent statement; it is stored with every order (PIPEDA / PHIPA)."""
    if not consent:
        raise HTTPException(400, "Confirm the privacy consent statement before uploading orders")
    consent_rec = {"consent_given": True, "consent_at": now_iso(), "consent_by": u.get("email") or u["id"], "consent_text": privacy.CONSENT_TEXT}
    m = await get_merchant_or_404(merchant_id)
    manifest_id = None
    if m.get("pharmacy_partner"):
        if run not in RUNS:
            raise HTTPException(400, "Pick a pickup run: afternoon or evening")
        info = next(r for r in billing.runs_today() if r["key"] == run)
        manifest_id = f"{merchant_id}:{info['date']}:{run}"
    else:
        run = None
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "CSV is larger than 2 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(400, "Could not read the file — save it as CSV (UTF-8)")
    try:
        reader = csv.DictReader(io.StringIO(text))
        headers = {h: re.sub(r"[^a-z_]", "", (h or "").strip().lower().replace(" ", "_")) for h in (reader.fieldnames or [])}
        mapping = {}
        for h, norm in headers.items():
            for k, aliases in COLUMNS.items():
                if norm in aliases and k not in mapping.values():
                    mapping[h] = k
        need = [k for k in ("customer_name", "phone", "address") if k not in mapping.values()]
        if need:
            raise HTTPException(400, f"CSV needs columns: customer_name, phone, address (missing {', '.join(need)}). Download the template for the format.")
        rows = []
        for r in reader:
            d = {k: "" for k in COLUMNS}
            for h, k in mapping.items():
                d[k] = (r.get(h) or "").strip()
            if any(d.values()):
                rows.append(d)
    except csv.Error as e:
        raise HTTPException(400, f"Could not read the CSV: {e}")
    if not rows:
        raise HTTPException(400, "The CSV has no order rows")
    if len(rows) > IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Up to {IMPORT_MAX_ROWS} orders per file — split it into smaller files")
    job = {"id": str(uuid.uuid4()), "merchant_id": merchant_id, "filename": file.filename or "orders.csv", "total": len(rows),
           "processed": 0, "created": 0, "flagged": 0, "status": "running", "created_at": now_iso(), "finished_at": None,
           "run": run, "manifest_id": manifest_id}
    await db.import_jobs.insert_one(dict(job))
    await db.import_rows.insert_many([{"id": str(uuid.uuid4()), "job_id": job["id"], "merchant_id": merchant_id, "line": i + 2, "data": d,
                                       "run": run, "manifest_id": manifest_id, "consent": consent_rec,
                                       "status": "queued", "error": None, "stop_id": None, "tracking_code": None, "created_at": job["created_at"]}
                                      for i, d in enumerate(rows)])
    realtime.spawn(process_import(job["id"]))
    return job


@router.get("/imports/{job_id}")
async def import_status(job_id: str, u: dict = Depends(current_user)):
    job = await db.import_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Import not found")
    check_merchant_access(u, job["merchant_id"])
    return job


class RowFixIn(BaseModel):
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    window_date: Optional[str] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    priority: Optional[str] = None
    matter_ref: Optional[str] = None
    amount_due: Optional[str] = None


class MatterIn(BaseModel):
    matter_ref: Optional[str] = None


@router.put("/merchants/{merchant_id}/stops/{stop_id}/matter")
async def set_matter(merchant_id: str, stop_id: str, body: MatterIn, _: dict = Depends(merchant_access)):
    """Tag (or clear) the client file / matter number on one of the store's deliveries."""
    ref = clean_matter(body.matter_ref)
    res = await db.stops.update_one({"id": stop_id, "merchant_id": merchant_id}, {"$set": {"matter_ref": ref}})
    if not res.matched_count:
        raise HTTPException(404, "Delivery not found")
    return {"ok": True, "matter_ref": ref}


@router.put("/import-rows/{row_id}")
async def fix_import_row(row_id: str, body: RowFixIn, request: Request, u: dict = Depends(current_user)):
    """Edit a flagged row and try it again straight away."""
    row = await db.import_rows.find_one({"id": row_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Row not found")
    check_merchant_access(u, row["merchant_id"])
    merchant_terms_ok(u, request)
    if row["status"] not in FLAGGED:
        raise HTTPException(400, f"Row is already {row['status']}")
    data = {**row["data"], **{k: v.strip() for k, v in body.model_dump().items() if v is not None}}
    await db.import_rows.update_one({"id": row_id}, {"$set": {"data": data}})
    out = await process_row({**row, "data": data})
    await refresh_job(row["job_id"], finished=(await db.import_jobs.find_one({"id": row["job_id"]}, {"_id": 0, "status": 1}))["status"] == "done")
    return out


@router.delete("/import-rows/{row_id}")
async def dismiss_import_row(row_id: str, request: Request, u: dict = Depends(current_user)):
    row = await db.import_rows.find_one({"id": row_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Row not found")
    check_merchant_access(u, row["merchant_id"])
    merchant_terms_ok(u, request)
    if row["status"] not in FLAGGED:
        raise HTTPException(400, f"Row is already {row['status']}")
    await db.import_rows.update_one({"id": row_id}, {"$set": {"status": "dismissed"}})
    await refresh_job(row["job_id"])
    return {"ok": True}
