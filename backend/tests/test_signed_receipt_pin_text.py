# Iter47: signed delivery receipt (law offices, emailed PDF link) + admin one-tap "new PIN by text".
# Creates TEST_ law-office/plain stores, cloned TSRC stops on drv-1's active route and TEST_ drivers; all cleaned up at teardown.
import io
import os
import sys
import time
import uuid
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

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


def H(t):
    return {"Authorization": f"Bearer {t}", "X-No-Auto-Auth": "1"}


def _tok(r):
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def toks():
    db.login_attempts.delete_many({})
    lg = lambda e, p: requests.post(f"{BASE_URL}/api/auth/login", json={"email": e, "password": p}, headers=NA)
    dl = lambda ph, pin: requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": ph, "pin": pin}, headers=NA)
    return {"admin": _tok(lg(*ADMIN)), "drv1": _tok(dl("+15195550101", "482913")), "m1": _tok(lg("bakery@arrivewise.app", "Bakery2026"))}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    mer1 = db.merchants.find_one({"id": "mer-1"}, {"_id": 0})
    yield
    for m in db.merchants.find({"name": {"$regex": "^TEST_"}}, {"id": 1}):
        db.users.delete_many({"merchant_id": m["id"]})
        db.invoices.delete_many({"merchant_id": m["id"]})
        db.merchants.delete_one({"id": m["id"]})
    for d in db.drivers.find({"name": {"$regex": "^TEST_"}}, {"id": 1}):
        db.users.delete_many({"driver_id": d["id"]})
        db.drivers.delete_one({"id": d["id"]})
    for s in db.stops.find({"tracking_code": {"$regex": "^TSRC"}}, {"id": 1}):
        db.invoices.delete_many({"stop_id": s["id"]})
    db.stops.delete_many({"tracking_code": {"$regex": "^TSRC"}})
    db.merchants.replace_one({"id": "mer-1"}, mer1)
    db.login_attempts.delete_many({})


def new_store(t, btype):
    sfx = uuid.uuid4().hex[:6]
    r = requests.post(f"{BASE_URL}/api/admin/merchant-accounts", headers=H(t), json={
        "store_name": f"TEST_{btype}_{sfx}", "email": f"test_{btype}_{sfx}@example.com", "password": "TestPass2026", "business_type": btype})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def clone_stop(merchant_id):
    r = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    src = db.stops.find_one({"route_id": r["id"], "status": "pending", "merchant_id": None, "batch_run": {"$in": [None, False]}}, {"_id": 0}) if r else None
    if not src:
        pytest.skip("need a pending drv-1 stop (POST /api/admin/reset)")
    sid = str(uuid.uuid4())
    db.stops.insert_one({**src, "id": sid, "tracking_code": f"TSRC{uuid.uuid4().hex[:5].upper()}", "merchant_id": merchant_id,
                         "seq": 99, "arrived_at": None, "share_token": None, "receipt_email_at": None, "receipt_email_status": None})
    return sid


def deliver(sid, t, **body):
    return requests.post(f"{BASE_URL}/api/stops/{sid}/deliver", json=body, headers=H(t))


def wait_receipt(sid, secs=20):
    for _ in range(secs * 2):
        s = db.stops.find_one({"id": sid}, {"_id": 0})
        if s.get("receipt_email_status"):
            return s
        time.sleep(0.5)
    return db.stops.find_one({"id": sid}, {"_id": 0})


def upload(t):
    r = requests.post(f"{BASE_URL}/api/upload", files={"file": ("pod.jpg", io.BytesIO(JPEG), "image/jpeg")}, headers=H(t))
    assert r.status_code == 200, r.text
    return r.json()["path"]


@pytest.fixture(scope="module")
def law(toks):
    return new_store(toks["admin"], "law_office")


# --- signed receipt email trigger ---
class TestReceiptTrigger:
    def test_law_office_signed_delivery_sets_receipt(self, toks, law):
        sid = clone_stop(law)
        r = deliver(sid, toks["drv1"], signature=SIG)
        assert r.status_code == 200, r.text
        s = wait_receipt(sid)
        assert s.get("receipt_email_at"), s
        assert s.get("receipt_email_status") in ("sent", "failed"), s.get("receipt_email_status")
        print("TEST law store receipt status:", s.get("receipt_email_status"))

    def test_mer1_as_law_office_sends_to_test_inbox(self, toks):
        r = requests.put(f"{BASE_URL}/api/admin/merchants/mer-1/settings", headers=H(toks["admin"]), json={"business_type": "law_office"})
        assert r.status_code == 200 and r.json()["signature_required"] is True
        sid = clone_stop("mer-1")
        assert deliver(sid, toks["drv1"], signature=SIG).status_code == 200
        s = wait_receipt(sid)
        print("mer-1 receipt status:", s.get("receipt_email_status"))
        assert s.get("receipt_email_status") == "sent", s.get("receipt_email_status")

    def test_no_signature_no_receipt(self, toks):
        m = new_store(toks["admin"], "law_office")
        requests.put(f"{BASE_URL}/api/admin/merchants/{m}/settings", headers=H(toks["admin"]), json={"signature_required": False})
        sid = clone_stop(m)
        r = deliver(sid, toks["drv1"], photo_path=upload(toks["drv1"]))
        assert r.status_code == 200, r.text
        time.sleep(4)
        s = db.stops.find_one({"id": sid}, {"_id": 0})
        assert not s.get("receipt_email_at") and not s.get("receipt_email_status")

    def test_non_law_store_no_receipt(self, toks):
        m = new_store(toks["admin"], "store")
        sid = clone_stop(m)
        assert deliver(sid, toks["drv1"], signature=SIG).status_code == 200
        time.sleep(4)
        s = db.stops.find_one({"id": sid}, {"_id": 0})
        assert not s.get("receipt_email_at") and not s.get("receipt_email_status")

    def test_no_logins_status_no_recipient(self, toks, law):
        m = new_store(toks["admin"], "law_office")
        db.users.update_many({"merchant_id": m}, {"$set": {"disabled": True}})
        sid = clone_stop(m)
        assert deliver(sid, toks["drv1"], signature=SIG).status_code == 200
        s = wait_receipt(sid)
        assert s.get("receipt_email_status") == "no_recipient", s.get("receipt_email_status")

    def test_never_sent_twice(self, toks, law):
        sid = clone_stop(law)
        assert deliver(sid, toks["drv1"], signature=SIG).status_code == 200
        first = wait_receipt(sid)["receipt_email_at"]
        import asyncio
        from routes import receipts
        asyncio.run(receipts.send_signed_receipt(sid))  # second run must be a no-op
        assert db.stops.find_one({"id": sid})["receipt_email_at"] == first


# --- PDF download ---
class TestReceiptPdf:
    @pytest.fixture(scope="class")
    def done(self, toks, law):
        sid = clone_stop(law)
        assert deliver(sid, toks["drv1"], signature=SIG).status_code == 200
        return db.stops.find_one({"id": sid}, {"_id": 0})

    def test_valid_token_returns_pdf(self, done):
        from pypdf import PdfReader
        tok = issue_token(done["id"], "merchant", purpose="receipt", minutes=60)
        r = requests.get(f"{BASE_URL}/api/stops/{done['id']}/receipt.pdf", params={"token": tok}, headers=NA)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/pdf") and r.content[:4] == b"%PDF"
        assert "attachment" in r.headers.get("content-disposition", "")
        rd = PdfReader(io.BytesIO(r.content))
        text = rd.pages[0].extract_text()
        for needle in (done["tracking_code"], done["customer_name"], "SIGNED DELIVERY RECEIPT", "Delivered at", "Eastern"):
            assert needle in text, needle
        assert done["address"].split(",")[0] in text
        stream = rd.pages[0].get_contents().get_data().decode("latin-1")
        assert stream.count(" l\n") + stream.count(" l ") >= 3  # polyline segments of the drawn signature

    def test_other_stop_token_403(self, done):
        tok = issue_token("some-other-stop", "merchant", purpose="receipt", minutes=60)
        r = requests.get(f"{BASE_URL}/api/stops/{done['id']}/receipt.pdf", params={"token": tok}, headers=NA)
        assert r.status_code == 403

    def test_garbage_token_400(self, done):
        r = requests.get(f"{BASE_URL}/api/stops/{done['id']}/receipt.pdf", params={"token": "garbage"}, headers=NA)
        assert r.status_code == 400

    def test_expired_token_400(self, done):
        tok = issue_token(done["id"], "merchant", purpose="receipt", minutes=-5)
        r = requests.get(f"{BASE_URL}/api/stops/{done['id']}/receipt.pdf", params={"token": tok}, headers=NA)
        assert r.status_code == 400 and "This link has expired" in r.text  # friendly branded page

    def test_session_token_wrong_purpose_400(self, done, toks):
        r = requests.get(f"{BASE_URL}/api/stops/{done['id']}/receipt.pdf", params={"token": toks["m1"]}, headers=NA)
        assert r.status_code == 400

    def test_missing_token_friendly_page(self, done):
        r = requests.get(f"{BASE_URL}/api/stops/{done['id']}/receipt.pdf", headers=NA)
        assert r.status_code == 400 and "link-error-page" in r.text


# --- admin one-tap new PIN by text ---
class TestPinText:
    @pytest.fixture(scope="class")
    def drv(self, toks):
        phone = f"+1519555{uuid.uuid4().int % 9000 + 1000}"
        r = requests.post(f"{BASE_URL}/api/admin/drivers", headers=H(toks["admin"]),
                          json={"name": "TEST_PinText Driver", "phone": phone, "vehicle": "Van", "pin": "583920"})
        assert r.status_code == 200, r.text
        return {**r.json(), "phone": r.json()["phone"]}

    def test_refused_without_admin(self, toks, drv):
        u = f"{BASE_URL}/api/admin/drivers/{drv['id']}/pin/text"
        assert requests.post(u, headers=NA).status_code in (401, 403)
        assert requests.post(u, headers=H(toks["m1"])).status_code == 403
        assert requests.post(u, headers=H(toks["drv1"])).status_code == 403

    def test_unknown_driver_404(self, toks):
        assert requests.post(f"{BASE_URL}/api/admin/drivers/drv-nope/pin/text", headers=H(toks["admin"])).status_code == 404

    def test_reset_and_text(self, toks, drv):
        old_hash = db.users.find_one({"driver_id": drv["id"]})["pin_hash"]
        r = requests.post(f"{BASE_URL}/api/admin/drivers/{drv['id']}/pin/text", headers=H(toks["admin"]))
        assert r.status_code == 200, r.text
        j = r.json()
        print("pin/text response:", {**j, "pin": "***" if j.get("pin") else None})
        assert j["ok"] is True and j["invite"] in ("sent", "not_configured", "failed")
        if j["invite"] == "sent":
            assert j["pin"] is None
        else:
            assert isinstance(j["pin"], str) and len(j["pin"]) == 6 and j["pin"].isdigit()
        assert db.users.find_one({"driver_id": drv["id"]})["pin_hash"] != old_hash
        assert db.drivers.find_one({"id": drv["id"]})["invite_status"] == j["invite"]
        db.login_attempts.delete_many({})
        old = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": drv["phone"], "pin": "583920"}, headers=NA)
        assert old.status_code == 401, old.text
        if j["pin"]:
            new = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": drv["phone"], "pin": j["pin"]}, headers=NA)
            assert new.status_code == 200, new.text
        db.login_attempts.delete_many({})

    def test_two_resets_give_different_pins(self, toks, drv):
        h1 = db.users.find_one({"driver_id": drv["id"]})["pin_hash"]
        r = requests.post(f"{BASE_URL}/api/admin/drivers/{drv['id']}/pin/text", headers=H(toks["admin"]))
        assert r.status_code == 429  # 60s cooldown right after the previous text
        db.drivers.update_one({"id": drv["id"]}, {"$unset": {"pin_texted_at": ""}})
        r = requests.post(f"{BASE_URL}/api/admin/drivers/{drv['id']}/pin/text", headers=H(toks["admin"]))
        assert r.status_code == 200
        assert db.users.find_one({"driver_id": drv["id"]})["pin_hash"] != h1

    def test_manual_reset_still_works(self, toks, drv):
        r = requests.put(f"{BASE_URL}/api/admin/drivers/{drv['id']}/pin", headers=H(toks["admin"]), json={"pin": "617294"})
        assert r.status_code == 200
        db.login_attempts.delete_many({})
        ok = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": drv["phone"], "pin": "617294"}, headers=NA)
        assert ok.status_code == 200, ok.text
