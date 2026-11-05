from html import escape
from fastapi import Request
from fastapi.responses import HTMLResponse
from core import HTML_CSP, db, decode_token

# ---------------------------------------------------------------- friendly pages for emailed download links
# Links opened straight in a browser (PDFs, CSVs) show a branded page instead of raw JSON when they can't be used.
HOW_TO_GET_NEW = {
    "receipt": "Sign in to your LocalDash store portal and open <b>Signed receipts</b> — every past receipt is there to download any time.",
    "receipts": "Go back to <b>Signed receipts</b> in your store portal and tap <b>Download all</b> again. Download links last 5 minutes.",
    "mstatement": "Sign in to your LocalDash store portal, open <b>Invoices</b> and tap <b>Statement PDF</b> for the month you need.",
    "invoice": "Sign in to your LocalDash store portal and open <b>Invoices</b> to download any invoice, or reply to your invoice email and we'll resend it.",
    "report": "Go back to your store portal and tap <b>Download CSV</b> again. Report links last 5 minutes.",
    "proof": "Ask the store that sent your delivery for a fresh proof-of-delivery link, or open your order in the LocalDash app.",
}


class LinkPageError(Exception):
    def __init__(self, kind: str, reason: str, status: int = 400):
        self.kind, self.reason, self.status = kind, reason, status


def download_payload(token: str, purpose: str, kind: str = None) -> dict:
    try:
        return decode_token(token, purpose)
    except Exception as e:
        raise LinkPageError(kind or purpose, "expired" if "expired" in str(getattr(e, "detail", e)).lower() else "invalid")


TITLES = {"expired": ("This link has expired", "For your security, download links only work for a limited time."),
          "invalid": ("This link isn't valid", "It may have been copied incompletely, or it was replaced by a newer link."),
          "wrong": ("This link is for something else", "It belongs to a different store or document."),
          "missing": ("We couldn't find this", "The item may have been removed under our data retention rules.")}


async def link_error_page(request: Request, exc: LinkPageError):
    base = (await db.settings.find_one({"key": "public_base"}, {"_id": 0}) or {}).get("value") or ""
    title, why = TITLES.get(exc.reason, TITLES["invalid"])
    how = HOW_TO_GET_NEW.get(exc.kind, HOW_TO_GET_NEW["receipt"])
    btn = "" if exc.kind == "proof" or not base else (
        f'<a class="btn" href="{escape(base)}/sign-in/merchant">Sign in to your store portal</a>')
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{escape(title)} · LocalDash</title>
<style>body{{margin:0;background:#F5F5F5;font-family:Georgia,'Times New Roman',serif;color:#333335}}
.wrap{{max-width:520px;margin:0 auto;padding:32px 20px}}.logo{{font-family:Arial,sans-serif;font-weight:800;font-size:22px;letter-spacing:.5px}}
.logo span{{color:#D7262E}}.card{{background:#fff;border-radius:12px;padding:24px;margin-top:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h1{{font-family:Arial,sans-serif;font-size:22px;margin:0 0 8px}}p{{font-size:16px;line-height:1.5;margin:0 0 14px}}
.how{{background:#F1F6F2;border-left:4px solid #1F5130;padding:12px 14px;border-radius:6px}}
.btn{{display:inline-block;background:#1F5130;color:#fff;text-decoration:none;font-family:Arial,sans-serif;font-weight:700;
padding:14px 20px;border-radius:8px;margin-top:6px}}.foot{{font-size:13px;color:#77777A;margin-top:18px}}</style></head>
<body><div class="wrap" data-testid="link-error-page"><div class="logo">ARRIVE<span>WISE</span></div><div class="card">
<h1>{escape(title)}</h1><p>{escape(why)}</p><p class="how"><b>How to get a new one:</b> {how}</p>{btn}
<p class="foot">Need help? Email hello@localdash.ca.</p></div></div></body></html>"""
    return HTMLResponse(html, status_code=exc.status, headers={"Content-Security-Policy": HTML_CSP, "Cache-Control": "no-store"})
