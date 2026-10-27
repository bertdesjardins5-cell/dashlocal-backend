# Iter48: Receipt archive (law-office store portal: list / single PDF link / ZIP of all) + friendly branded pages for bad/expired download links.
# Creates TEST_ law-office/plain stores and cloned TSRA stops on drv-1's active route; all cleaned up at teardown.
# Run: EXPO_PUBLIC_BACKEND_URL=<public url> python -m pytest tests/test_receipt_archive_linkpages.py -n0
import html as htmllib
import io
import os
import sys
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
from core import issue_token  # noqa: E402

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
ADMIN = (os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
NA = {"X-No-Auto-Auth": "1"}
SIG = "M20 30 L80 90 L140 60 M200 50 L300 150"
JPEG = b"\xff\xd8\xff\xe0" + b"RCPTTEST" * 40 + b"\xff\xd9"
PW = "TestPass2026"


def H(t=None):
    return {"Authorization": f"Bearer {t}", "X-No-Auto-Auth": "1"} if t else dict(NA)


def login(e, p):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": e, "password": p}, headers=NA)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def toks():
    db.login_attempts.delete_many({})
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"}, headers=NA)
    assert r.status_code == 200, r.text
    return {"admin": login(*ADMIN), "drv1": r.json()["token"], "m1": login("bakery@dashlocal.app", "Bakery2026")}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    mer1 = db.merchants.find_one({"id": "mer-1"}, {"_id": 0})
    yield
    for m in db.merchants.find({"name": {"$regex": "^TEST_"}}, {"id": 1}):
        db.users.delete_many({"merchant_id": m["id"]})
        db.invoices.delete_many({"merchant_id": m["id"]})
        db.merchants.delete_one({"id": m["id"]})
    for s in db.stops.find({"tracking_code": {"$regex": "^TSRA"}}, {"id": 1}):
        db.invoices.delete_many({"stop_id": s["id"]})
    db.stops.delete_many({"tracking_code": {"$regex": "^TSRA"}})
    db.merchants.replace_one({"id": "mer-1"}, mer1)
    db.login_attempts.delete_many({})


def new_store(t, btype):
    sfx = uuid.uuid4().hex[:6]
    email = f"test_{btype}_{sfx}@example.com"
    r = requests.post(f"{BASE_URL}/api/admin/merchant-accounts", headers=H(t), json={
        "store_name": f"TEST_{btype}_{sfx}", "email": email, "password": PW, "business_type": btype})
    assert r.status_code == 200, r.text
    return r.json()["id"], email


def clone_stop(merchant_id):
    r = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    src = db.stops.find_one({"route_id": r["id"], "status": "pending", "merchant_id": None, "batch_run": {"$in": [None, False]}}, {"_id": 0}) if r else None
    if not src:
        pytest.skip("need a pending drv-1 stop (POST /api/admin/reset)")
    sid = str(uuid.uuid4())
    db.stops.insert_one({**src, "id": sid, "tracking_code": f"TSRA{uuid.uuid4().hex[:5].upper()}", "merchant_id": merchant_id,
                         "seq": 99, "arrived_at": None, "share_token": None, "receipt_email_at": None, "receipt_email_status": None})
    return sid


def deliver(sid, t, **body):
    r = requests.post(f"{BASE_URL}/api/stops/{sid}/deliver", json=body, headers=H(t))
    assert r.status_code == 200, r.text
    return r


def upload(t):
    r = requests.post(f"{BASE_URL}/api/upload", files={"file": ("pod.jpg", io.BytesIO(JPEG), "image/jpeg")}, headers=H(t))
    assert r.status_code == 200, r.text
    return r.json()["path"]


@pytest.fixture(scope="module")
def law(toks):
    """Law-office store with 3 signed deliveries + 1 unsigned (photo-only) delivery."""
    mid, email = new_store(toks["admin"], "law_office")
    signed = []
    for _ in range(3):
        sid = clone_stop(mid)
        deliver(sid, toks["drv1"], signature=SIG)
        signed.append(sid)
        time.sleep(1.1)
    requests.put(f"{BASE_URL}/api/admin/merchants/{mid}/settings", headers=H(toks["admin"]), json={"signature_required": False})
    unsigned = clone_stop(mid)
    deliver(unsigned, toks["drv1"], photo_path=upload(toks["drv1"]))
    requests.put(f"{BASE_URL}/api/admin/merchants/{mid}/settings", headers=H(toks["admin"]), json={"signature_required": True})
    return {"id": mid, "token": login(email, PW), "signed": signed, "unsigned": unsigned}


@pytest.fixture(scope="module")
def plain(toks):
    """Plain store with one signed delivery (for cross-store checks)."""
    mid, email = new_store(toks["admin"], "store")
    sid = clone_stop(mid)
    deliver(sid, toks["drv1"], signature=SIG)
    return {"id": mid, "token": login(email, PW), "stop": sid}


def link(mid, t, stop_id):
    return requests.post(f"{BASE_URL}/api/merchants/{mid}/receipts/link", json={"stop_id": stop_id}, headers=H(t))


# --- receipt list ---
class TestReceiptList:
    def test_non_law_store_empty(self, toks):
        r = requests.get(f"{BASE_URL}/api/merchants/mer-1/receipts", headers=H(toks["m1"]))
        assert r.status_code == 200, r.text
        assert r.json() == {"law_office": False, "receipts": []}

    def test_law_store_lists_signed_newest_first(self, law):
        r = requests.get(f"{BASE_URL}/api/merchants/{law['id']}/receipts", headers=H(law["token"]))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["law_office"] is True
        ids = [x["id"] for x in d["receipts"]]
        assert ids == list(reversed(law["signed"])), ids
        assert law["unsigned"] not in ids
        x = d["receipts"][0]
        assert set(x) == {"id", "tracking_code", "customer_name", "delivered_at", "matter_ref"}  # iter51: matter_ref added
        assert x["tracking_code"].startswith("TSRA") and x["delivered_at"]

    def test_admin_can_list(self, toks, law):
        r = requests.get(f"{BASE_URL}/api/merchants/{law['id']}/receipts", headers=H(toks["admin"]))
        assert r.status_code == 200 and len(r.json()["receipts"]) == 3

    def test_other_store_forbidden_and_anon_401(self, toks, law):
        assert requests.get(f"{BASE_URL}/api/merchants/{law['id']}/receipts", headers=H(toks["m1"])).status_code == 403
        assert requests.get(f"{BASE_URL}/api/merchants/{law['id']}/receipts", headers=H()).status_code == 401


# --- links + downloads ---
class TestReceiptLinks:
    def test_single_link_downloads_pdf(self, law):
        sid = law["signed"][0]
        r = link(law["id"], law["token"], sid)
        assert r.status_code == 200, r.text
        p = r.json()["path"]
        assert p.startswith(f"/api/stops/{sid}/receipt.pdf?token=")
        g = requests.get(f"{BASE_URL}{p}")
        assert g.status_code == 200 and g.headers["content-type"].startswith("application/pdf") and g.content[:4] == b"%PDF"

    def test_link_rejects_unsigned_other_store_unknown(self, law, plain):
        assert link(law["id"], law["token"], law["unsigned"]).status_code == 404
        assert link(law["id"], law["token"], plain["stop"]).status_code == 404
        assert link(law["id"], law["token"], "nope-" + uuid.uuid4().hex).status_code == 404

    def test_link_non_law_403(self, plain, toks):
        assert link(plain["id"], plain["token"], plain["stop"]).status_code == 403
        assert link("mer-1", toks["m1"], None).status_code == 403

    def test_link_other_store_login_403(self, law, toks):
        assert link(law["id"], toks["m1"], None).status_code == 403
        assert link(law["id"], None, None).status_code == 401

    def test_link_token_5_min(self, law):
        import jwt
        tok = link(law["id"], law["token"], law["signed"][1]).json()["path"].split("token=")[1]
        p = jwt.decode(tok, options={"verify_signature": False})
        assert p["purpose"] == "receipt" and 280 <= p["exp"] - p["iat"] <= 310

    def test_zip_all(self, law):
        r = link(law["id"], law["token"], None)
        assert r.status_code == 200, r.text
        p = r.json()["path"]
        assert p.startswith(f"/api/merchants/{law['id']}/receipts.zip?token=")
        g = requests.get(f"{BASE_URL}{p}")
        assert g.status_code == 200 and g.headers["content-type"].startswith("application/zip"), g.text[:300]
        z = zipfile.ZipFile(io.BytesIO(g.content))
        names = z.namelist()
        assert len(names) == 3 and all(n.endswith(".pdf") for n in names), names
        assert all(z.read(n)[:4] == b"%PDF" for n in names)


# --- friendly link pages ---
def assert_page(r, status, title, button=True):
    assert r.status_code == status, (r.status_code, r.text[:300])
    assert r.headers["content-type"].startswith("text/html"), r.headers["content-type"]
    assert "Content-Security-Policy" in r.headers
    body = htmllib.unescape(r.text)
    assert 'data-testid="link-error-page"' in body
    assert f"<h1>{title}</h1>" in body, body[:600]
    assert "How to get a new one:" in body
    # the Cloudflare edge sometimes injects its own challenge <script> (cdn-cgi) into HTML; CSP blocks it. App must add none.
    import re
    app_body = re.sub(r"<script>\(function\(\)\{function c\(\).*?cdn-cgi.*?</script>", "", body, flags=re.S)
    assert "<script" not in app_body.lower()
    has_btn = "/sign-in/merchant\">Sign in to your store portal</a>" in body
    assert has_btn == button, body[-600:]


def endpoints(law):
    """(url builder, kind purpose, sub, extra) per download endpoint."""
    sid, mid = law["signed"][0], law["id"]
    inv = db.invoices.find_one({}, {"_id": 0, "id": 1})
    return {
        "receipt": (f"/api/stops/{sid}/receipt.pdf", "receipt", sid, {}),
        "receipts": (f"/api/merchants/{mid}/receipts.zip", "receipts", mid, {}),
        "mstatement": ("/api/merchants/mer-1/statements/2026-01.pdf", "mstatement", "mer-1", {"month": "2026-01"}),
        "invoice": (f"/api/invoices/{inv['id']}.pdf", "invoice", inv["id"], {}),
        "report": ("/api/merchants/mer-1/report.csv", "report", "u-x", {"mid": "mer-1", "rng": "7d"}),
    }


class TestLinkPages:
    @pytest.mark.parametrize("kind", ["receipt", "receipts", "mstatement", "invoice", "report"])
    def test_bad_token(self, law, kind):
        url = endpoints(law)[kind][0]
        assert_page(requests.get(f"{BASE_URL}{url}?token=garbage.token.x"), 400, "This link isn't valid")

    @pytest.mark.parametrize("kind", ["receipt", "receipts", "mstatement", "invoice", "report"])
    def test_expired_token(self, law, kind):
        url, purpose, sub, extra = endpoints(law)[kind]
        tok = issue_token(sub, "merchant", purpose=purpose, minutes=-1, **extra)
        assert_page(requests.get(f"{BASE_URL}{url}?token={tok}"), 400, "This link has expired")

    @pytest.mark.parametrize("kind", ["receipt", "receipts", "mstatement", "invoice", "report"])
    def test_wrong_purpose_is_invalid(self, law, kind):
        url, _, sub, extra = endpoints(law)[kind]
        tok = issue_token(sub, "merchant", purpose="session", minutes=5, **extra)
        assert_page(requests.get(f"{BASE_URL}{url}?token={tok}"), 400, "This link isn't valid")

    @pytest.mark.parametrize("kind", ["receipt", "receipts", "mstatement", "invoice", "report"])
    def test_wrong_sub(self, law, kind):
        url, purpose, sub, extra = endpoints(law)[kind]
        extra = {**extra, "mid": "mer-2"} if kind == "report" else extra
        tok = issue_token("other-" + sub if kind != "report" else sub, "merchant", purpose=purpose, minutes=5, **extra)
        assert_page(requests.get(f"{BASE_URL}{url}?token={tok}"), 403, "This link is for something else")

    def test_statement_wrong_month(self):
        tok = issue_token("mer-1", "merchant", purpose="mstatement", minutes=5, month="2025-12")
        assert_page(requests.get(f"{BASE_URL}/api/merchants/mer-1/statements/2026-01.pdf?token={tok}"), 403, "This link is for something else")

    def test_missing(self, law, toks):
        uns = law["unsigned"]
        tok = issue_token(uns, "merchant", purpose="receipt", minutes=5)
        assert_page(requests.get(f"{BASE_URL}/api/stops/{uns}/receipt.pdf?token={tok}"), 404, "We couldn't find this")
        iid = "nope-" + uuid.uuid4().hex
        tok = issue_token(iid, "merchant", purpose="invoice", minutes=5)
        assert_page(requests.get(f"{BASE_URL}/api/invoices/{iid}.pdf?token={tok}"), 404, "We couldn't find this")
        empty, _ = new_store(toks["admin"], "law_office")
        tok = issue_token(empty, "merchant", purpose="receipts", minutes=5)
        assert_page(requests.get(f"{BASE_URL}/api/merchants/{empty}/receipts.zip?token={tok}"), 404, "We couldn't find this")

    def test_valid_tokens_still_download(self, law, toks):
        r = requests.post(f"{BASE_URL}/api/merchants/mer-1/statements/link", json={"month": "2026-01"}, headers=H(toks["m1"]))
        g = requests.get(f"{BASE_URL}{r.json()['path']}")
        assert g.status_code == 200 and g.content[:4] == b"%PDF"
        inv = db.invoices.find_one({"merchant_id": "mer-1"}, {"_id": 0, "id": 1})
        r = requests.post(f"{BASE_URL}/api/merchants/mer-1/invoices/{inv['id']}/link", headers=H(toks["m1"]))
        g = requests.get(f"{BASE_URL}{r.json()['path']}")
        assert g.status_code == 200 and g.content[:4] == b"%PDF"
        r = requests.post(f"{BASE_URL}/api/merchants/mer-1/report-link", json={"range": "7d"}, headers=H(toks["m1"]))
        g = requests.get(f"{BASE_URL}{r.json()['path']}")
        assert g.status_code == 200 and "text/csv" in g.headers["content-type"], g.headers

    def test_proof_unknown_and_expired(self, law):
        assert_page(requests.get(f"{BASE_URL}/api/proof/unknown-{uuid.uuid4().hex}"), 404, "This link isn't valid", button=False)
        sid = law["signed"][2]
        tok = "tsra" + uuid.uuid4().hex
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        db.stops.update_one({"id": sid}, {"$set": {"share_token": tok, "share_created_at": old}})
        assert_page(requests.get(f"{BASE_URL}/api/proof/{tok}"), 410, "This link has expired", button=False)
        db.stops.update_one({"id": sid}, {"$set": {"share_created_at": datetime.now(timezone.utc).isoformat()}})
        g = requests.get(f"{BASE_URL}/api/proof/{tok}")
        assert g.status_code == 200 and "link-error-page" not in g.text and "Proof of delivery" in g.text

    def test_button_points_to_public_base(self):
        base = (db.settings.find_one({"key": "public_base"}) or {}).get("value")
        r = requests.get(f"{BASE_URL}/api/invoices/x.pdf?token=bad")
        assert f'href="{base}/sign-in/merchant"' in r.text
