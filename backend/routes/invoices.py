from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from fastapi.concurrency import run_in_threadpool
from pymongo.errors import DuplicateKeyError
from html import escape
import asyncio
import uuid
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, time as time_cls
from core import ADMIN_ONLY, RUNS, TZ, admin_user, db, hhmm12, issue_token, logger, merchant_access, now_iso
from routes import auth
from routes import billing
from routes import emails
from routes import merchants as merchant_routes
from routes import linkpages
from routes import onboarding
from routes import pickups as pickup_routes
from services import realtime

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- invoices with HST (one per completed delivery, CAD)
BUSINESS_DEFAULT = {"name": "DashLocal", "address": "Business address — set in Admin › Invoices", "hst_number": "HST # — set in Admin › Invoices", "hst_pct": 13.0}


async def get_business() -> dict:
    doc = await db.settings.find_one({"key": "business"}, {"_id": 0}) or {}
    return {**BUSINESS_DEFAULT, **(doc.get("value") or {})}


async def next_invoice_number() -> str:
    c = await db.counters.find_one_and_update({"key": "invoice"}, {"$inc": {"n": 1}}, upsert=True, return_document=True)
    return f"DL-{c['n']:06d}"


async def create_invoice(stop_id: str) -> Optional[dict]:
    """Idempotent: one invoice per delivered merchant stop. Snapshots business + merchant details at issue time."""
    s = await db.stops.find_one({"id": stop_id}, {"_id": 0})
    if not s or s.get("status") != "delivered" or not s.get("merchant_id"):
        return None
    if existing := await db.invoices.find_one({"stop_id": stop_id}, {"_id": 0}):
        return existing
    m = await db.merchants.find_one({"id": s["merchant_id"]}, {"_id": 0}) or {}
    biz = await get_business()
    fee = s.get("fee") or billing.compute_fee(s, await billing.get_pricing(s["merchant_id"]))
    lines = [{"description": "Same-day delivery fee", "amount": fee["base"]}]
    if fee.get("distance_fee"):
        lines.append({"description": f"Distance ({fee['km']} km)", "amount": fee["distance_fee"]})
    if fee.get("priority_fee"):
        lines.append({"description": "Priority surcharge", "amount": fee["priority_fee"]})
    subtotal = round(sum(x["amount"] for x in lines), 2)
    hst = round(subtotal * biz["hst_pct"] / 100, 2)
    delivered = datetime.fromisoformat(s.get("delivered_at") or now_iso()).astimezone(TZ)
    inv = {"id": str(uuid.uuid4()), "number": None, "merchant_id": s["merchant_id"], "stop_id": stop_id,
           "date": delivered.date().isoformat(), "issued_at": now_iso(), "currency": "CAD",
           "business": biz, "merchant": {"name": m.get("name", ""), "address": m.get("address")},
           "delivery": {"tracking_code": s["tracking_code"], "customer_name": s["customer_name"], "address": s["address"],
                        "delivered_at": s.get("delivered_at"), "delivered_local": delivered.strftime("%Y-%m-%d %H:%M"), "km": fee.get("km"), "priority": bool(s.get("priority"))},
           "lines": lines, "subtotal": subtotal, "hst_pct": biz["hst_pct"], "hst": hst, "total": round(subtotal + hst, 2),
           "status": "unpaid", "paid_at": None, "paid_note": None}
    try:
        inv["number"] = await next_invoice_number()
        await db.invoices.insert_one(dict(inv))
    except DuplicateKeyError:  # a parallel call already issued it
        return await db.invoices.find_one({"stop_id": stop_id}, {"_id": 0})
    if m.get("pharmacy_partner"):
        await apply_manifest_rate(s["merchant_id"], s.get("manifest_id"))
    else:
        await apply_volume_tier(s["merchant_id"], inv["date"])
    return await db.invoices.find_one({"id": inv["id"]}, {"_id": 0})


async def open_session_invoice_ids(merchant_id: str) -> set:
    """Invoices inside a Square card charge being processed right now keep their price."""
    return {i for c in await db.square_charges.find({"merchant_id": merchant_id, "status": "processing"}, {"_id": 0, "invoice_ids": 1}).to_list(100)
            for i in c.get("invoice_ids", [])}


async def rerate_invoice(inv: dict, rate: float, desc: str, extra: dict):
    lines = [dict(x) for x in inv["lines"]]
    if not lines or (lines[0]["amount"] == rate and lines[0]["description"] == desc):
        return
    lines[0] = {"description": desc, "amount": rate}
    sub = round(sum(x["amount"] for x in lines), 2)
    hst = round(sub * inv["hst_pct"] / 100, 2)
    await db.invoices.update_one({"id": inv["id"], "status": "unpaid"}, {"$set": {
        "lines": lines, "subtotal": sub, "hst": hst, "total": round(sub + hst, 2), **extra}})


async def apply_manifest_rate(merchant_id: str, manifest_id: Optional[str]):
    """Pharmacy contract: $6.25/drop when the pickup manifest holds 10+ drops, else the $8.99 standard rate.
    Orders uploaded without a run (no manifest) are billed at the standard rate."""
    if not manifest_id:
        return
    stop_ids = [x["id"] for x in await db.stops.find({"merchant_id": merchant_id, "manifest_id": manifest_id}, {"_id": 0, "id": 1}).to_list(1000)]
    n = len(stop_ids)
    batch = n >= billing.PHARMACY["min_drops"]
    rate = billing.PHARMACY["batch_rate"] if batch else billing.PHARMACY["standard_rate"]
    desc = (f"Pharmacy batch rate ({n} drops on this pickup)" if batch
            else f"Standard rate — pickup had {n} drop{'s' if n != 1 else ''}, {billing.PHARMACY['min_drops']} needed for batch rate")
    locked = await open_session_invoice_ids(merchant_id)
    for inv in await db.invoices.find({"merchant_id": merchant_id, "stop_id": {"$in": stop_ids}, "status": "unpaid"}, {"_id": 0}).to_list(1000):
        if inv["id"] not in locked:
            await rerate_invoice(inv, rate, desc, {"manifest_drops": n, "tier": "Pharmacy batch" if batch else "Standard"})


async def ensure_platform_invoices():
    """Monthly platform fee (same for every store, set in Admin › Pricing), once per calendar month (idempotent via the unique stop_id key)."""
    month = datetime.now(TZ).strftime("%Y-%m")
    biz = await get_business()
    sub = round((await billing.get_pricing())["monthly_fee"], 2)
    if sub <= 0:
        return
    for m in await db.merchants.find({"disabled": {"$ne": True}}, {"_id": 0}).to_list(1000):
        key = f"platform:{m['id']}:{month}"
        if await db.invoices.find_one({"stop_id": key}, {"_id": 1}):
            continue
        hst = round(sub * biz["hst_pct"] / 100, 2)
        label = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
        inv = {"id": str(uuid.uuid4()), "number": None, "kind": "platform", "merchant_id": m["id"], "stop_id": key,
               "date": datetime.now(TZ).date().isoformat(), "issued_at": now_iso(), "currency": "CAD", "business": biz,
               "merchant": {"name": m.get("name", ""), "address": m.get("address")},
               "delivery": {"tracking_code": "PLATFORM", "customer_name": f"Monthly platform fee — {label}", "address": "Merchant Portal, tracking, invoicing & dispatch",
                            "delivered_at": None, "delivered_local": None, "km": None, "priority": False},
               "lines": [{"description": f"Monthly platform fee — {label}", "amount": sub}], "subtotal": sub, "hst_pct": biz["hst_pct"],
               "hst": hst, "total": round(sub + hst, 2), "status": "unpaid", "paid_at": None, "paid_note": None}
        try:
            inv["number"] = await next_invoice_number()
            await db.invoices.insert_one(inv)
        except DuplicateKeyError:
            pass


async def batch_progress_watcher():
    """Pharmacy pickups 1-3 drops short of the batch rate get one email 30 min before the run's cut-off."""
    await asyncio.sleep(25)
    while True:
        try:
            await send_batch_progress_alerts()
            await pickup_routes.send_missed_pickup_alerts()
        except Exception as e:
            logger.warning(f"Batch progress loop error: {e}")
        await asyncio.sleep(60)


BATCH_ALERT_LEAD_MIN = 30
BATCH_ALERT_MAX_SHORT = 3


async def send_batch_progress_alerts(now: Optional[datetime] = None) -> list:
    now = now or datetime.now(TZ)
    sent = []
    for key, r in RUNS.items():
        cut = datetime.combine(now.date(), time_cls(*map(int, r["cutoff"].split(":"))), TZ)
        if not (cut - timedelta(minutes=BATCH_ALERT_LEAD_MIN) <= now < cut):
            continue
        for m in await db.merchants.find({"pharmacy_partner": True}, {"_id": 0}).to_list(500):
            manifest = f"{m['id']}:{now.date().isoformat()}:{key}"
            n = await db.stops.count_documents({"manifest_id": manifest})
            short = billing.PHARMACY["min_drops"] - n
            if n < 1 or not (1 <= short <= BATCH_ALERT_MAX_SHORT):
                continue
            try:
                await db.batch_alerts.insert_one({"id": manifest, "merchant_id": m["id"], "run": key, "drops": n, "created_at": now_iso(), "recipients": []})
            except DuplicateKeyError:
                continue  # already sent for this pickup
            realtime.spawn(email_batch_progress(m, r, n, short, manifest))
            sent.append(manifest)
    return sent


async def email_batch_progress(m: dict, r: dict, n: int, short: int, manifest: str):
    saving = round((billing.PHARMACY["standard_rate"] - billing.PHARMACY["batch_rate"]) * billing.PHARMACY["min_drops"], 2)
    html = onboarding.brand_email(f"{short} more drop{'s' if short > 1 else ''} for the batch rate",
                       f"<p>Your <b>{r['label']}</b> pickup today has <b>{n} drop{'s' if n != 1 else ''}</b>. "
                       f"Add <b>{short} more</b> before the <b>{hhmm12(r['cutoff'])}</b> cut-off and every drop on this pickup is billed at "
                       f"<b>${billing.PHARMACY['batch_rate']:.2f}</b> instead of ${billing.PHARMACY['standard_rate']:.2f} — at least ${saving:.2f} saved.</p>"
                       f"<p>Upload more orders in the merchant portal and pick the {r['label']}.</p>")
    demo = {em for _, em, _ in auth.DEMO_MERCHANT_USERS}
    recips = []
    for u in await db.users.find({"merchant_id": m["id"], "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0, "email": 1}).to_list(20):
        to = emails.TEST_INBOX if u["email"] in demo else u["email"]
        try:
            await emails.send_email(to=to, subject=f"{m['name']}: {short} drop{'s' if short > 1 else ''} short of the batch rate — {r['label']}", html=html)
            recips.append({"login": u["email"], "to": to, "status": "sent"})
        except Exception as e:
            recips.append({"login": u["email"], "to": to, "status": f"failed: {e}"})
    await db.batch_alerts.update_one({"id": manifest}, {"$set": {"recipients": recips}})


@router.post("/admin/batch-alerts/run", dependencies=ADMIN_ONLY)
async def run_batch_alerts(at: Optional[str] = None):
    """Run the batch-progress check now; `at` (ISO, Eastern) pretends it is that time — for testing."""
    try:
        now = datetime.fromisoformat(at).astimezone(TZ) if at and ("+" in at[10:] or at.endswith("Z")) else (datetime.fromisoformat(at).replace(tzinfo=TZ) if at else None)
    except ValueError:
        raise HTTPException(400, "at must be an ISO date-time, e.g. 2026-06-01T13:10:00")
    return {"sent": await send_batch_progress_alerts(now)}


async def platform_fee_watcher():
    await asyncio.sleep(20)
    while True:
        try:
            await ensure_platform_invoices()
        except Exception as e:
            logger.warning(f"Platform fee loop error: {e}")
        await asyncio.sleep(600)


async def apply_volume_tier(merchant_id: str, day: str):
    """Re-rate the shop's still-open invoices for `day` to the tier its delivered-drop count has reached.
    Count only grows, so rates only go down. Paid / reported / in-checkout invoices keep their price.
    Stores with a custom per-delivery fee keep that flat fee (no tiers)."""
    m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0, "pricing_override": 1}) or {}
    if "base_fee" in (m.get("pricing_override") or {}):
        return
    p = await billing.get_pricing(merchant_id)
    q = {"merchant_id": merchant_id, "date": day, "kind": {"$ne": "platform"}}
    count = await db.invoices.count_documents(q)
    tier = billing.volume_tier(count, p["base_fee"])
    locked = await open_session_invoice_ids(merchant_id)
    for inv in await db.invoices.find({**q, "status": "unpaid"}, {"_id": 0}).to_list(500):
        if inv["id"] not in locked:
            await rerate_invoice(inv, tier["rate"], f"Same-day delivery fee — {tier['name']} ({count} drops that day)",
                                 {"tier": tier["name"], "day_drops": count})


async def backfill_invoices():
    await db.invoices.create_index("stop_id", unique=True)
    await db.invoices.create_index("number", unique=True)
    for s in await db.stops.find({"status": "delivered", "merchant_id": {"$ne": None}}, {"_id": 0, "id": 1}).to_list(5000):
        await create_invoice(s["id"])


def invoice_summary(invs: list) -> dict:
    out = round(sum(i["total"] for i in invs if i["status"] in ("unpaid", "reported")), 2)
    paid = round(sum(i["total"] for i in invs if i["status"] == "paid"), 2)
    return {"outstanding": out, "paid": paid, "unpaid_count": sum(i["status"] in ("unpaid", "reported") for i in invs),
            "reported_count": sum(i["status"] == "reported" for i in invs), "count": len(invs)}


@router.get("/merchants/{merchant_id}/invoices")
async def merchant_invoices(merchant_id: str, status: Optional[str] = None, _: dict = Depends(merchant_access)):
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    invs = await db.invoices.find({"merchant_id": merchant_id}, {"_id": 0}).sort("number", -1).to_list(2000)
    # "unpaid" = everything still owed (including payments the shop reported but dispatch hasn't confirmed)
    shown = [i for i in invs if not status or i["status"] == status or (status == "unpaid" and i["status"] == "reported")]
    out = {**invoice_summary(invs), "invoices": shown[:200], "billing_address": m.get("address"), "currency": "CAD"}
    if m.get("pharmacy_partner"):  # savings badge: what the batch rate saved this month vs the standard rate
        month = datetime.now(TZ).strftime("%Y-%m")
        n = sum(1 for i in invs if i.get("tier") == "Pharmacy batch" and i["date"].startswith(month))
        out["batch_savings"] = {"month": month, "label": datetime.now(TZ).strftime("%B %Y"), "drops": n,
                                "amount": round(n * (billing.PHARMACY["standard_rate"] - billing.PHARMACY["batch_rate"]), 2)}
    return out


class AddressIn(BaseModel):
    address: str


@router.put("/merchants/{merchant_id}/billing-address")
async def set_billing_address(merchant_id: str, body: AddressIn, _: dict = Depends(merchant_access)):
    """Address printed on new invoices (issued invoices keep the address they were issued with)."""
    await merchant_routes.get_merchant_or_404(merchant_id)
    addr = " ".join(body.address.split())[:200]
    if not addr:
        raise HTTPException(400, "Enter the billing address")
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"address": addr}})
    await db.invoices.update_many({"merchant_id": merchant_id, "merchant.address": None}, {"$set": {"merchant.address": addr}})
    return {"billing_address": addr}


@router.post("/merchants/{merchant_id}/invoices/{invoice_id}/link")
async def invoice_link(merchant_id: str, invoice_id: str, u: dict = Depends(merchant_access)):
    if not await db.invoices.find_one({"id": invoice_id, "merchant_id": merchant_id}, {"_id": 1}):
        raise HTTPException(404, "Invoice not found")
    tok = issue_token(invoice_id, u["role"], purpose="invoice", minutes=5)
    return {"path": f"/api/invoices/{invoice_id}.pdf?token={tok}"}


def invoice_pdf(inv: dict) -> bytes:
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors as rl
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=f"Invoice {inv['number']}", author=inv["business"]["name"])
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=10, leading=14, textColor=rl.HexColor("#333335"))
    small = ParagraphStyle("s", parent=body, fontSize=8.5, textColor=rl.HexColor("#5C5C5E"))
    h = ParagraphStyle("h", parent=body, fontName="Helvetica-Bold", fontSize=22, leading=26)
    e = lambda v: escape(str(v or ""))
    money = lambda n: f"CA${n:,.2f}"
    biz, mer, d = inv["business"], inv["merchant"], inv["delivery"]
    logo = Paragraph('<font name="Helvetica-Bold" size="18" color="#D7262E">DASH</font><font name="Helvetica-Bold" size="18" color="#333335">LOCAL</font>', body)
    head = Table([[logo, Paragraph("INVOICE", ParagraphStyle("r", parent=h, alignment=2))]], colWidths=[3.45 * inch, 3.45 * inch])
    meta = Table([
        [Paragraph(f"<b>{e(biz['name'])}</b><br/>{e(biz['address'])}<br/>HST Reg. No. {e(biz['hst_number'])}", body),
         Paragraph(f"<b>Invoice #</b> {e(inv['number'])}<br/><b>Date</b> {e(inv['date'])}<br/><b>Status</b> {e(inv['status'].upper())}"
                   + (f"<br/><b>Paid</b> {e(inv['paid_at'][:10])}" if inv.get("paid_at") else ""), ParagraphStyle("m", parent=body, alignment=2))],
    ], colWidths=[3.45 * inch, 3.45 * inch])
    bill_to = Paragraph(f"<b>BILL TO</b><br/>{e(mer['name'])}<br/>{e(mer.get('address') or 'Address not on file')}", body)
    deliv = Paragraph(f"<b>SERVICE</b> {e(d['customer_name'])} · {e(d['address'])}", body) if inv.get("kind") == "platform" else \
        Paragraph(f"<b>DELIVERY</b> {e(d['tracking_code'])} · {e(d['customer_name'])} · {e(d['address'])}<br/>"
                  f"Delivered {e(d.get('delivered_local') or (d.get('delivered_at') or '')[:16].replace('T', ' '))}{' · Priority' if d.get('priority') else ''}", body)
    rows = [["Description", "Amount"]] + [[x["description"], money(x["amount"])] for x in inv["lines"]] + [
        ["Subtotal", money(inv["subtotal"])], [f"HST ({inv['hst_pct']:g}%, Ontario)", money(inv["hst"])], ["Total owing (CAD)", money(inv["total"])]]
    n = len(inv["lines"])
    t = Table(rows, colWidths=[5.2 * inch, 1.7 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 10), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
        ("BACKGROUND", (0, 0), (-1, 0), rl.HexColor("#F5F5F5")), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, rl.HexColor("#333335")), ("LINEABOVE", (0, n + 1), (-1, n + 1), 0.5, rl.HexColor("#E5E5E5")),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 12), ("LINEABOVE", (0, -1), (-1, -1), 1.2, rl.HexColor("#2B8932")),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    doc.build([head, Spacer(1, 14), meta, Spacer(1, 18), bill_to, Spacer(1, 12), deliv, Spacer(1, 16), t, Spacer(1, 24),
               Paragraph("Thank you for delivering with DashLocal. Stores with a card on file are charged automatically (monthly fee at the start of each month, "
                         "deliveries every Monday for the week before). Otherwise pay by card in the Merchant Portal, quoting the invoice number.", small)])
    return buf.getvalue()


@router.get("/invoices/{invoice_id}.pdf")
async def download_invoice(invoice_id: str, token: str = ""):
    p = linkpages.download_payload(token, "invoice")
    if p["sub"] != invoice_id:
        raise linkpages.LinkPageError("invoice", "wrong", 403)
    inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not inv:
        raise linkpages.LinkPageError("invoice", "missing", 404)
    pdf = await run_in_threadpool(invoice_pdf, inv)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="DashLocal-{inv["number"]}.pdf"', "Cache-Control": "no-store"})


@router.get("/admin/invoices", dependencies=ADMIN_ONLY)
async def admin_invoices(status: Optional[str] = None, merchant_id: Optional[str] = None):
    q = {k: v for k, v in (("status", status), ("merchant_id", merchant_id)) if v}
    invs = await db.invoices.find(q, {"_id": 0}).sort("number", -1).to_list(500)
    alls = await db.invoices.find({}, {"_id": 0, "status": 1, "total": 1}).to_list(10000)
    return {**invoice_summary(alls), "invoices": invs}


class InvoicesPaidIn(BaseModel):
    ids: List[str]
    paid: bool = True
    note: Optional[str] = None


@router.post("/admin/invoices/mark-paid")
async def mark_invoices_paid(body: InvoicesPaidIn, u: dict = Depends(admin_user)):
    """Confirm payment (usually one the shop reported) or set invoices back to unpaid (e.g. reported payment not received)."""
    if not body.ids:
        raise HTTPException(400, "Pick at least one invoice")
    note = " ".join((body.note or "").split())[:200]
    invs = await db.invoices.find({"id": {"$in": body.ids[:500]}}, {"_id": 0}).to_list(500)
    if body.paid:  # validate the whole batch first so a bad row never leaves it half-saved
        for inv in invs:
            if not (note or (inv.get("report") or {}).get("note")):
                raise HTTPException(400, f"Add a payment note for {inv['number']} (e.g. e-Transfer reference)")
    n = 0
    for inv in invs:
        if body.paid:
            upd = {"status": "paid", "paid_at": now_iso(), "paid_note": note or inv["report"]["note"], "paid_by": u["email"]}
        else:
            upd = {"status": "unpaid", "paid_at": None, "paid_note": None, "paid_by": None, "report": None}
        n += (await db.invoices.update_one({"id": inv["id"]}, {"$set": upd})).modified_count
    return {"updated": n}


class ReportPaidIn(BaseModel):
    ids: List[str]
    note: str


@router.post("/merchants/{merchant_id}/invoices/report-paid")
async def report_invoices_paid(merchant_id: str, body: ReportPaidIn, u: dict = Depends(merchant_access)):
    """Shop says it paid (e-Transfer, cheque…). Invoices show 'payment reported' until an admin confirms."""
    note = " ".join(body.note.split())[:200]
    if not body.ids or not note:
        raise HTTPException(400, "Pick invoices and add how you paid (e.g. e-Transfer reference)")
    res = await db.invoices.update_many({"id": {"$in": body.ids[:500]}, "merchant_id": merchant_id, "status": "unpaid"},
                                        {"$set": {"status": "reported", "report": {"note": note, "at": now_iso(), "by": u["email"]}}})
    if res.modified_count:
        biz_base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
        m = await merchant_routes.get_merchant_or_404(merchant_id)
        realtime.spawn(onboarding.notify_admin(f"{m['name']} reported a payment", f"<p>{res.modified_count} invoice(s) · “{escape(note)}”</p><p>Confirm it in Admin › Invoices.</p>", biz_base))
    return {"updated": res.modified_count}


class BusinessIn(BaseModel):
    name: str
    address: str
    hst_number: str
    hst_pct: float


@router.get("/admin/business", dependencies=ADMIN_ONLY)
async def read_business():
    return await get_business()


@router.put("/admin/business", dependencies=ADMIN_ONLY)
async def update_business(body: BusinessIn):
    """Details printed on new invoices; issued invoices keep what they were issued with."""
    v = {"name": " ".join(body.name.split())[:80], "address": " ".join(body.address.split())[:200],
         "hst_number": " ".join(body.hst_number.split())[:40], "hst_pct": round(body.hst_pct, 3)}
    if not v["name"] or not v["address"] or not v["hst_number"]:
        raise HTTPException(400, "Business name, address and HST number are required")
    if not 0 <= v["hst_pct"] <= 30:
        raise HTTPException(400, "HST % must be between 0 and 30")
    await db.settings.update_one({"key": "business"}, {"$set": {"key": "business", "value": v}}, upsert=True)
    return v
