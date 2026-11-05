"""Public web pages for the stores: Privacy Policy and Account Deletion (no sign-in needed)."""
from html import escape
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from core import HTML_CSP
from routes import privacy

router = APIRouter(prefix="/api")
UPDATED = "June 2026"

PRIVACY_SECTIONS = [
    ("Who we are", [
        "DashLocal Inc. (\"DashLocal\") provides same-day local delivery for shops and pharmacies in Kitchener-Waterloo, Ontario. This policy explains how we handle personal information under Canada's Personal Information Protection and Electronic Documents Act (PIPEDA) and, for pharmacy deliveries, Ontario's Personal Health Information Protection Act, 2004 (PHIPA).",
        "For pharmacy orders, the pharmacy is the health information custodian. DashLocal acts as its agent and only handles what it needs to complete the delivery."]),
    ("What we collect", [
        "Recipients: name, delivery address, phone number, delivery instructions (for example a gate code or \"leave at side door\"), the delivery time window, and proof of delivery (a doorstep photo or signature, and the time it was delivered). We may also keep a rating and comment if you choose to leave one.",
        "Shops and pharmacies: business name, contact name, work email, phone, store and pickup address, and billing records. Card details are entered into Square's secure form; DashLocal never stores card numbers.",
        "Drivers: name, phone number, vehicle, and location while a route is active. Location is only collected during an active delivery route, including in the background so stops are marked arrived with the phone locked.",
        "Device and app data: a push notification token and crash reports, used only to send delivery alerts and fix problems.",
        "What we do not collect: prescription names, medications, dosages, diagnoses or any other health details. Shops are told never to put these in order notes."]),
    ("Why we collect it", [
        "Only to deliver your order: planning the route, telling the driver where to go, letting you track the delivery, contacting you about it through a masked phone line, proving it was delivered, and billing the shop. We do not sell personal information or use it for advertising."]),
    ("Consent", [
        "Shops confirm the recipient's consent each time they place orders. We record when consent was given and who gave it, alongside the order. Shops, pharmacies and drivers also accept these terms before they use DashLocal, and we record that acceptance with a timestamp.",
        "You can withdraw consent at any time by contacting the shop you ordered from or our Privacy Officer. If you do, we may not be able to complete a delivery."]),
    ("How we store and protect it", [
        "All data travels over encrypted HTTPS connections. Delivery notes, signatures and doorstep photos are also encrypted when they are stored.",
        "Access is limited by role. Drivers see only the recipient's name, street address and delivery notes for their own stops, never phone numbers, prices or invoice items. Shops see only their own orders, and only signed-in DashLocal staff can see everything else.",
        "Calls and texts between drivers and customers go through a masked number, so neither side sees the other's phone number. Every time anyone views or changes personal information, it is recorded in an audit log."]),
    ("How long we keep it", [
        "Personal details on an order are erased automatically after the retention period, which is 2 years by default. That includes the name, phone, address, notes, photo and signature. We keep invoice numbers and amounts for as long as tax law requires, but without the recipient's details."]),
    ("Sharing", [
        "We share information only with the shop that placed the order, the driver assigned to it, and the service providers that run the platform: hosting, Square (payments), Twilio (SMS and masked calls), Firebase/Expo (push notifications) and Resend (email). They are bound to protect it. We disclose information to authorities only when the law requires it."]),
    ("Your rights", [
        "Under PIPEDA and PHIPA you can ask to see the personal information we hold about you, ask us to correct it, ask how it has been used or shared, and withdraw your consent. You can also complain to us, and if you are not satisfied, to the Office of the Privacy Commissioner of Canada (priv.gc.ca) or, for health information, the Information and Privacy Commissioner of Ontario (ipc.on.ca).",
        "If a privacy breach puts you at real risk of significant harm, we will notify you and the relevant commissioner as the law requires."]),
]


def page(title: str, body: str) -> HTMLResponse:
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · DashLocal</title>
<style>body{{margin:0;background:#F5F5F5;font-family:Georgia,'Times New Roman',serif;color:#333335}}
.wrap{{max-width:760px;margin:0 auto;padding:32px 20px}}.logo{{font-family:Arial,sans-serif;font-weight:800;font-size:22px;letter-spacing:.5px}}
.logo span{{color:#D7262E}}h1{{font-family:Arial,sans-serif;font-size:30px;margin:20px 0 4px}}.upd{{color:#77777A;font-size:14px;margin:0 0 16px}}
.card{{background:#fff;border-radius:12px;padding:18px 20px;margin-top:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h2{{font-family:Arial,sans-serif;font-size:18px;margin:0 0 8px}}p,li{{font-size:16px;line-height:1.6;margin:0 0 10px}}
a{{color:#1F5130}}.note{{background:#F1F6F2;border-left:4px solid #1F5130;padding:12px 14px;border-radius:6px}}</style></head>
<body><div class="wrap"><div class="logo"><span>DASH</span>LOCAL</div>{body}
<p class="upd" style="margin-top:24px">DashLocal Inc. · Kitchener-Waterloo, Ontario, Canada</p></div></body></html>"""
    return HTMLResponse(html, headers={"Content-Security-Policy": HTML_CSP, "Cache-Control": "public, max-age=300"})


async def officer() -> tuple:
    o = await privacy.privacy_officer()
    return escape(o["officer_name"]), escape(o["officer_email"])


@router.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_policy_page():
    name, email = await officer()
    cards = "".join(f'<div class="card"><h2>{escape(h)}</h2>{"".join(f"<p>{escape(t)}</p>" for t in ps)}</div>' for h, ps in PRIVACY_SECTIONS)
    cards += ('<div class="card" id="delete"><h2>Deleting your account and data</h2>'
              '<p>Stores and drivers can delete their account inside the app at any time, or from the web. '
              '<a href="delete-account">See how to delete your account</a>.</p></div>'
              f'<div class="card"><h2>Contact</h2><p>{name}, Privacy Officer, DashLocal Inc. · '
              f'<a href="mailto:{email}">{email}</a>. We reply within 30 days.</p></div>')
    return page("Privacy Policy", f'<h1>Privacy Policy</h1><p class="upd">Last updated {UPDATED} · PIPEDA &amp; PHIPA (Ontario)</p>{cards}')


@router.get("/delete-account", response_class=HTMLResponse)
async def delete_account_page():
    name, email = await officer()
    subject = "Delete my DashLocal account"
    body = f"""<h1>Delete your DashLocal account</h1><p class="upd">For the DashLocal app (Google Play and App Store)</p>
<div class="card"><h2>In the app (fastest)</h2><ul>
<li><b>Store / pharmacy:</b> sign in → Store portal → scroll to the bottom → <b>Delete my store account</b> → confirm with your password.</li>
<li><b>Driver:</b> sign in → your route screen → scroll to the bottom → <b>Delete my account</b> → confirm with your PIN.</li></ul></div>
<div class="card"><h2>Without the app</h2><p>Email our Privacy Officer, {name}, at <a href="mailto:{email}?subject={escape(subject)}">{email}</a>
from the email or phone number on your account, with the subject “{escape(subject)}”. We confirm your identity and delete the account within 30 days, then email you a confirmation.</p>
<p>Customers who only received a delivery don't have an account. To have your delivery details erased, email the same address with your tracking code.</p></div>
<div class="card"><h2>What is deleted</h2><ul>
<li>Your sign-in, name, email, phone number, vehicle details, push alerts and saved card are removed right away, and you can no longer sign in.</li></ul>
<h2>What we keep</h2><ul><li>Invoice numbers, amounts and delivery records are kept without your personal details for as long as Canadian tax law requires.</li></ul>
<p class="note">Deletion is paused while a store has unpaid bills or open deliveries, or a driver has an assigned route. The app tells you what to finish first.</p></div>
<div class="card"><p>Read our full <a href="privacy-policy">Privacy Policy</a>.</p></div>"""
    return page("Delete your account", body)
