from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import Response
from fastapi.concurrency import run_in_threadpool
import os
from html import escape
import re
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from core import ADMIN_ONLY, db, issue_token, logger, merchant_access, now_iso
from routes import billing
from routes import invoices
from routes import merchants as merchant_routes
from routes import linkpages
from routes import onboarding
from services import realtime

router = APIRouter(prefix="/api")


# ---- monthly statement PDF + card payments (Stripe Checkout)
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
    head = Table([[Paragraph('<font name="Helvetica-Bold" size="18" color="#333335">DASH</font><font name="Helvetica-Bold" size="18" color="#D7262E">LOCAL</font>', body),
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
                    headers={"Content-Disposition": f'attachment; filename="DashLocal-statement-{month}.pdf"', "Cache-Control": "no-store"})


def stripe_client():
    """Stripe SDK configured for this environment (the pod's test key goes through Emergent's Stripe proxy)."""
    import stripe
    stripe.api_key = os.environ["STRIPE_API_KEY"]
    if "sk_test_emergent" in stripe.api_key:
        stripe.api_base = "https://integrations.emergentagent.com/stripe"
    return stripe


class CheckoutIn(BaseModel):
    ids: List[str]


@router.post("/merchants/{merchant_id}/invoices/checkout")
async def invoices_checkout(merchant_id: str, body: CheckoutIn, request: Request, u: dict = Depends(merchant_access)):
    """Stripe Checkout for selected outstanding invoices. Amount is computed here from the database, never from the app."""
    stripe = stripe_client()
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    invs = await db.invoices.find({"id": {"$in": body.ids[:100]}, "merchant_id": merchant_id, "status": {"$in": ["unpaid", "reported"]}}, {"_id": 0}).to_list(100)
    if not invs:
        raise HTTPException(400, "Pick at least one outstanding invoice")
    cents = sum(int(round(i["total"] * 100)) for i in invs)
    if cents < 50:
        raise HTTPException(400, "Card payments need at least CA$0.50")
    base = await onboarding.app_base(request)
    # never two payable card sessions for the same invoice: re-check earlier open ones with Stripe first
    ids = [i["id"] for i in invs]
    for old in await db.payment_sessions.find({"merchant_id": merchant_id, "status": "open", "invoice_ids": {"$in": ids}}, {"_id": 0}).to_list(50):
        try:
            st = (await settle_session(old["session_id"]))["status"]
        except Exception as e:
            if isinstance(e, stripe.error.InvalidRequestError) or "no such checkout" in str(e).lower():
                await db.payment_sessions.update_one({"session_id": old["session_id"]}, {"$set": {"status": "missing"}})
                continue  # Stripe has no such session, so it can never be paid
            logger.warning(f"Could not check old checkout {old['session_id']}: {e}")
            raise HTTPException(424, "Couldn't check your earlier card checkout — please try again in a minute")
        if st != "open":
            continue
        if sorted(old["invoice_ids"]) == sorted(ids) and old.get("url"):
            return {"url": old["url"], "session_id": old["session_id"], "amount": old["amount_cents"] / 100, "reused": True}
        mins = max(1, 30 - int((datetime.now(timezone.utc) - datetime.fromisoformat(old["created_at"])).total_seconds() // 60))
        raise HTTPException(409, f"You have an unfinished card checkout for some of these invoices. Finish it, or try again in about {mins} min when it expires.")
    invs = await db.invoices.find({"id": {"$in": ids}, "status": {"$in": ["unpaid", "reported"]}}, {"_id": 0}).to_list(100)
    if not invs:
        raise HTTPException(400, "These invoices were just paid")
    cents = sum(int(round(i["total"] * 100)) for i in invs)
    nums = ", ".join(i["number"] for i in invs)
    try:
        session = await run_in_threadpool(lambda: stripe.checkout.Session.create(
            mode="payment", payment_method_types=["card"], customer_email=u.get("email"),
            expires_at=int((datetime.now(timezone.utc) + timedelta(minutes=31)).timestamp()),
            line_items=[{"price_data": {"currency": "cad", "unit_amount": cents,
                                        "product_data": {"name": f"DashLocal invoices ({len(invs)})", "description": nums[:500]}}, "quantity": 1}],
            metadata={"merchant_id": merchant_id, "invoices": ",".join(i["id"] for i in invs)[:500]},
            success_url=f"{base}/merchant/{merchant_id}?paid={{CHECKOUT_SESSION_ID}}", cancel_url=f"{base}/merchant/{merchant_id}?paid=cancel"))
    except Exception as e:
        logger.warning(f"Stripe checkout failed: {e}")
        raise HTTPException(424, "Card payments are unavailable right now — please try again")
    await db.payment_sessions.insert_one({"session_id": session.id, "merchant_id": merchant_id, "invoice_ids": [i["id"] for i in invs],
                                          "amount_cents": cents, "currency": "cad", "status": "open", "created_at": now_iso(), "by": u["email"], "url": session.url})
    return {"url": session.url, "session_id": session.id, "amount": cents / 100}


async def settle_session(session_id: str) -> dict:
    """Ask Stripe for the session's real state; if paid, mark its invoices paid exactly once."""
    stripe = stripe_client()
    ps = await db.payment_sessions.find_one({"session_id": session_id}, {"_id": 0})
    if not ps:
        raise HTTPException(404, "Payment not found")
    if ps["status"] == "paid":
        return ps
    s = await run_in_threadpool(lambda: stripe.checkout.Session.retrieve(session_id))
    if s.payment_status == "paid" and s.amount_total == ps["amount_cents"]:
        if (await db.payment_sessions.update_one({"session_id": session_id, "status": {"$ne": "paid"}},
                                                 {"$set": {"status": "paid", "paid_at": now_iso(), "payment_intent": s.payment_intent}})).modified_count:
            already = await db.invoices.find({"id": {"$in": ps["invoice_ids"]}, "status": "paid"}, {"_id": 0, "number": 1, "total": 1}).to_list(100)
            if already:  # paid twice (e.g. an old checkout tab): record the credit and tell dispatch to refund
                credit = round(sum(i["total"] for i in already), 2)
                await db.payment_sessions.update_one({"session_id": session_id}, {"$set": {"credit": credit, "credit_invoices": [i["number"] for i in already]}})
                ps.update(credit=credit)
                m = await db.merchants.find_one({"id": ps["merchant_id"]}, {"_id": 0}) or {}
                realtime.spawn(onboarding.notify_admin(f"Refund needed: {m.get('name', ps['merchant_id'])} paid CA${credit:.2f} twice",
                                   f"<p>Card payment {escape(str(s.payment_intent))} covered invoices already paid: {escape(', '.join(i['number'] for i in already))}.</p>"
                                   "<p>Refund it from the Stripe dashboard.</p>", (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")))
            await db.invoices.update_many({"id": {"$in": ps["invoice_ids"]}, "status": {"$ne": "paid"}},
                                          {"$set": {"status": "paid", "paid_at": now_iso(), "paid_note": f"Card payment (Stripe {str(s.payment_intent or session_id)[-8:]})",
                                                    "paid_by": "card", "stripe_session": session_id}})
        ps["status"] = "paid"
    elif s.payment_status == "paid":  # paid, but not the amount we asked for: flag it for dispatch
        if (await db.payment_sessions.update_one({"session_id": session_id, "status": "open"},
                                                 {"$set": {"status": "amount_mismatch", "paid_amount_cents": s.amount_total}})).modified_count:
            realtime.spawn(onboarding.notify_admin("Card payment amount mismatch", f"<p>Stripe session {escape(session_id)} was paid {s.amount_total} cents; expected {ps['amount_cents']}.</p>",
                               (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")))
        ps["status"] = "amount_mismatch"
    elif s.status == "expired":
        await db.payment_sessions.update_one({"session_id": session_id}, {"$set": {"status": "expired"}})
        ps["status"] = "expired"
    return ps


@router.get("/merchants/{merchant_id}/payments/{session_id}")
async def payment_status(merchant_id: str, session_id: str, _: dict = Depends(merchant_access)):
    ps = await db.payment_sessions.find_one({"session_id": session_id, "merchant_id": merchant_id}, {"_id": 0})
    if not ps:
        raise HTTPException(404, "Payment not found")
    try:
        ps = await settle_session(session_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Stripe status check failed: {e}")
        raise HTTPException(424, "Couldn't reach the card processor — we'll keep checking")
    return {"status": ps["status"], "amount": ps["amount_cents"] / 100, "count": len(ps["invoice_ids"]), "credit": ps.get("credit", 0)}


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Optional: set STRIPE_WEBHOOK_SECRET to settle payments even if the shop never returns to the app."""
    import stripe
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(404, "Webhook not configured")
    try:
        ev = stripe.Webhook.construct_event(await request.body(), request.headers.get("stripe-signature", ""), secret)
    except Exception:
        raise HTTPException(400, "Invalid webhook")
    if ev["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        try:
            await settle_session(ev["data"]["object"]["id"])
        except HTTPException:
            pass
    return {"received": True}


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
