from fastapi import APIRouter, HTTPException, Depends, Request
import os
import ipaddress
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
import asyncio
import httpx
import uuid
import re
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone, timedelta, time as time_cls
from core import TZ, db, issue_token, logger, merchant_access, now_iso
from models import Stop
from routes import auth
from routes import billing
from routes import invoices
from routes import merchants as merchant_routes
from routes import scorecards
from services import realtime
from services import seed

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- weekly merchant report emails (Emergent managed Resend)
EMAIL_BASE_URL = "https://integrations.emergentagent.com"  # constant on purpose: survives deployment
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "LocalDash")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")
TEST_INBOX = "delivered@resend.dev"  # seeded demo store logins are not real inboxes
WEEKLY_SEND_HOUR = 8  # Monday 08:00 depot (Eastern) time
WEEKLY_LINK_DAYS = 7
SEND_NOW_COOLDOWN_MIN = 5

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} ≠ real link host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str) -> str:
    """Returns the provider id; raises RuntimeError with a short reason on failure."""
    _assert_safe_email(subject, html)
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    if EMAIL_REPLY_TO:
        payload["contact_email"] = EMAIL_REPLY_TO
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(f"{EMAIL_BASE_URL}/api/v1/email/send", headers={"X-Email-Key": EMAIL_KEY}, json=payload)
        resp.raise_for_status()
        return resp.json().get("id") or ""
    except httpx.HTTPStatusError as e:
        try:
            j = e.response.json()
        except ValueError:
            j = {}
        if j.get("code") in ("insufficient_credits", "integration_disabled", "daily_limit_reached"):
            logger.warning(f"Email paused: {j.get('code')} ({e.response.status_code}): {j.get('message', '')}")
            raise RuntimeError(f"Email paused: {j.get('code')}")
        logger.error(f"Email send failed: {e.response.status_code} {e.response.text[:300]}")
        raise RuntimeError(f"Email provider error {e.response.status_code}")
    except httpx.HTTPError as e:
        logger.error(f"Email send error: {e}")
        raise RuntimeError("Email service unreachable")


async def weekly_summary(merchant_id: str, since: str, until: str) -> dict:
    rows = [Stop(**d) for d in await db.stops.find({"merchant_id": merchant_id}, {"_id": 0}).to_list(5000)]

    def in_week(st: Stop) -> bool:
        a = st.delivered_at or st.created_at
        return bool(a) and since <= datetime.fromisoformat(a).astimezone(TZ).date().isoformat() <= until

    week = [x for x in rows if in_week(x)]
    delivered = [x for x in week if x.status == "delivered"]
    windowed = [x for x in delivered if x.window_end]
    rated = [x.rating for x in delivered if x.rating]
    return {"orders": len([x for x in week if x.created_at and since <= datetime.fromisoformat(x.created_at).astimezone(TZ).date().isoformat() <= until]),
            "delivered": len(delivered), "failed": sum(1 for x in week if x.status == "failed"),
            "open": sum(1 for x in rows if x.status in ("pending", "arrived")),
            "on_time_rate": round(sum(1 for x in windowed if scorecards.delivered_on_time(x)) / len(windowed) * 100) if windowed else None,
            "avg_rating": scorecards.avg(rated), "complaints": sum(1 for r in rated if r <= 2)}


def cad(n: float) -> str:
    return f"CA${n:,.2f}"


def weekly_bill_html(b: dict, link: str) -> str:
    e = escape
    paid = b.get("payment")
    rows = [("Drops billed", str(b["drops"])), ("Delivery fees", cad(b["base_total"])), ("Distance", cad(b["distance_total"])),
            ("Priority surcharge", cad(b["priority_total"])), (f"Tax {b['tax_pct']}%", cad(b["tax"])),
            ("Total for the week", cad(b["total"]) + (" (paid)" if paid else ""))]
    owed = b.get("outstanding") or {"total": 0, "weeks": []}
    if owed["total"]:
        rows.append((f"Total due ({len(owed['weeks'])} unpaid week{'s' if len(owed['weeks']) != 1 else ''})", cad(owed["total"])))
    trs = "".join(f'<tr><td style="padding:8px 0;border-bottom:1px solid #e5e5e5;color:#555">{e(k)}</td>'
                  f'<td style="padding:8px 0;border-bottom:1px solid #e5e5e5;text-align:right;font-weight:bold">{e(v)}</td></tr>' for k, v in rows)
    return (f'<p style="margin:28px 0 8px;font-size:16px;font-weight:bold">Your statement</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{trs}</table>'
            f'<p style="margin:16px 0 8px"><a href="{e(link, quote=True)}" style="display:inline-block;background:#0a0a0a;color:#ffffff;padding:12px 20px;text-decoration:none;font-weight:bold">Download the statement CSV</a></p>'
            f'<p style="margin:0;color:#777;font-size:12px">Amounts in Canadian dollars. Payment is arranged directly with dispatch — we never ask for card details by email.</p>')


def weekly_invoices_html(b: dict, portal: str) -> str:
    """Weekly email section summarising the per-delivery invoices (fee + HST) and what is still outstanding."""
    e = escape
    return (f'<p style="margin:24px 0 8px;font-size:16px;font-weight:bold">Invoices this week</p>'
            f'<p style="margin:0;color:#555">{b["count"]} invoice{"s" if b["count"] != 1 else ""} · subtotal {e(cad(b["subtotal"]))} · HST {e(cad(b["hst"]))} · '
            f'<b>total {e(cad(b["total"]))}</b></p>'
            f'<p style="margin:8px 0 0;font-weight:bold">Outstanding: {e(cad(b["outstanding"]))} ({b["unpaid_count"]} invoice{"s" if b["unpaid_count"] != 1 else ""})</p>'
            f'<p style="margin:12px 0 0"><a href="{e(portal, quote=True)}" style="color:#e11d1d;font-weight:bold">View and download your invoices</a></p>')


def weekly_email_html(store: str, since: str, until: str, s: dict, link: str, bill: Optional[dict] = None, statement: Optional[str] = None) -> str:
    e = escape
    rng = f"{seed.nice_date(since)} – {seed.nice_date(until)}"
    cells = [("Delivered", s["delivered"]), ("Failed", s["failed"]),
             ("On time", "—" if s["on_time_rate"] is None else f"{s['on_time_rate']}%"),
             ("Avg rating", "—" if s["avg_rating"] is None else f"{s['avg_rating']} / 5"),
             ("Complaints (1–2★)", s["complaints"]), ("New orders", s["orders"]), ("Still open", s["open"])]
    rows = "".join(f'<tr><td style="padding:8px 0;border-bottom:1px solid #e5e5e5;color:#555">{e(k)}</td>'
                   f'<td style="padding:8px 0;border-bottom:1px solid #e5e5e5;text-align:right;font-weight:bold">{e(str(v))}</td></tr>' for k, v in cells)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4"><tr><td align="center" style="padding:24px">'
            f'<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border:2px solid #0a0a0a;font-family:Arial,sans-serif;color:#0a0a0a">'
            f'<tr><td style="background:#0a0a0a;color:#ffffff;padding:20px 24px;font-size:20px;font-weight:bold;letter-spacing:1px">{e(EMAIL_FROM_NAME.upper())} · WEEKLY DELIVERY REPORT</td></tr>'
            f'<tr><td style="padding:24px">'
            f'<p style="margin:0 0 4px;font-size:18px;font-weight:bold">{e(store)}</p>'
            f'<p style="margin:0 0 16px;color:#555">Week of {e(rng)} (Kitchener-Waterloo time)</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'
            f'<p style="margin:24px 0 8px"><a href="{e(link, quote=True)}" style="display:inline-block;background:#e11d1d;color:#ffffff;padding:12px 20px;text-decoration:none;font-weight:bold">Download the full CSV report</a></p>'
            f'<p style="margin:0;color:#777;font-size:12px">The download link works for {WEEKLY_LINK_DAYS} days. It lists every drop with status, times, proof-of-delivery links and ratings.</p>'
            + (weekly_invoices_html(bill, statement) if bill and statement else "") +
            f'</td></tr><tr><td style="padding:16px 24px;border-top:1px solid #e5e5e5;color:#888;font-size:12px">'
            f'Sent by {e(EMAIL_FROM_NAME)} every Monday morning. Turn this email off from the Merchant console. We never ask for your password by email.'
            f'</td></tr></table></td></tr></table>')


async def send_weekly_report(merchant_id: str, trigger: str) -> dict:
    """Email last week's summary + a 7-day CSV link to every login of the store. Logged in db.report_emails."""
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    since, until = merchant_routes.report_window("lastweek")
    log = {"id": str(uuid.uuid4()), "merchant_id": merchant_id, "trigger": trigger, "week_start": since, "week_end": until,
           "created_at": now_iso(), "recipients": [], "status": "sent", "error": None}
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value")
    users = await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0}).to_list(20)
    if not base:
        log.update(status="failed", error="App address unknown yet — sign in once so links can be built")
    elif not users:
        log.update(status="failed", error="Store has no login email")
    else:
        tok = issue_token(f"weekly:{merchant_id}", "merchant", purpose="report", minutes=WEEKLY_LINK_DAYS * 24 * 60, mid=merchant_id, rng="lastweek")
        link = f"{base}/api/merchants/{merchant_id}/report.csv?token={tok}"
        summary = await weekly_summary(merchant_id, since, until)
        week_invs = await db.invoices.find({"merchant_id": merchant_id, "date": {"$gte": since, "$lte": until}}, {"_id": 0}).to_list(2000)
        owed = invoices.invoice_summary(await db.invoices.find({"merchant_id": merchant_id}, {"_id": 0, "status": 1, "total": 1}).to_list(10000))
        bill = {"count": len(week_invs), "subtotal": round(sum(i["subtotal"] for i in week_invs), 2), "hst": round(sum(i["hst"] for i in week_invs), 2),
                "total": round(sum(i["total"] for i in week_invs), 2), "outstanding": owed["outstanding"], "unpaid_count": owed["unpaid_count"]}
        html = weekly_email_html(m["name"], since, until, summary, link, bill, f"{base}/sign-in/merchant")
        subject = f"{m['name']}: weekly delivery report & invoices ({seed.nice_date(since)} – {seed.nice_date(until)}) · {cad(bill['outstanding'])} outstanding"
        log["bill_total"], log["total_due"] = bill["total"], bill["outstanding"]
        errors = []
        for u in users:
            to = TEST_INBOX if u.get("email") in {em for _, em, _ in auth.DEMO_MERCHANT_USERS} else u["email"]
            try:
                eid = await send_email(to=to, subject=subject, html=html)
                log["recipients"].append({"login": u["email"], "to": to, "email_id": eid, "status": "sent"})
            except Exception as ex:
                errors.append(str(ex))
                log["recipients"].append({"login": u["email"], "to": to, "email_id": None, "status": "failed"})
        if errors:
            log.update(status="failed" if len(errors) == len(users) else "partial", error=errors[0])
        log["summary"] = summary
    await db.report_emails.insert_one(dict(log))
    return log


def next_weekly_send(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(TZ)
    d = now.date() + timedelta(days=(7 - now.weekday()) % 7)
    at = datetime.combine(d, time_cls(WEEKLY_SEND_HOUR, 0), TZ)
    return at if at > now else at + timedelta(days=7)


async def weekly_scheduler():
    """Every minute: on Monday from 08:00 Eastern, send each opted-in store's report once per ISO week."""
    while True:
        try:
            now = datetime.now(TZ)
            if now.weekday() == 0 and now.hour >= WEEKLY_SEND_HOUR:
                wk = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
                for m in await db.merchants.find({"weekly_email": {"$ne": False}}, {"_id": 0, "id": 1}).to_list(500):
                    claimed = await db.merchants.update_one({"id": m["id"], "weekly_sent_week": {"$ne": wk}}, {"$set": {"weekly_sent_week": wk}})
                    if claimed.modified_count:
                        log = await send_weekly_report(m["id"], "scheduled")
                        tries = await db.report_emails.count_documents({"merchant_id": m["id"], "trigger": "scheduled", "week_start": log["week_start"]})
                        if log["status"] == "failed" and tries < 3:  # retry next minute, at most 3 tries a week
                            await db.merchants.update_one({"id": m["id"]}, {"$unset": {"weekly_sent_week": ""}})
                for m in await db.merchants.find({"pharmacy_partner": True}, {"_id": 0, "id": 1}).to_list(500):
                    claimed = await db.merchants.update_one({"id": m["id"], "pharmacy_summary_week": {"$ne": wk}}, {"$set": {"pharmacy_summary_week": wk}})
                    if claimed.modified_count:
                        realtime.spawn(billing.email_pharmacy_summary(m["id"], (now.date() - timedelta(days=7)).isoformat()))
        except Exception as e:
            logger.error(f"Weekly report scheduler error: {e}")
        await asyncio.sleep(60)


class WeeklyEmailIn(BaseModel):
    enabled: bool


async def weekly_email_status(merchant_id: str) -> dict:
    m = await merchant_routes.get_merchant_or_404(merchant_id)
    users = await db.users.find({"merchant_id": merchant_id, "role": "merchant", "disabled": {"$ne": True}}, {"_id": 0, "email": 1}).to_list(20)
    last = await db.report_emails.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).to_list(1)
    return {"enabled": m.get("weekly_email", True), "recipients": [u["email"] for u in users],
            "next_send": next_weekly_send().isoformat(), "last": last[0] if last else None}


@router.get("/merchants/{merchant_id}/weekly-email")
async def get_weekly_email(merchant_id: str, _: dict = Depends(merchant_access)):
    return await weekly_email_status(merchant_id)


@router.put("/merchants/{merchant_id}/weekly-email")
async def set_weekly_email(merchant_id: str, body: WeeklyEmailIn, _: dict = Depends(merchant_access)):
    await merchant_routes.get_merchant_or_404(merchant_id)
    await db.merchants.update_one({"id": merchant_id}, {"$set": {"weekly_email": body.enabled}})
    return await weekly_email_status(merchant_id)


@router.post("/merchants/{merchant_id}/weekly-email/send-now")
async def send_weekly_now(merchant_id: str, request: Request, _: dict = Depends(merchant_access)):
    """Send last week's report right away to the store's own login emails (rate-limited)."""
    await merchant_routes.remember_public_base(request)
    recent = await db.report_emails.find_one({"merchant_id": merchant_id, "trigger": "manual", "status": {"$ne": "failed"},
                                              "created_at": {"$gt": (datetime.now(timezone.utc) - timedelta(minutes=SEND_NOW_COOLDOWN_MIN)).isoformat()}})
    if recent:
        raise HTTPException(429, f"A report was just sent — try again in a few minutes")
    log = await send_weekly_report(merchant_id, "manual")
    return {**(await weekly_email_status(merchant_id)), "sent": log}
