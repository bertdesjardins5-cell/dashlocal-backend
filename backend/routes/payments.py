from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from fastapi.concurrency import run_in_threadpool
from html import escape
import re
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from core import ADMIN_ONLY, db, issue_token, merchant_access, now_iso
from routes import billing
from routes import invoices
from routes import merchants as merchant_routes
from routes import linkpages

router = APIRouter(prefix="/api")


# ---- monthly statement PDF (card payments: routes/square.py)
class MonthIn(BaseModel):
    month: str  # YYYY-MM


@router.post("/merchants/{merchant_id}/statements/link")
async def statement_pdf_link(merchant_id: str, body: MonthIn, u: dict = Depends(merchant_access)):
    await merchant_routes.get_merchant_or_404(merchant_id)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", body.month):
        raise HTTPException(400, "Pick a month")
    tok = issue_token(merchant_id, u["role"], purpose="mstatement", minutes=5, month=body.month)
    return {"path": f"/api/merchants/{merchant_id}/statements/{body.month}.pdf?token={tok}"}


def statement_pdf(m: dict, month: str, invs: list, biz: dict, owed: float) -> bytes:
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors as rl
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=f"Statement {month}", author=biz["name"])
    body = ParagraphStyle("b", parent=getSampleStyleSheet()["Normal"], fontName="Helvetica", fontSize=10, leading=14, textColor=rl.HexColor("#333335"))
    e = lambda v: escape(str(v or ""))
    money = lambda n: f"CA${n:,.2f}"
    label = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    head = Table([[Paragraph('<font name="Helvetica-Bold" size="18" color="#333335">LOCAL</font><font name="Helvetica-Bold" size="18" color="#D7262E">DASH</font>', body),
                   Paragraph(f'<para alignment="right"><font name="Helvetica-Bold" size="18">MONTHLY STATEMENT</font><br/>{e(label)}</para>', body)]], colWidths=[3.5 * inch, 3.6 * inch])
    who = Table([[Paragraph(f"<b>{e(biz['name'])}</b><br/>{e(biz['address'])}<br/>HST Reg. No. {e(biz['hst_number'])}", body),
                  Paragraph(f"<para alignment='right'><b>FOR</b><br/>{e(m['name'])}<br/>{e(m.get('address') or 'Address not on file')}</para>", body)]], colWidths=[3.5 * inch, 3.6 * inch])
    rows = [["Invoice", "Date", "Delivery", "Subtotal", "HST", "Total", "Status"]] + [
        [i["number"], i["date"], f"{i['delivery']['tracking_code']} · {i['delivery']['customer_name']}"[:28], money(i["subtotal"]), money(i["hst"]), money(i["total"]), i["status"].upper()]
        for i in invs] + [["Totals", "", f"{len(invs)} invoices", money(sum(i["subtotal"] for i in invs)), money(sum(i["hst"] for i in invs)), money(sum(i["total"] for i in invs)), ""]]
    t = Table(rows, colWidths=[0.95 * inch, 0.85 * inch, 1.9 * inch, 0.85 * inch, 0.7 * inch, 0.85 * inch, 0.9 * inch], repeatRows=1)
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                           ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9), ("BACKGROUND", (0, 0), (-1, 0), rl.HexColor("#F5F5F5")),
                           ("ALIGN", (3, 0), (5, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.8, rl.HexColor("#333335")),
                           ("LINEABOVE", (0, -1), (-1, -1), 1.2, rl.HexColor("#2B8932")), ("ROWBACKGROUNDS", (0, 1), (-1, -2), [rl.white, rl.HexColor("#FAFAFA")])]))
    paid = sum(i["total"] for i in invs if i["status"] == "paid")
    doc.build([head, Spacer(1, 12), who, Spacer(1, 16), t, Spacer(1, 14),
               Paragraph(f"Paid this month's invoices: <b>{money(paid)}</b> · Still owing from this month: <b>{money(sum(i['total'] for i in invs) - paid)}</b>"
                         f" · Total outstanding on your account: <b>{money(owed)}</b>", body)])
    return buf.getvalue()


@router.get("/merchants/{merchant_id}/statements/{month}.pdf")
async def download_statement_pdf(merchant_id: str, month: str, token: str = ""):
    p = linkpages.download_payload(token, "mstatement")
    if p["sub"] != merchant_id or p.get("month") != month:
        raise linkpages.LinkPageError("mstatement", "wrong", 403)
    m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0})
    if not m:
        raise linkpages.LinkPageError("mstatement", "missing", 404)
    invs = await db.invoices.find({"merchant_id": merchant_id, "date": {"$regex": f"^{re.escape(month)}-"}}, {"_id": 0}).sort("number", 1).to_list(5000)
    owed = invoices.invoice_summary(await db.invoices.find({"merchant_id": merchant_id}, {"_id": 0, "status": 1, "total": 1}).to_list(10000))["outstanding"]
    pdf = await run_in_threadpool(statement_pdf, m, month, invs, await invoices.get_business(), owed)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="LocalDash-statement-{month}.pdf"', "Cache-Control": "no-store"})


@router.get("/rate-card")
async def rate_card():
    """Public: the standard prices shown on the landing page (stores may have custom rates)."""
    p, biz = await billing.get_pricing(), await invoices.get_business()
    tiers = [{"name": n, "min_drops": lo, "max_drops": (billing.VOLUME_TIERS[i + 1][1] - 1) if i + 1 < len(billing.VOLUME_TIERS) else None,
              "rate": round(p["base_fee"] if r is None else min(r, p["base_fee"]), 2)} for i, (n, lo, r) in enumerate(billing.VOLUME_TIERS)]
    return {k: p[k] for k in ("currency", "base_fee", "included_km", "per_km", "priority_fee")} | {"hst_pct": biz["hst_pct"], "tiers": tiers,
            "pharmacy": {**billing.PHARMACY, "runs": [{"key": r["key"], "label": r["label"], "cutoff_label": r["cutoff_label"], "pickup_label": r["pickup_label"],
                                               "window_label": r["window_label"]} for r in billing.runs_today()]}}


class PharmacyIn(BaseModel):
    enabled: bool
    pickup_address: Optional[str] = None


@router.put("/admin/merchants/{merchant_id}/pharmacy", dependencies=ADMIN_ONLY)
async def set_pharmacy(merchant_id: str, body: PharmacyIn):
    """Mark a store as a pharmacy partner (Addendum A). The pickup address is where distance is measured from."""
    m0 = await merchant_routes.get_merchant_or_404(merchant_id)
    upd: dict = {"pharmacy_partner": body.enabled}
    addr = (body.pickup_address or "").strip()
    if addr and addr != (m0.get("pickup") or {}).get("address"):
        try:
            geo = await merchant_routes.geocode(addr)
        except Exception:
            raise HTTPException(503, "Address lookup is unavailable right now — try again shortly")
        if not geo:
            raise HTTPException(400, "Pickup address not found in Kitchener-Waterloo — check the street and number")
        upd["pickup"] = {"lat": geo["lat"], "lng": geo["lng"], "address": addr}
    if body.enabled:
        upd["pharmacy_since"] = now_iso()
    await db.merchants.update_one({"id": merchant_id}, {"$set": upd})
    if body.enabled:
        await invoices.ensure_platform_invoices()
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    return {"merchant_id": merchant_id, "pharmacy_partner": bool(m.get("pharmacy_partner")), "pickup": m.get("pickup")}
