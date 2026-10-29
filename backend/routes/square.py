"""Square payments: merchant card on file (auto-charged monthly platform fee + weekly delivery charges),
one-off invoice payments, and customer order payments. All amounts CAD incl. Ontario HST.
Card numbers never touch ArriveWise: Square's Web Payments SDK (hosted page below) returns a one-use token."""
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from html import escape
import asyncio
import hashlib
import json
import os
import re
import secrets
import uuid
import httpx
from core import ADMIN_ONLY, TZ, admin_user, check_order_token, db, decode_token, issue_token, logger, merchant_access, now_iso
from routes import auth, billing, emails, invoices, onboarding
from routes import merchants as merchant_routes
from services import realtime

router = APIRouter(prefix="/api")
SQUARE_VERSION = "2025-10-16"
MAX_ATTEMPTS = 3
RETRY_HOURS = 24
CARD_CONSENT = ("I authorize ArriveWise to charge this card for my monthly platform fee and, each week, for the deliveries completed "
                "the week before — all in CAD including 13% Ontario HST. A receipt is emailed after every charge. I can remove the card anytime.")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


# ---------------------------------------------------------------- Square API
def sandbox() -> bool:
    return os.environ.get("SQUARE_APPLICATION_ID", "").startswith("sandbox-")


def card_usable(sqd: Optional[dict]) -> bool:
    """A saved card only works in the Square mode (test or live) it was saved in. Cards saved before this flag were test cards."""
    return bool((sqd or {}).get("card_id")) and bool((sqd or {}).get("live")) == (not sandbox())


def configured() -> bool:
    return all(os.environ.get(k) for k in ("SQUARE_ACCESS_TOKEN", "SQUARE_APPLICATION_ID", "SQUARE_LOCATION_ID"))


def api_base() -> str:
    return "https://connect.squareupsandbox.com/v2" if sandbox() else "https://connect.squareup.com/v2"


def sdk_url() -> str:
    return "https://sandbox.web.squarecdn.com/v1/square.js" if sandbox() else "https://web.squarecdn.com/v1/square.js"


SETUP_ERRORS = ("NETWORK", "NOT_CONFIGURED", "UNAUTHORIZED", "ACCESS_TOKEN_EXPIRED", "ACCESS_TOKEN_REVOKED", "FORBIDDEN", "INSUFFICIENT_SCOPES")


def http_code(e: "SquareError") -> int:
    """402 = the card itself was refused; 503 = our Square setup or connection is the problem."""
    return 503 if e.code in SETUP_ERRORS else 402


class SquareError(Exception):
    def __init__(self, msg: str, code: str = ""):
        super().__init__(msg)
        self.code = code


CARD_ERRORS = {"CARD_DECLINED": "The card was declined", "GENERIC_DECLINE": "The card was declined", "INSUFFICIENT_FUNDS": "Insufficient funds",
               "CVV_FAILURE": "The security code (CVV) didn't match", "ADDRESS_VERIFICATION_FAILURE": "The postal code didn't match the card",
               "INVALID_EXPIRATION": "The expiry date is invalid", "EXPIRATION_FAILURE": "The card has expired", "INVALID_CARD": "The card number is invalid",
               "CARD_NOT_SUPPORTED": "This card type isn't supported", "TRANSACTION_LIMIT": "The card's transaction limit was reached",
               "VERIFY_CVV_FAILURE": "The security code (CVV) didn't match", "VERIFY_AVS_FAILURE": "The postal code didn't match the card"}


async def sq(method: str, path: str, body: Optional[dict] = None) -> dict:
    if not configured():
        raise SquareError("Card payments aren't set up yet", "NOT_CONFIGURED")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, api_base() + path, json=body, headers={
                "Authorization": f"Bearer {os.environ['SQUARE_ACCESS_TOKEN']}", "Square-Version": SQUARE_VERSION, "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        logger.warning(f"Square unreachable: {e}")
        raise SquareError("The card processor is unreachable — try again in a minute", "NETWORK")
    j = r.json() if r.content else {}
    if r.status_code >= 300:
        err = (j.get("errors") or [{}])[0]
        code = err.get("code", "")
        logger.warning(f"Square {method} {path} -> {r.status_code} {code}: {err.get('detail', '')}")
        if code in SETUP_ERRORS:
            raise SquareError("Card payments are temporarily unavailable — please try again later", code)
        raise SquareError(CARD_ERRORS.get(code) or "The card payment didn't go through", code)
    return j


def cents(x: float) -> int:
    return int(round(x * 100))


async def charge(source_id: str, amount_cents: int, key: str, note: str, ref: str, customer_id: Optional[str] = None, email: Optional[str] = None) -> dict:
    body = {"idempotency_key": key[:45], "source_id": source_id, "amount_money": {"amount": amount_cents, "currency": "CAD"},
            "location_id": os.environ["SQUARE_LOCATION_ID"], "reference_id": ref[:40], "note": note[:500], "autocomplete": True}
    if customer_id:
        body["customer_id"] = customer_id
    if email:
        body["buyer_email_address"] = email
    p = (await sq("POST", "/payments", body))["payment"]
    if p.get("status") != "COMPLETED":
        raise SquareError("The card payment didn't complete", p.get("status", ""))
    cd = (p.get("card_details") or {}).get("card") or {}
    return {"payment_id": p["id"], "receipt_url": p.get("receipt_url"), "receipt_number": p.get("receipt_number"),
            "brand": cd.get("card_brand"), "last4": cd.get("last_4"), "amount_cents": p["amount_money"]["amount"]}


def card_label(brand: Optional[str], last4: Optional[str]) -> str:
    return f"{(brand or 'Card').replace('_', ' ').title()} ••{last4 or '????'}"


# ---------------------------------------------------------------- emails
async def merchant_recipients(merchant_id: str) -> list:
    demo = {em for _, em, _ in auth.DEMO_MERCHANT_USERS}
    users = await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0, "email": 1}).to_list(20)
    return list(dict.fromkeys(emails.TEST_INBOX if u["email"] in demo else u["email"] for u in users))


def money(x: float) -> str:
    return f"CA${x:,.2f}"


def amounts_table(rows: list, subtotal: float, hst_pct: float, hst: float, total: float, tip: float = 0) -> str:
    td = 'style="padding:6px 0;font-family:Arial,sans-serif;font-size:14px"'
    tr = "".join(f'<tr><td {td}>{escape(a)}</td><td {td} align="right">{money(b)}</td></tr>' for a, b in rows)
    tip_tr = f'<tr><td {td}>Driver tip (no HST)</td><td {td} align="right">{money(tip)}</td></tr>' if tip else ""
    return (f'<table width="100%" cellpadding="0" cellspacing="0">{tr}'
            f'<tr><td {td}>Subtotal</td><td {td} align="right">{money(subtotal)}</td></tr>'
            f'<tr><td {td}>HST ({hst_pct:g}%, Ontario)</td><td {td} align="right">{money(hst)}</td></tr>{tip_tr}'
            f'<tr><td {td}><b>Total charged (CAD)</b></td><td {td} align="right"><b>{money(total)}</b></td></tr></table>')


async def email_merchant_receipt(merchant_id: str, ch: dict, invs: list):
    m = await db.merchants.find_one({"id": merchant_id}, {"_id": 0, "name": 1}) or {}
    rows = [(f"{i['number']} · {i['delivery']['customer_name'] if i.get('kind') == 'platform' else i['delivery']['tracking_code']}", i["subtotal"]) for i in invs[:60]]
    if len(invs) > 60:
        rows.append((f"…and {len(invs) - 60} more invoices", round(sum(i["subtotal"] for i in invs[60:]), 2)))
    hst_pct = invs[0]["hst_pct"] if invs else 13.0
    html = onboarding.brand_email(f"Payment receipt — {money(ch['total'])}",
                                  f"<p>Hi {escape(m.get('name', ''))}, we charged <b>{escape(card_label(ch.get('brand'), ch.get('last4')))}</b> "
                                  f"<b>{money(ch['total'])}</b> on {datetime.now(TZ).strftime('%B %-d, %Y')} for: <b>{escape(ch['label'])}</b>.</p>"
                                  + amounts_table(rows, ch["subtotal"], hst_pct, ch["hst"], ch["total"])
                                  + f"<p style='color:#5C5C5E;font-size:12px'>Square payment {escape(ch['payment_id'])}. The invoices are marked paid in your Merchant Portal.</p>",
                                  ("View Square receipt", ch["receipt_url"]) if (ch.get("receipt_url") or "").startswith("https://") else None)
    sent = []
    for to in await merchant_recipients(merchant_id):
        try:
            await emails.send_email(to=to, subject=f"ArriveWise receipt: {money(ch['total'])} charged — {ch['label']}", html=html)
            sent.append({"to": to, "status": "sent"})
        except Exception as e:
            sent.append({"to": to, "status": f"failed: {e}"})
    return sent


# ---------------------------------------------------------------- invoices -> one Square charge
async def settle_invoices(merchant_id: str, invs: list, source_id: str, key: str, label: str, kind: str, by: str,
                          customer_id: Optional[str] = None, charge_doc: Optional[str] = None) -> dict:
    sub = round(sum(i["subtotal"] for i in invs), 2)
    hst = round(sum(i["hst"] for i in invs), 2)
    total = round(sum(i["total"] for i in invs), 2)
    nums = ", ".join(i["number"] for i in invs)
    p = await charge(source_id, cents(total), key, f"ArriveWise {label}: {nums}", f"{merchant_id}"[:40], customer_id=customer_id)
    ch = {"merchant_id": merchant_id, "kind": kind, "label": label, "invoice_ids": [i["id"] for i in invs], "subtotal": sub, "hst": hst, "total": total,
          "status": "paid", "paid_at": now_iso(), "by": by, **p}
    if charge_doc:
        await db.square_charges.update_one({"id": charge_doc}, {"$set": ch})
    else:
        ch.update(id=str(uuid.uuid4()), key=key, created_at=now_iso(), attempts=1)
        await db.square_charges.insert_one(dict(ch))
    await db.invoices.update_many({"id": {"$in": ch["invoice_ids"]}, "status": {"$ne": "paid"}},
                                  {"$set": {"status": "paid", "paid_at": now_iso(), "paid_by": "square",
                                            "paid_note": f"Card {card_label(p.get('brand'), p.get('last4'))} · Square {p['payment_id'][-8:]}", "square_payment_id": p["payment_id"]}})
    ch["receipt_emails"] = await email_merchant_receipt(merchant_id, ch, invs)
    await db.square_charges.update_one({"key": key} if not charge_doc else {"id": charge_doc}, {"$set": {"receipt_emails": ch["receipt_emails"]}})
    return ch


async def charging_invoice_ids(merchant_id: str) -> set:
    """Invoices inside a card charge that is being processed right now — their price must not change."""
    return {i for c in await db.square_charges.find({"merchant_id": merchant_id, "status": "processing"}, {"_id": 0, "invoice_ids": 1}).to_list(100)
            for i in c.get("invoice_ids", [])}


# ---------------------------------------------------------------- auto billing (card on file)
def week_start(d=None):
    d = d or datetime.now(TZ).date()
    return d - timedelta(days=d.weekday())


async def run_auto_billing() -> list:
    """Charge every store with a card on file: unpaid monthly platform fees right away, and every Monday all unpaid
    delivery invoices from before this week in one charge. Failed charges retry daily, up to 3 tries."""
    if not configured():
        return []
    await invoices.ensure_platform_invoices()
    out = []
    monday = week_start().isoformat()
    for m in await db.merchants.find({"square.card_id": {"$exists": True}}, {"_id": 0}).to_list(1000):
        sqd = m["square"]
        if not card_usable(sqd):
            continue  # a test-mode card can't be charged with live keys: the store re-adds its card
        jobs = []
        for inv in await db.invoices.find({"merchant_id": m["id"], "kind": "platform", "status": "unpaid"}, {"_id": 0}).to_list(24):
            jobs.append((f"plat:{inv['id']}", "platform", inv["delivery"]["customer_name"], [inv]))
        weekly = await db.invoices.find({"merchant_id": m["id"], "kind": {"$ne": "platform"}, "status": "unpaid", "date": {"$lt": monday}}, {"_id": 0}).sort("number", 1).to_list(2000)
        if weekly:  # the unpaid set changed: an older failed weekly charge is replaced by this one
            wkey = f"week:{m['id']}:{monday}:{hashlib.sha1(','.join(i['id'] for i in weekly).encode()).hexdigest()[:10]}"
            await db.square_charges.update_many({"merchant_id": m["id"], "kind": "weekly", "status": "failed", "key": {"$ne": wkey}}, {"$set": {"status": "superseded"}})
            jobs.append((wkey, "weekly", f"Weekly deliveries before {monday} ({len(weekly)})", weekly))
        for key, kind, label, invs in jobs:
            out.append(await auto_charge(m, sqd, key, kind, label, invs))
    return [o for o in out if o]


async def auto_charge(m: dict, sqd: dict, key: str, kind: str, label: str, invs: list) -> Optional[dict]:
    doc = await db.square_charges.find_one({"key": key}, {"_id": 0})
    now = datetime.now(timezone.utc)
    if doc and (doc["status"] in ("paid", "processing", "gave_up") or (doc.get("next_try_at") and datetime.fromisoformat(doc["next_try_at"]) > now)):
        return None
    if not doc:
        doc = {"id": str(uuid.uuid4()), "key": key, "merchant_id": m["id"], "kind": kind, "label": label, "status": "processing",
               "attempts": 0, "created_at": now_iso(), "invoice_ids": [i["id"] for i in invs]}
        try:
            await db.square_charges.insert_one(dict(doc))
        except DuplicateKeyError:
            return None
    elif not (await db.square_charges.update_one({"id": doc["id"], "status": "failed"}, {"$set": {"status": "processing", "invoice_ids": [i["id"] for i in invs]}})).modified_count:
        return None
    attempt = doc["attempts"] + 1
    locks = await lock_invoices([i["id"] for i in invs])
    if locks is None:  # a manual card payment is in progress: try again next cycle
        await db.square_charges.update_one({"id": doc["id"]}, {"$set": {"status": "failed", "next_try_at": None}})
        return None
    try:
        invs = await db.invoices.find({"id": {"$in": [i["id"] for i in invs]}, "status": "unpaid"}, {"_id": 0}).to_list(2000)
        if not invs:
            await db.square_charges.update_one({"id": doc["id"]}, {"$set": {"status": "paid", "note": "Paid manually before autopay ran"}})
            return None
        ch = await settle_invoices(m["id"], invs, sqd["card_id"], f"{doc['id'][:13]}:{sqd['card_id'][-16:]}:{attempt}", label, kind, "autopay", customer_id=sqd["customer_id"], charge_doc=doc["id"])
        await db.square_charges.update_one({"id": doc["id"]}, {"$set": {"attempts": attempt, "last_error": None, "next_try_at": None}})
        return {"key": key, "status": "paid", "total": ch["total"]}
    except SquareError as e:
        if e.code == "NETWORK":  # Square may still have charged: retry soon with the same idempotency key
            await db.square_charges.update_one({"id": doc["id"]}, {"$set": {"status": "failed", "last_error": str(e), "next_try_at": (now + timedelta(minutes=10)).isoformat()}})
            return {"key": key, "status": "failed", "error": str(e)}
        final = attempt >= MAX_ATTEMPTS or e.code == "NOT_CONFIGURED"
        await db.square_charges.update_one({"id": doc["id"]}, {"$set": {
            "status": "gave_up" if final else "failed", "attempts": attempt, "last_error": str(e), "last_error_code": e.code,
            "next_try_at": None if final else (now + timedelta(hours=RETRY_HOURS)).isoformat(),
            "total": round(sum(i["total"] for i in invs), 2)}})
        realtime.spawn(email_charge_failed(m, label, round(sum(i["total"] for i in invs), 2), str(e), final))
        return {"key": key, "status": "gave_up" if final else "failed", "error": str(e)}
    finally:
        await db.square_locks.delete_many({"key": {"$in": locks}})


async def email_charge_failed(m: dict, label: str, total: float, reason: str, final: bool):
    nxt = "We won't retry it automatically — please update your card or pay the invoices in your Merchant Portal." if final else \
        f"We'll try again in {RETRY_HOURS} hours. To avoid that, update your card in the Merchant Portal."
    html = onboarding.brand_email("Your card payment didn't go through",
                                  f"<p>We tried to charge <b>{money(total)}</b> (CAD incl. HST) for <b>{escape(label)}</b> to "
                                  f"{escape(card_label(m['square'].get('brand'), m['square'].get('last4')))}, but it failed: {escape(reason)}.</p><p>{nxt}</p>")
    for to in await merchant_recipients(m["id"]):
        try:
            await emails.send_email(to=to, subject=f"ArriveWise: card payment failed — {money(total)}", html=html)
        except Exception as e:
            logger.warning(f"Charge-failed email to {to}: {e}")
    if final:
        await onboarding.notify_admin(f"Autopay gave up: {m.get('name', m['id'])} {money(total)}", f"<p>{escape(label)} · {escape(reason)}</p>",
                                      (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value"))


async def billing_watcher():
    await asyncio.sleep(40)
    await db.square_charges.create_index("key", unique=True)
    await db.square_locks.create_index("key", unique=True)
    await db.square_locks.create_index("at_dt", expireAfterSeconds=900)  # a lock left by a crash frees itself
    await db.square_charges.update_many({"status": "processing", "payment_id": {"$exists": False}}, {"$set": {"status": "failed", "next_try_at": None}})  # interrupted by a restart
    while True:
        try:
            await run_auto_billing()
        except Exception as e:
            logger.warning(f"Square billing loop error: {e}")
        await asyncio.sleep(600)


# ---------------------------------------------------------------- hosted card page (Square Web Payments SDK)
def page_token(kind: str, sub: str, minutes: int = 30, **extra) -> str:
    return issue_token(sub, "pay", purpose="sqpay", minutes=minutes, kind=kind, **extra)


async def page_url(request: Request, tok: str) -> str:
    await merchant_routes.remember_public_base(request)
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value") or str(request.base_url).rstrip("/")
    return f"{base}/api/square/pay?t={tok}"


async def describe(p: dict) -> dict:
    """What the hosted page shows, recomputed from the database (never trusted from the app)."""
    if p["kind"] == "card":
        m = await merchant_routes.get_merchant_or_404(p["sub"])
        return {"title": "Card on file for autopay", "lines": [], "total": None, "who": m["name"], "button": "Save card"}
    if p["kind"] == "invoices":
        invs = await db.invoices.find({"id": {"$in": p["ids"]}, "merchant_id": p["sub"], "status": {"$in": ["unpaid", "reported"]}}, {"_id": 0}).to_list(100)
        if not invs:
            raise HTTPException(409, "These invoices are already paid")
        m = await merchant_routes.get_merchant_or_404(p["sub"])
        return {"title": f"Pay {len(invs)} invoice{'s' if len(invs) != 1 else ''}", "who": m["name"], "invs": invs, "total": round(sum(i["total"] for i in invs), 2),
                "lines": [(i["number"], i["total"]) for i in invs[:8]] + ([(f"+ {len(invs) - 8} more", round(sum(i['total'] for i in invs[8:]), 2))] if len(invs) > 8 else []),
                "subtotal": round(sum(i["subtotal"] for i in invs), 2), "hst": round(sum(i["hst"] for i in invs), 2), "button": "Pay"}
    s = await db.stops.find_one({"id": p["sub"]}, {"_id": 0})
    if p["kind"] == "tip":
        if not s or s.get("status") != "delivered":
            raise HTTPException(404, "Tips open once your order is delivered")
        m = await db.merchants.find_one({"id": s.get("merchant_id")}, {"_id": 0, "name": 1}) or {}
        return {"title": f"Tip your driver · {s['tracking_code']}", "who": m.get("name", "ArriveWise"), "total": None, "lines": [], "button": "Send tip", "stop": s}
    pay = order_payment(s) if s else None
    if not pay:
        raise HTTPException(404, "Nothing to pay on this order")
    if pay["paid"]:
        raise HTTPException(409, "This order is already paid")
    m = await db.merchants.find_one({"id": s.get("merchant_id")}, {"_id": 0, "name": 1}) or {}
    return {"title": f"Order {s['tracking_code']}", "who": m.get("name", "ArriveWise"), "total": pay["total"], "subtotal": pay["amount"], "hst": pay["hst"],
            "hst_pct": pay["hst_pct"], "lines": [(f"Order from {m.get('name', 'the store')}", pay["amount"])], "button": "Pay", "stop": s, "pay": pay}


PAGE_CSS = ("body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#F5F5F5;color:#333335}"
            ".w{max-width:460px;margin:0 auto;padding:20px}.c{background:#fff;border:2px solid #333335;padding:18px}"
            "h1{font-size:20px;margin:0 0 4px}.m{color:#5C5C5E;font-size:13px}.r{display:flex;justify-content:space-between;font-size:14px;padding:4px 0}"
            ".t{font-weight:700;font-size:17px;border-top:2px solid #333335;margin-top:6px;padding-top:8px}"
            "label{display:block;font-size:12px;font-weight:700;margin:14px 0 6px;letter-spacing:.5px}"
            "input[type=email],input[type=number]{width:100%;box-sizing:border-box;min-height:46px;border:2px solid #333335;padding:0 12px;font-size:16px}"
            ".ck{display:flex;gap:10px;align-items:flex-start;font-size:13px;font-weight:400;letter-spacing:0;margin-top:14px}.ck input{width:22px;height:22px;flex:none}"
            "button{width:100%;min-height:52px;margin-top:16px;background:#2B8932;color:#fff;border:0;font-size:16px;font-weight:700;letter-spacing:1px;cursor:pointer}"
            "button:disabled{opacity:.5}.e{color:#D7262E;font-size:14px;margin-top:10px;min-height:18px}.ok{text-align:center;padding:24px 8px}"
            ".sb{background:#FFE9A8;padding:8px 10px;font-size:12px;margin-bottom:12px}#card{margin-top:14px;min-height:90px}"
            ".tips{display:flex;gap:8px}.tips button{flex:1;margin-top:0;min-height:46px;background:#fff;color:#333335;border:2px solid #333335;letter-spacing:0}"
            ".tips button{white-space:nowrap;padding:0 4px;font-size:15px}.tips button.on{background:#333335;color:#fff}#tipc{display:none;margin-top:8px}")


@router.get("/square/pay", response_class=HTMLResponse)
async def pay_page(t: str = ""):
    p = decode_token(t, "sqpay")
    try:
        d = await describe(p)
    except HTTPException as e:
        return HTMLResponse(f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><style>{PAGE_CSS}</style>"
                            f"<div class=w><div class='c ok'><h1>{escape(str(e.detail))}</h1><p class=m>You can close this window.</p></div></div>", status_code=e.status_code)
    nonce = secrets.token_urlsafe(16)
    rows = "".join(f"<div class=r><span>{escape(a)}</span><span>{money(b)}</span></div>" for a, b in d["lines"])
    if d.get("subtotal") is not None and p["kind"] == "order":
        rows += f"<div class=r><span>HST ({d['hst_pct']:g}%, Ontario)</span><span>{money(d['hst'])}</span></div>"
    elif d.get("hst") is not None:
        rows += f"<div class=r><span>incl. HST</span><span>{money(d['hst'])}</span></div>"
    tipping = p["kind"] in ("order", "tip")
    if tipping:
        rows += f"<div class=r id=tiprow{' style=display:none' if p['kind'] == 'order' else ''}><span>Driver tip (no HST)</span><span id=tipv>{money(0)}</span></div>"
    if d["total"] is not None or tipping:
        rows += f"<div class='r t'><span>Total (CAD)</span><span id=totv>{money(d['total'] or 0)}</span></div>"
    extra = ""
    if tipping:
        none = "<button type=button data-t=0 class=on>No tip</button>" if p["kind"] == "order" else ""
        extra = (f"<label>{'ADD A TIP FOR YOUR DRIVER' if p['kind'] == 'order' else 'CHOOSE A TIP — 100% GOES TO YOUR DRIVER'}</label><div class=tips id=tips>{none}"
                 + "".join(f"<button type=button data-t={t}>{money(t).replace('CA', '').replace('.00', '')}</button>" for t in TIP_PRESETS)
                 + "<button type=button data-t=c>Other</button></div>"
                 f"<input id=tipc type=number inputmode=decimal min=1 max={MAX_TIP} step=0.01 placeholder='Tip amount, e.g. 4.50'>"
                 "<label for=em>EMAIL FOR YOUR RECEIPT</label><input id=em type=email autocomplete=email inputmode=email placeholder=you@example.com>")
    if p["kind"] == "card":
        extra = f"<label class=ck><input id=agree type=checkbox><span>{escape(CARD_CONSENT)}</span></label>"
    btn = f"{d['button']} {money(d['total'])}" if d["total"] is not None else d["button"]
    cfg = json.dumps({"app": os.environ.get("SQUARE_APPLICATION_ID", ""), "loc": os.environ.get("SQUARE_LOCATION_ID", ""), "t": t, "kind": p["kind"],
                      "base": d["total"] or 0, "label": d["button"], "maxTip": MAX_TIP,
                      "amount": f"{d['total']:.2f}" if d["total"] is not None else None}).replace("<", "\\u003c")
    html = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>ArriveWise · Secure card payment</title><style>{PAGE_CSS}</style><script src="{sdk_url()}" nonce="{nonce}"></script></head><body><div class=w>
{"<div class=sb>TEST MODE · use card 4111 1111 1111 1111, any future expiry, CVV 111, ZIP 12345</div>" if sandbox() else ""}
<div class=c id=box><h1>{escape(d['title'])}</h1><div class=m>{escape(d.get('who') or '')} · Secured by Square</div><div style="margin-top:12px">{rows}</div>
{extra}<div id=card></div><button id=go disabled>{escape(btn)}</button><div class=e id=err></div>
<div class=m style="margin-top:10px">Your card details go straight to Square. ArriveWise never sees or stores your card number.</div></div></div>
<script nonce="{nonce}">
const C={cfg};const $=i=>document.getElementById(i);let card;let tip=0;let tipMode=C.kind==='tip'?null:'0';
const fmt=n=>'CA$'+n.toFixed(2);
const tipNow=()=>{{if(tipMode==='c'){{const v=parseFloat($('tipc').value);return isFinite(v)?Math.round(v*100)/100:0}}return tipMode?parseFloat(tipMode):0}};
const paint=()=>{{tip=tipNow();const tot=Math.round((C.base+tip)*100)/100;C.amount=tot.toFixed(2);if($('tipv'))$('tipv').textContent=fmt(tip);
 if($('tiprow')&&C.kind==='order')$('tiprow').style.display=tip>0?'flex':'none';if($('totv'))$('totv').textContent=fmt(tot);
 if(C.kind==='order'||C.kind==='tip')$('go').textContent=C.label+(tot>0?' '+fmt(tot):'')}};
if($('tips')){{$('tips').querySelectorAll('button').forEach(b=>b.onclick=()=>{{tipMode=b.dataset.t;$('tips').querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));
 $('tipc').style.display=tipMode==='c'?'block':'none';if(tipMode==='c')$('tipc').focus();paint()}});$('tipc').oninput=paint;paint()}}
const tell=m=>{{try{{if(window.ReactNativeWebView)window.ReactNativeWebView.postMessage(JSON.stringify(m));else if(window.parent!==window)window.parent.postMessage(m,location.origin)}}catch(e){{}}}};
const fail=m=>{{$('err').textContent=m;$('go').disabled=false}};
(async()=>{{try{{const pay=Square.payments(C.app,C.loc);card=await pay.card();await card.attach('#card');$('go').disabled=false}}catch(e){{fail('Card form failed to load — check your connection and reopen this page.')}}}})();
$('go').onclick=async()=>{{$('err').textContent='';
 const em=$('em')?$('em').value.trim():null;
 if(C.kind==='tip'&&!tipMode)return fail('Choose a tip amount');
 if((C.kind==='order'||C.kind==='tip')&&tipMode==='c'&&!(tip>=1&&tip<=C.maxTip))return fail('Enter a tip between CA$1 and CA$'+C.maxTip);
 if((C.kind==='order'||C.kind==='tip')&&!/^[^@\\s]+@[^@\\s]+\\.[A-Za-z]{{2,}}$/.test(em||''))return fail('Enter your email for the receipt');
 if(C.kind==='card'&&!$('agree').checked)return fail('Tick the box to authorize automatic charges');
 const btnTxt=$('go').textContent;$('go').disabled=true;$('go').textContent='Processing…';
 try{{const bc={{countryCode:'CA'}};if(em)bc.email=em;const v=C.kind==='card'?{{intent:'STORE',customerInitiated:true,sellerKeyedIn:false,billingContact:bc}}:{{amount:C.amount,currencyCode:'CAD',intent:'CHARGE',customerInitiated:true,sellerKeyedIn:false,billingContact:bc}};
  const r=await card.tokenize(v);
  if(r.status!=='OK'){{$('go').textContent=btnTxt;return fail((r.errors&&r.errors[0]&&r.errors[0].message)||'Check your card details')}}
  const res=await fetch('/api/square/complete',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{t:C.t,source_id:r.token,email:em,tip:tip}})}});
  const j=await res.json().catch(()=>({{}}));
  if(!res.ok){{$('go').textContent=btnTxt;return fail(typeof j.detail==='string'?j.detail:'Payment failed — try again')}}
  $('box').innerHTML='<div class=ok><h1>'+(C.kind==='card'?'Card saved':C.kind==='tip'?'Thank you! Your tip was sent':'Payment received')+'</h1><p class=m>You can close this window.</p></div>';
  tell({{type:'dashlocal-square',ok:true,result:j}});
 }}catch(e){{$('go').textContent=btnTxt;fail('Payment failed — check your connection and try again')}}}};
</script></body></html>"""
    sq_src = "https://*.squarecdn.com https://*.squareup.com https://*.squareupsandbox.com https://*.cloudfront.net"
    ancestors = " ".join(["'self'"] + [o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip().startswith("https://")])
    csp = (f"default-src 'self'; script-src 'nonce-{nonce}' {sq_src}; style-src 'unsafe-inline' {sq_src}; img-src 'self' data: {sq_src}; "
           f"font-src {sq_src} data:; frame-src {sq_src}; connect-src 'self' {sq_src}; frame-ancestors {ancestors}; base-uri 'none'; form-action 'none'")
    return HTMLResponse(html, headers={"Content-Security-Policy": csp, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


class CompleteIn(BaseModel):
    t: str
    source_id: str
    email: Optional[str] = None
    tip: float = 0


TIP_PRESETS = (2, 3, 5)
MAX_TIP = 100


def clean_tip(tip: float, required: bool) -> float:
    tip = round(tip or 0, 2)
    if tip != tip or tip < 0 or tip > MAX_TIP or (0 < tip < 1) or (required and tip < 1):
        raise HTTPException(400, f"Tip must be between CA$1 and CA${MAX_TIP}")
    return tip


async def take_lock(key: str):
    try:
        await db.square_locks.insert_one({"key": key, "at": now_iso(), "at_dt": datetime.now(timezone.utc)})
    except DuplicateKeyError:
        raise HTTPException(409, "A payment for this is already being processed — wait a moment and refresh")


async def lock_invoices(ids: list) -> Optional[list]:
    """All-or-nothing lock so a manual card payment and autopay never charge the same invoice. None = busy."""
    keys = [f"inv:{i}" for i in ids]
    for n, k in enumerate(keys):
        try:
            await db.square_locks.insert_one({"key": k, "at": now_iso(), "at_dt": datetime.now(timezone.utc)})
        except DuplicateKeyError:
            await db.square_locks.delete_many({"key": {"$in": keys[:n]}})
            return None
    return keys


@router.post("/square/complete")
async def complete(body: CompleteIn):
    p = decode_token(body.t, "sqpay")
    if not (10 <= len(body.source_id) <= 500):
        raise HTTPException(400, "Invalid card token")
    d = await describe(p)
    try:
        if p["kind"] == "card":
            return await save_card(p, body.source_id)
        if p["kind"] == "invoices":
            locks = await lock_invoices([i["id"] for i in d["invs"]])
            if locks is None:
                raise HTTPException(409, "A payment for these invoices is already being processed — wait a moment and refresh")
            try:
                d["invs"] = await db.invoices.find({"id": {"$in": [i["id"] for i in d["invs"]]}, "status": {"$in": ["unpaid", "reported"]}}, {"_id": 0}).to_list(100)
                if not d["invs"]:
                    raise HTTPException(409, "These invoices were just paid")
                ch = await settle_invoices(p["sub"], d["invs"], body.source_id, f"inv-{uuid.uuid4().hex}", f"{len(d['invs'])} invoice(s) paid by card",
                                           "manual", p.get("by") or "merchant")
            finally:
                await db.square_locks.delete_many({"key": {"$in": locks}})
            return {"kind": "invoices", "status": "paid", "total": ch["total"], "count": len(d["invs"]), "card": card_label(ch.get("brand"), ch.get("last4")),
                    "payment_id": ch["payment_id"], "receipt_url": ch.get("receipt_url")}
        if p["kind"] == "tip":
            return await pay_tip(d, body.source_id, (body.email or "").strip(), clean_tip(body.tip, True))
        return await pay_order(d, body.source_id, (body.email or "").strip(), clean_tip(body.tip, False))
    except SquareError as e:
        raise HTTPException(http_code(e), str(e))


async def save_card(p: dict, source_id: str) -> dict:
    m = await merchant_routes.get_merchant_or_404(p["sub"])
    old = m.get("square") or {}
    cid = old.get("customer_id") if bool(old.get("live")) == (not sandbox()) else None  # test-mode customers don't exist in live Square
    if not cid:
        c = await sq("POST", "/customers", {"idempotency_key": str(uuid.uuid4()), "company_name": m["name"][:100], "reference_id": m["id"],
                                            **({"email_address": p["email"]} if EMAIL_RE.match(p.get("email") or "") else {})})
        cid = c["customer"]["id"]
    card = (await sq("POST", "/cards", {"idempotency_key": str(uuid.uuid4()), "source_id": source_id,
                                        "card": {"customer_id": cid, "reference_id": m["id"]}}))["card"]
    info = {"customer_id": cid, "card_id": card["id"], "brand": card.get("card_brand"), "last4": card.get("last_4"), "exp_month": card.get("exp_month"),
            "exp_year": card.get("exp_year"), "saved_at": now_iso(), "saved_by": p.get("by"), "consent_text": CARD_CONSENT, "live": not sandbox()}
    await db.merchants.update_one({"id": m["id"]}, {"$set": {"square": info}})
    if card_usable(old) and old["card_id"] != card["id"]:
        try:
            await sq("POST", f"/cards/{old['card_id']}/disable")
        except SquareError:
            pass
    await db.square_charges.update_many({"merchant_id": m["id"], "status": {"$in": ["failed", "gave_up"]}}, {"$set": {"status": "failed", "next_try_at": None, "attempts": 0}})
    realtime.spawn(run_auto_billing())  # anything overdue is charged to the new card now
    return {"kind": "card", "status": "saved", "card": card_label(info["brand"], info["last4"]), "exp": f"{info['exp_month']:02d}/{info['exp_year']}" if info["exp_month"] else None}


# ---------------------------------------------------------------- merchant endpoints
@router.get("/merchants/{merchant_id}/square")
async def merchant_square(merchant_id: str, _: dict = Depends(merchant_access)):
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    sqd = m.get("square") or {}
    charges = await db.square_charges.find({"merchant_id": merchant_id}, {"_id": 0, "invoice_ids": 0, "receipt_emails": 0}).sort("created_at", -1).to_list(10)
    p = await billing.get_pricing(merchant_id)
    return {"configured": configured(), "sandbox": sandbox(), "monthly_fee": p.get("monthly_fee", 0), "hst_pct": (await invoices.get_business())["hst_pct"],
            "card": {k: sqd.get(k) for k in ("brand", "last4", "exp_month", "exp_year", "saved_at")} if card_usable(sqd) else None,
            "card_needs_update": bool(sqd.get("card_id")) and not card_usable(sqd),
            "charges": [{k: c.get(k) for k in ("id", "kind", "label", "status", "total", "attempts", "last_error", "next_try_at", "paid_at", "created_at", "receipt_url", "brand", "last4")} for c in charges]}


@router.post("/merchants/{merchant_id}/square/card-link")
async def card_link(merchant_id: str, request: Request, u: dict = Depends(merchant_access)):
    await merchant_routes.get_merchant_or_404(merchant_id)
    if not configured():
        raise HTTPException(503, "Card payments aren't set up yet")
    return {"url": await page_url(request, page_token("card", merchant_id, by=u["email"], email=u["email"]))}


@router.delete("/merchants/{merchant_id}/square/card")
async def remove_card(merchant_id: str, _: dict = Depends(merchant_access)):
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    cid = (m.get("square") or {}).get("card_id")
    if not cid:
        raise HTTPException(404, "No card on file")
    try:
        await sq("POST", f"/cards/{cid}/disable")
    except SquareError as e:
        logger.warning(f"Square disable card {cid}: {e}")
    await db.merchants.update_one({"id": merchant_id}, {"$unset": {"square.card_id": "", "square.brand": "", "square.last4": "", "square.exp_month": "", "square.exp_year": ""}})
    return {"ok": True}


class PayInvoicesIn(BaseModel):
    ids: List[str]


@router.post("/merchants/{merchant_id}/invoices/square-pay")
async def pay_invoices_link(merchant_id: str, body: PayInvoicesIn, request: Request, u: dict = Depends(merchant_access)):
    """Secure Square card page for the selected outstanding invoices (the amount is recomputed on the page and at charge time)."""
    if not configured():
        raise HTTPException(503, "Card payments aren't set up yet")
    invs = await db.invoices.find({"id": {"$in": body.ids[:100]}, "merchant_id": merchant_id, "status": {"$in": ["unpaid", "reported"]}}, {"_id": 0, "id": 1, "total": 1}).to_list(100)
    if not invs:
        raise HTTPException(400, "Pick at least one outstanding invoice")
    if sum(cents(i["total"]) for i in invs) < 100:
        raise HTTPException(400, "Card payments need at least CA$1.00")
    return {"url": await page_url(request, page_token("invoices", merchant_id, ids=[i["id"] for i in invs], by=u["email"])),
            "amount": round(sum(i["total"] for i in invs), 2)}


class AmountDueIn(BaseModel):
    amount: Optional[float] = None


@router.put("/merchants/{merchant_id}/stops/{stop_id}/amount-due")
async def set_amount_due(merchant_id: str, stop_id: str, body: AmountDueIn, _: dict = Depends(merchant_access)):
    """What the customer pays for this order in the app (before HST). Empty clears it. Locked once paid."""
    s = await db.stops.find_one({"id": stop_id, "merchant_id": merchant_id}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Delivery not found")
    if (s.get("customer_payment") or {}).get("payment_id"):
        raise HTTPException(409, "The customer already paid for this order")
    amt = body.amount
    if amt is not None and (amt != amt or not 1 <= amt <= 10000):
        raise HTTPException(400, "Amount must be between $1.00 and $10,000")
    amt = round(amt, 2) if amt is not None else None
    await db.stops.update_one({"id": stop_id}, {"$set": {"amount_due": amt}})
    s["amount_due"] = amt
    return {"ok": True, "amount_due": amt, "payment": order_payment(s)}


# ---------------------------------------------------------------- customer payments
def order_payment(s: dict) -> Optional[dict]:
    amt = s.get("amount_due")
    cp = s.get("customer_payment") or {}
    if not amt and not cp:
        return None
    pct = cp.get("hst_pct", 13.0)
    amount = cp.get("amount", amt)
    hst = cp.get("hst", round(amount * pct / 100, 2))
    return {"amount": amount, "hst_pct": pct, "hst": hst, "total": cp.get("total", round(amount + hst, 2)), "paid": bool(cp.get("payment_id")),
            "tip": cp.get("tip", 0), "refunded": refunded(cp),
            "paid_at": cp.get("paid_at"), "card": cp.get("card"), "receipt_url": cp.get("receipt_url"), "payment_id": cp.get("payment_id"), "email": cp.get("email")}


@router.post("/track/{code}/pay-link")
async def order_pay_link(code: str, request: Request):
    s = await db.stops.find_one({"tracking_code": code.strip().upper()}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Tracking code not found")
    await check_order_token(request.headers.get("x-order-token", "").strip(), s["id"])  # only the verified customer (last 4 phone digits)
    pay = order_payment(s)
    if not pay:
        raise HTTPException(404, "Nothing to pay on this order")
    if pay["paid"]:
        raise HTTPException(409, "This order is already paid")
    if not configured():
        raise HTTPException(503, "Card payments aren't set up yet")
    return {"url": await page_url(request, page_token("order", s["id"])), "total": pay["total"]}


@router.post("/track/{code}/tip-link")
async def tip_link(code: str, request: Request):
    """Secure Square page for a driver tip after delivery (the verified customer only)."""
    s = await db.stops.find_one({"tracking_code": code.strip().upper()}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Tracking code not found")
    await check_order_token(request.headers.get("x-order-token", "").strip(), s["id"])
    if s.get("status") != "delivered":
        raise HTTPException(409, "Tips open once your order is delivered")
    if not configured():
        raise HTTPException(503, "Card payments aren't set up yet")
    return {"url": await page_url(request, page_token("tip", s["id"]))}


async def pay_tip(d: dict, source_id: str, email: str, tip: float) -> dict:
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter your email for the receipt")
    s = d["stop"]
    await take_lock(f"tip:{s['id']}")
    try:
        p = await charge(source_id, cents(tip), f"tip-{uuid.uuid4().hex}", f"Driver tip · order {s['tracking_code']}", s["tracking_code"], email=email)
    finally:
        await db.square_locks.delete_one({"key": f"tip:{s['id']}"})
    t = {"payment_id": p["payment_id"], "amount": tip, "total": tip, "paid_at": now_iso(), "card": card_label(p.get("brand"), p.get("last4")),
         "receipt_url": p.get("receipt_url"), "email": email, "driver_id": await stop_driver_id(s)}
    await db.stops.update_one({"id": s["id"]}, {"$push": {"tips": t}})
    realtime.spawn(email_tip_receipt(s, t))
    return {"kind": "tip", "status": "paid", "tracking_code": s["tracking_code"], **{k: t[k] for k in ("amount", "card", "payment_id", "paid_at", "email", "receipt_url")}}


async def stop_driver_id(s: dict) -> Optional[str]:
    r = await db.routes.find_one({"id": s.get("route_id")}, {"_id": 0, "driver_id": 1}) if s.get("route_id") else None
    return (r or {}).get("driver_id")


async def email_tip_receipt(s: dict, t: dict):
    html = onboarding.brand_email(f"Thanks for your tip — {money(t['amount'])}",
                                  f"<p>Your tip of <b>{money(t['amount'])}</b> for order <b>{escape(s['tracking_code'])}</b> was charged to {escape(t['card'])} "
                                  f"on {datetime.now(TZ).strftime('%B %-d, %Y')}. All of it goes to your driver. Tips have no HST.</p>"
                                  f"<p style='color:#5C5C5E;font-size:12px'>Square payment {escape(t['payment_id'])}.</p>",
                                  ("View Square receipt", t["receipt_url"]) if (t.get("receipt_url") or "").startswith("https://") else None)
    try:
        await emails.send_email(to=t["email"], subject=f"Your ArriveWise tip receipt — order {s['tracking_code']} ({money(t['amount'])})", html=html)
    except Exception as e:
        logger.warning(f"Tip receipt email: {e}")


async def pay_order(d: dict, source_id: str, email: str, tip: float = 0) -> dict:
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter your email for the receipt")
    s, pay = d["stop"], d["pay"]
    pct = (await invoices.get_business())["hst_pct"]
    hst = round(pay["amount"] * pct / 100, 2)
    total = round(pay["amount"] + hst + tip, 2)
    await take_lock(f"order:{s['id']}")
    try:
        p = await charge(source_id, cents(total), f"ord-{uuid.uuid4().hex}", f"Order {s['tracking_code']} ({d['who']})" + (f" + tip {money(tip)}" if tip else ""),
                         s["tracking_code"], email=email)
    except Exception:
        await db.square_locks.delete_one({"key": f"order:{s['id']}"})
        raise
    cp = {"payment_id": p["payment_id"], "amount": pay["amount"], "hst_pct": pct, "hst": hst, "tip": tip, "total": total, "paid_at": now_iso(),
          "card": card_label(p.get("brand"), p.get("last4")), "receipt_url": p.get("receipt_url"), "email": email,
          **({"tip_driver_id": await stop_driver_id(s)} if tip else {})}
    await db.stops.update_one({"id": s["id"]}, {"$set": {"customer_payment": cp}})
    realtime.spawn(email_customer_receipt(s, d["who"], cp))
    if s.get("route_id"):
        realtime.spawn(realtime.broadcast_route(s["route_id"]))
    return {"kind": "order", "status": "paid", "tracking_code": s["tracking_code"], **{k: cp[k] for k in ("amount", "hst", "hst_pct", "tip", "total", "card", "payment_id", "paid_at", "email", "receipt_url")}}


async def email_customer_receipt(s: dict, store: str, cp: dict):
    html = onboarding.brand_email(f"Payment received — {money(cp['total'])}",
                                  f"<p>Thanks! We received your payment for order <b>{escape(s['tracking_code'])}</b> from <b>{escape(store)}</b>, "
                                  f"charged to {escape(cp['card'])} on {datetime.now(TZ).strftime('%B %-d, %Y')}.</p>"
                                  + amounts_table([(f"Order from {store}", cp["amount"])], cp["amount"], cp["hst_pct"], cp["hst"], cp["total"], cp.get("tip") or 0)
                                  + f"<p style='color:#5C5C5E;font-size:12px'>Square payment {escape(cp['payment_id'])}.</p>",
                                  ("View Square receipt", cp["receipt_url"]) if (cp.get("receipt_url") or "").startswith("https://") else None)
    try:
        await emails.send_email(to=cp["email"], subject=f"Your ArriveWise receipt — order {s['tracking_code']} ({money(cp['total'])})", html=html)
        status = "sent"
    except Exception as e:
        status = f"failed: {e}"
    await db.stops.update_one({"id": s["id"]}, {"$set": {"customer_payment.receipt_email": status}})
    if s.get("merchant_id"):
        await email_store_order_paid(s, store, cp)


async def email_store_order_paid(s: dict, store: str, cp: dict):
    """Copy for the store: their customer paid for this order in the app."""
    html = onboarding.brand_email(f"Customer paid — {money(cp['total'])}",
                                  f"<p>Hi {escape(store)}, <b>{escape(s.get('customer_name') or 'your customer')}</b> paid for order <b>{escape(s['tracking_code'])}</b> "
                                  f"by card in the ArriveWise app on {datetime.now(TZ).strftime('%B %-d, %Y')}.</p>"
                                  + amounts_table([(f"Order {s['tracking_code']}", cp["amount"])], cp["amount"], cp["hst_pct"], cp["hst"], cp["total"], cp.get("tip") or 0)
                                  + f"<p style='color:#5C5C5E;font-size:12px'>Square payment {escape(cp['payment_id'])}. Shown as paid in your Merchant Portal.</p>")
    sent = []
    for to in await merchant_recipients(s["merchant_id"]):
        try:
            await emails.send_email(to=to, subject=f"ArriveWise: order {s['tracking_code']} paid by customer ({money(cp['total'])})", html=html)
            sent.append(to)
        except Exception as e:
            logger.warning(f"Store payment email to {to}: {e}")
    await db.stops.update_one({"id": s["id"]}, {"$set": {"customer_payment.store_email": "sent" if sent else "failed"}})


# ---------------------------------------------------------------- admin
@router.post("/admin/square/run-billing", dependencies=ADMIN_ONLY)
async def admin_run_billing():
    """Run the autopay cycle now (it also runs every 10 minutes)."""
    return {"results": await run_auto_billing()}


@router.get("/admin/square/charges", dependencies=ADMIN_ONLY)
async def admin_charges(status: Optional[str] = None):
    q = {"status": status} if status else {}
    rows = await db.square_charges.find(q, {"_id": 0, "invoice_ids": 0}).sort("created_at", -1).to_list(200)
    return {"configured": configured(), "sandbox": sandbox(), "charges": rows}


# ---------------------------------------------------------------- refunds (admin) + customer payments & tips list
def refunded(p: dict) -> float:
    return round(sum(r["amount"] for r in p.get("refunds") or [] if r.get("status") not in ("FAILED", "REJECTED")), 2)


def customer_payments(s: dict) -> list:
    """Every customer card payment on an order: the order payment (may include a tip) and separate tips."""
    out = []
    cp = s.get("customer_payment") or {}
    if cp.get("payment_id"):
        out.append({"kind": "order", "tip": cp.get("tip") or 0, "driver_id": cp.get("tip_driver_id"), **cp})
    for t in s.get("tips") or []:
        out.append({"kind": "tip", "tip": t["amount"], **t})
    for p in out:
        p["refunded"] = refunded(p)
        p["refundable"] = round(p["total"] - p["refunded"], 2)
    return out


@router.get("/admin/customer-payments", dependencies=ADMIN_ONLY)
async def admin_customer_payments():
    stops = await db.stops.find({"$or": [{"customer_payment.payment_id": {"$exists": True}}, {"tips.0": {"$exists": True}}]}, {"_id": 0}).to_list(2000)
    drivers = {d["id"]: d["name"] for d in await db.drivers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)}
    merchants = {m["id"]: m["name"] for m in await db.merchants.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)}
    rows, tips = [], {}
    for s in stops:
        for p in customer_payments(s):
            row = {k: p.get(k) for k in ("kind", "payment_id", "total", "tip", "refunded", "refundable", "card", "paid_at", "email", "refunds")}
            row.update(stop_id=s["id"], tracking_code=s["tracking_code"], customer_name=s.get("customer_name"), status=s.get("status"),
                       store=merchants.get(s.get("merchant_id")), driver=drivers.get(p.get("driver_id")))
            rows.append(row)
            if p["tip"] and p.get("driver_id"):
                tips.setdefault(p["driver_id"], {"driver": drivers.get(p["driver_id"], p["driver_id"]), "total": 0, "count": 0})
                tips[p["driver_id"]]["total"] = round(tips[p["driver_id"]]["total"] + p["tip"], 2)
                tips[p["driver_id"]]["count"] += 1
    rows.sort(key=lambda r: r["paid_at"] or "", reverse=True)
    return {"payments": rows, "tips_by_driver": sorted(tips.values(), key=lambda t: -t["total"]), "sandbox": sandbox()}


class RefundIn(BaseModel):
    payment_id: str
    amount: float
    reason: str = ""


@router.post("/admin/stops/{stop_id}/refund")
async def refund_payment(stop_id: str, body: RefundIn, u: dict = Depends(admin_user)):
    """Full or partial refund of a customer's Square payment (order or tip) back to their card."""
    s = await db.stops.find_one({"id": stop_id}, {"_id": 0})
    p = next((x for x in customer_payments(s) if x["payment_id"] == body.payment_id), None) if s else None
    if not p:
        raise HTTPException(404, "Payment not found on this order")
    amt = round(body.amount, 2)
    if amt != amt or amt < 0.01:
        raise HTTPException(400, "Enter a refund amount")
    if amt > p["refundable"]:
        raise HTTPException(400, f"You can refund at most {money(p['refundable'])}")
    reason = " ".join(body.reason.split())[:180] or ("Order " + (s.get("status") or "refund"))
    await take_lock(f"refund:{body.payment_id}")
    try:  # re-read under the lock so two quick refunds can't both pass the limit
        p = next((x for x in customer_payments(await db.stops.find_one({"id": stop_id}, {"_id": 0})) if x["payment_id"] == body.payment_id))
        if amt > p["refundable"]:
            raise HTTPException(400, f"You can refund at most {money(p['refundable'])}")
        try:
            r = (await sq("POST", "/refunds", {"idempotency_key": f"rf-{uuid.uuid4().hex}", "payment_id": body.payment_id,
                                               "amount_money": {"amount": cents(amt), "currency": "CAD"}, "reason": reason}))["refund"]
        except SquareError as e:
            raise HTTPException(http_code(e), str(e))
        rec = {"id": r["id"], "amount": amt, "status": r.get("status", "PENDING"), "reason": reason, "at": now_iso(), "by": u.get("email") or "admin"}
        if p["kind"] == "order":
            await db.stops.update_one({"id": stop_id}, {"$push": {"customer_payment.refunds": rec}})
        else:
            await db.stops.update_one({"id": stop_id, "tips.payment_id": body.payment_id}, {"$push": {"tips.$.refunds": rec}})
    finally:
        await db.square_locks.delete_one({"key": f"refund:{body.payment_id}"})
    realtime.spawn(email_refund(s, p, rec))
    return {"ok": True, "refund": rec, "refundable": round(p["refundable"] - amt, 2)}


async def email_refund(s: dict, p: dict, rec: dict):
    if not EMAIL_RE.match(p.get("email") or ""):
        return
    what = "driver tip" if p["kind"] == "tip" else "payment"
    html = onboarding.brand_email(f"Refund on its way — {money(rec['amount'])}",
                                  f"<p>We refunded <b>{money(rec['amount'])}</b> of your {what} for order <b>{escape(s['tracking_code'])}</b> "
                                  f"to {escape(p.get('card') or 'your card')}. Reason: {escape(rec['reason'])}.</p>"
                                  "<p>Refunds usually show on your statement within 5–10 business days.</p>"
                                  f"<p style='color:#5C5C5E;font-size:12px'>Square refund {escape(rec['id'])} · payment {escape(p['payment_id'])}.</p>")
    try:
        await emails.send_email(to=p["email"], subject=f"ArriveWise refund — order {s['tracking_code']} ({money(rec['amount'])})", html=html)
    except Exception as e:
        logger.warning(f"Refund email: {e}")


# ---------------------------------------------------------------- card re-add reminders (after going live)
CARD_LINK_DAYS = 7
CARD_REMIND_COOLDOWN_H = 20


async def card_reminder_targets() -> list:
    out = []
    for m in await db.merchants.find({"disabled": {"$ne": True}, "id": {"$ne": "mer-review"}},  # the Play reviewer store has no real inbox
                                     {"_id": 0, "id": 1, "name": 1, "square": 1, "card_reminded_at": 1}).sort("name", 1).to_list(1000):
        sqd = m.get("square") or {}
        if card_usable(sqd):
            continue
        out.append({"id": m["id"], "name": m["name"], "reason": "re-add" if sqd.get("card_id") else "no-card", "reminded_at": m.get("card_reminded_at")})
    return out


@router.get("/admin/square/card-reminders", dependencies=ADMIN_ONLY)
async def card_reminders_status():
    t = await card_reminder_targets()
    return {"configured": configured(), "sandbox": sandbox(), "stores": t,
            "re_add": sum(1 for x in t if x["reason"] == "re-add"), "no_card": sum(1 for x in t if x["reason"] == "no-card")}


@router.post("/admin/square/card-reminders", dependencies=ADMIN_ONLY)
async def send_card_reminders(request: Request):
    """Email every store without a working card a one-tap link (valid 7 days) to Square's secure card page."""
    if not configured():
        raise HTTPException(503, "Card payments aren't set up yet")
    now = datetime.now(timezone.utc)
    results = []
    for m in await card_reminder_targets():
        if m["reminded_at"] and now - datetime.fromisoformat(m["reminded_at"]) < timedelta(hours=CARD_REMIND_COOLDOWN_H):
            results.append({"id": m["id"], "name": m["name"], "status": "skipped_recent"})
            continue
        to_list = await merchant_recipients(m["id"])
        if not to_list:
            results.append({"id": m["id"], "name": m["name"], "status": "no_email"})
            continue
        link = await page_url(request, page_token("card", m["id"], minutes=CARD_LINK_DAYS * 24 * 60, by="email-link"))
        p = await billing.get_pricing(m["id"])
        hst = (await invoices.get_business())["hst_pct"]
        intro = ("Card payments on ArriveWise are now live. The card you saved earlier was a test card, so autopay is paused until you add your card again."
                 if m["reason"] == "re-add" else "Card payments on ArriveWise are now live. Add a card once and we'll pay your invoices automatically.")
        html = onboarding.brand_email("Add your card to keep autopay on",
                                      f"<p>Hi {escape(m['name'])}, {intro}</p>"
                                      f"<p>We charge your monthly platform fee ({money(p.get('monthly_fee', 0))} + {hst:g}% HST) and, every Monday, the deliveries "
                                      "completed the week before. You get a receipt after every charge and can remove the card anytime in your Merchant Portal.</p>"
                                      f"<p style='color:#5C5C5E;font-size:12px'>The button opens Square's secure card page and works for {CARD_LINK_DAYS} days. "
                                      "Your card number goes straight to Square — we never ask for card details by email.</p>",
                                      ("Add my card", link))
        sent = 0
        for to in to_list:
            try:
                await emails.send_email(to=to, subject=f"Action needed: add your card for ArriveWise autopay — {m['name']}", html=html)
                sent += 1
            except Exception as e:
                logger.warning(f"Card reminder to {to}: {e}")
        if sent:
            await db.merchants.update_one({"id": m["id"]}, {"$set": {"card_reminded_at": now_iso()}})
        results.append({"id": m["id"], "name": m["name"], "status": "sent" if sent else "failed", "emails": sent})
    return {"sent": sum(1 for r in results if r["status"] == "sent"), "results": results}
