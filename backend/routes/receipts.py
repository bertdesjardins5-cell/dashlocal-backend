import re
import secrets
import zipfile
from datetime import datetime
from typing import Optional
from html import escape
from io import BytesIO
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from core import TZ, db, issue_token, logger, merchant_access, now_iso
from models import Stop
from routes import dispatch
from routes import emails
from routes import invoices
from routes import merchants as merchant_routes
from routes import linkpages
from routes import onboarding

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------- signed delivery receipts (law offices)
RECEIPT_LINK_DAYS = 30
SIG_BOX = (360, 200)  # SignaturePad viewBox


def signature_strokes(d: str) -> list:
    """SVG path data from the signature pad ('M x y L x y ...') -> list of strokes [[(x, y), ...], ...]."""
    strokes, cur = [], None
    for cmd, args in re.findall(r"([MLQCZmlqcz])([^MLQCZmlqcz]*)", d or ""):
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", args)]
        pts = list(zip(nums[0::2], nums[1::2]))
        if cmd in "Mm" and pts:
            cur = [pts[0]]
            strokes.append(cur)
            pts = pts[1:]
        if cur is not None and pts:
            cur.append(pts[-1])  # line/curve end points are enough at receipt size
    return [s for s in strokes if s]


def receipt_pdf(st: Stop, store: dict, biz: dict, driver: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors as rl
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.graphics.shapes import Drawing, PolyLine, Rect
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title=f"Signed delivery receipt {st.tracking_code}", author=biz["name"])
    body = ParagraphStyle("b", parent=getSampleStyleSheet()["Normal"], fontName="Helvetica", fontSize=10, leading=14, textColor=rl.HexColor("#333335"))
    e = lambda v: escape(str(v or ""))  # noqa: E731
    head = Table([[Paragraph('<font name="Helvetica-Bold" size="18" color="#333335">ARRIVE</font><font name="Helvetica-Bold" size="18" color="#D7262E">WISE</font>', body),
                   Paragraph('<para alignment="right"><font name="Helvetica-Bold" size="16">SIGNED DELIVERY RECEIPT</font></para>', body)]],
                 colWidths=[3.2 * inch, 3.7 * inch])
    rows = [("Sender", store["name"])] + ([("Matter / file no.", st.matter_ref)] if st.matter_ref else []) + [("Tracking code", st.tracking_code), ("Delivered to", st.customer_name), ("Address", st.address),
            ("Delivered at", f"{merchant_routes.local_fmt(st.delivered_at)} (Eastern)"), ("Driver", driver), ("Receipt ID", st.id)]
    t = Table([[Paragraph(f"<b>{e(k)}</b>", body), Paragraph(e(v), body)] for k, v in rows], colWidths=[1.6 * inch, 5.3 * inch])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, rl.HexColor("#E5E5E5")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    w, h = 4.5 * inch, 2.5 * inch
    sx, sy = w / SIG_BOX[0], h / SIG_BOX[1]
    sig = Drawing(w, h)
    sig.add(Rect(0, 0, w, h, strokeColor=rl.HexColor("#333335"), strokeWidth=1, fillColor=None))
    for stroke in signature_strokes(st.pod_signature):
        pts = [v for x, y in stroke for v in (x * sx, h - y * sy)]  # SVG y grows down, PDF y grows up
        if len(pts) == 2:
            pts += [pts[0] + 0.5, pts[1]]
        sig.add(PolyLine(pts, strokeColor=rl.HexColor("#0A0A0A"), strokeWidth=1.6, strokeLineCap=1, strokeLineJoin=1))
    doc.build([head, Spacer(1, 18), t, Spacer(1, 18), Paragraph("<b>Recipient's signature</b>", body), Spacer(1, 6), sig, Spacer(1, 18),
               Paragraph(f"Signed on the driver's device at the door. Issued by {e(biz['name'])}, {e(biz['address'])}.", body)])
    return buf.getvalue()


async def send_signed_receipt(stop_id: str):
    """After a law-office drop with a signature: email every store login a link to the signed PDF receipt."""
    try:
        st = await dispatch.get_stop_or_404(stop_id)
        m = await db.merchants.find_one({"id": st.merchant_id}, {"_id": 0}) if st.merchant_id else None
        if not (m and m.get("business_type") == "law_office" and st.pod_signature and st.status == "delivered"):
            return
        if not (await db.stops.update_one({"id": stop_id, "receipt_email_at": None}, {"$set": {"receipt_email_at": now_iso()}})).modified_count:
            return  # already sent
        base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
        users = await db.users.find({"merchant_id": m["id"], "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0, "email": 1}).to_list(20)
        if not base or not users:
            await db.stops.update_one({"id": stop_id}, {"$set": {"receipt_email_status": "no_recipient" if base else "no_app_address"}})
            return
        tok = issue_token(stop_id, "merchant", purpose="receipt", minutes=RECEIPT_LINK_DAYS * 24 * 60, n=secrets.token_hex(4))
        link = f"{base}/api/stops/{stop_id}/receipt.pdf?token={tok}"
        html = onboarding.brand_email(
            f"Signed receipt · {st.tracking_code}",
            f"<p>Your delivery <b>{escape(st.tracking_code)}</b>{f' (matter {escape(st.matter_ref)})' if st.matter_ref else ''} to <b>{escape(st.customer_name)}</b> was signed for at "
            f"{escape(merchant_routes.local_fmt(st.delivered_at))}.</p><p>Download the signed PDF receipt for your file. "
            f"The link works for {RECEIPT_LINK_DAYS} days.</p>", ("Download signed receipt", link))
        sent = 0
        for u in users:
            try:
                await emails.send_email(to=onboarding.demo_inbox(u["email"]), subject=f"Signed delivery receipt · {st.tracking_code}" + (f" · {st.matter_ref}" if st.matter_ref else ""), html=html)
                sent += 1
            except Exception as ex:
                logger.warning(f"Receipt email failed for {stop_id}: {ex}")
        await db.stops.update_one({"id": stop_id}, {"$set": {"receipt_email_status": "sent" if sent else "failed"}})
    except Exception as e:
        logger.warning(f"Signed receipt failed for {stop_id}: {e}")


@router.get("/stops/{stop_id}/receipt.pdf")
async def download_receipt(stop_id: str, token: str = ""):
    p = linkpages.download_payload(token, "receipt")
    if p["sub"] != stop_id:
        raise linkpages.LinkPageError("receipt", "wrong", 403)
    doc = await db.stops.find_one({"id": stop_id, "pod_signature": {"$nin": [None, ""]}}, {"_id": 0})
    if not doc or not doc.get("merchant_id"):
        raise linkpages.LinkPageError("receipt", "missing", 404)
    st = Stop(**doc)
    store = await db.merchants.find_one({"id": st.merchant_id}, {"_id": 0})
    if not store:
        raise linkpages.LinkPageError("receipt", "missing", 404)
    pdf = await _pdf_for(st, store, await invoices.get_business())
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="ArriveWise-receipt-{st.tracking_code}.pdf"', "Cache-Control": "no-store"})


# ---------------------------------------------------------------- receipt archive (store portal)
async def _signed_stops(merchant_id: str, limit: int = 500) -> list:
    q = {"merchant_id": merchant_id, "status": "delivered", "pod_signature": {"$nin": [None, ""]}}
    docs = await db.stops.find(q, {"_id": 0}).sort("delivered_at", -1).to_list(limit)
    return [Stop(**d) for d in docs]


async def _pdf_for(st: Stop, store: dict, biz: dict) -> bytes:
    r = await db.routes.find_one({"id": st.route_id}, {"_id": 0, "driver_id": 1}) if st.route_id else None
    d = await db.drivers.find_one({"id": (r or {}).get("driver_id")}, {"_id": 0, "name": 1}) if r else None
    return await run_in_threadpool(receipt_pdf, st, store, biz, d["name"].split(" ")[0] if d else "—")


@router.get("/merchants/{merchant_id}/receipts")
async def list_receipts(merchant_id: str, u: dict = Depends(merchant_access)):
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    if m.get("business_type") != "law_office":
        return {"law_office": False, "receipts": []}
    return {"law_office": True, "receipts": [{"id": s.id, "tracking_code": s.tracking_code, "customer_name": s.customer_name,
                                              "delivered_at": s.delivered_at, "matter_ref": s.matter_ref} for s in await _signed_stops(merchant_id, 500)]}


class ReceiptLinkIn(BaseModel):
    stop_id: Optional[str] = None  # None = all receipts as one ZIP


@router.post("/merchants/{merchant_id}/receipts/link")
async def receipt_link(merchant_id: str, body: ReceiptLinkIn, request: Request, u: dict = Depends(merchant_access)):
    """Short-lived (5 min) download link so the PDF/ZIP opens in a browser tab without a sign-in header."""
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    if m.get("business_type") != "law_office":
        raise HTTPException(403, "Signed receipts are for law offices")
    await merchant_routes.remember_public_base(request)
    if body.stop_id:
        st = await db.stops.find_one({"id": body.stop_id, "merchant_id": merchant_id, "status": "delivered", "pod_signature": {"$nin": [None, ""]}}, {"_id": 1})
        if not st:
            raise HTTPException(404, "No signed receipt for this delivery")
        tok = issue_token(body.stop_id, "merchant", purpose="receipt", minutes=5)
        return {"path": f"/api/stops/{body.stop_id}/receipt.pdf?token={tok}"}
    tok = issue_token(merchant_id, "merchant", purpose="receipts", minutes=5)
    return {"path": f"/api/merchants/{merchant_id}/receipts.zip?token={tok}"}


@router.get("/merchants/{merchant_id}/receipts.zip")
async def download_receipts_zip(merchant_id: str, token: str = ""):
    p = linkpages.download_payload(token, "receipts")
    if p["sub"] != merchant_id:
        raise linkpages.LinkPageError("receipts", "wrong", 403)
    store = await db.merchants.find_one({"id": merchant_id}, {"_id": 0})
    stops = await _signed_stops(merchant_id, 500) if store else []
    if not stops:
        raise linkpages.LinkPageError("receipts", "missing", 404)
    biz = await invoices.get_business()
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for st in stops:
            ref = re.sub(r"[^\w-]+", "_", st.matter_ref or "").strip("_")
            z.writestr(f"ArriveWise-receipt-{(ref + '-') if ref else ''}{st.tracking_code}-{(st.delivered_at or '')[:10]}.pdf", await _pdf_for(st, store, biz))
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="ArriveWise-signed-receipts-{datetime.now(TZ).date()}.zip"', "Cache-Control": "no-store"})
