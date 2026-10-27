# Iter46: driver welcome text (invite), store business_type + signature-at-door setting, deliver enforcement.
# Creates TEST_ drivers/merchants and cloned TEST stops on drv-1's active route; all cleaned up at module teardown.
import io
import os
import uuid
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
ADMIN = (os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
NA = {"X-No-Auto-Auth": "1"}
JPEG = b"\xff\xd8\xff\xe0" + b"SIGREQTEST" * 40 + b"\xff\xd9"
SIG = "M10 10 L60 40"
SIG_MSG = "This store needs the recipient's signature at the door"
TYPES = ["store", "law_office", "flower_shop", "pharmacy", "other"]


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
    return {"admin": _tok(lg(*ADMIN)), "drv1": _tok(dl("+15195550101", "482913")), "m1": _tok(lg("bakery@dashlocal.app", "Bakery2026"))}


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
    for s in db.stops.find({"tracking_code": {"$regex": "^TSIG"}}, {"id": 1}):
        db.invoices.delete_many({"stop_id": s["id"]})
    db.stops.delete_many({"tracking_code": {"$regex": "^TSIG"}})
    db.signup_requests.delete_many({"store_name": {"$regex": "^TEST_"}})
    db.merchants.replace_one({"id": "mer-1"}, mer1)  # restore mer-1 exactly


def new_store(t, btype, suffix=None):
    suffix = suffix or uuid.uuid4().hex[:6]
    return requests.post(f"{BASE_URL}/api/admin/merchant-accounts", headers=H(t), json={
        "store_name": f"TEST_{btype}_{suffix}", "email": f"test_{btype}_{suffix}@example.com", "password": "TestPass2026", "business_type": btype})


def clone_stop(merchant_id):
    r = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    src = db.stops.find_one({"route_id": r["id"], "status": "pending", "merchant_id": None, "batch_run": {"$in": [None, False]}}, {"_id": 0}) if r else None
    if not src:
        pytest.skip("need a pending drv-1 stop (POST /api/admin/reset)")
    sid = str(uuid.uuid4())
    db.stops.insert_one({**src, "id": sid, "tracking_code": f"TSIG{uuid.uuid4().hex[:5].upper()}", "merchant_id": merchant_id,
                         "seq": 99, "arrived_at": None, "share_token": None})
    return sid


def upload(t):
    r = requests.post(f"{BASE_URL}/api/upload", files={"file": ("pod.jpg", io.BytesIO(JPEG), "image/jpeg")}, headers=H(t))
    assert r.status_code == 200, r.text
    return r.json()["path"]


def deliver(sid, t, **body):
    return requests.post(f"{BASE_URL}/api/stops/{sid}/deliver", json=body, headers=H(t))


# --- driver welcome text ---
class TestDriverInvite:
    def test_create_driver_invite_sent(self, toks):
        phone = f"+1519555{uuid.uuid4().int % 90 + 110:04d}"
        db.drivers.delete_many({"phone": phone, "name": {"$regex": "^TEST_"}})
        r = requests.post(f"{BASE_URL}/api/admin/drivers", headers=H(toks["admin"]),
                          json={"name": "TEST_Invite Driver", "phone": phone, "vehicle": "Van", "pin": "583920"})
        if r.status_code == 409:
            pytest.skip("phone collision")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["invite"] in ("sent", "not_configured", "failed")
        assert d["invite"] == "sent", f"invite={d['invite']}"
        doc = db.drivers.find_one({"id": d["id"]})
        assert doc["invite_status"] == d["invite"] and doc.get("invite_sent_at")
        # driver can log in with the PIN from the text
        lr = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": phone, "pin": "583920"}, headers=NA)
        assert lr.status_code == 200, lr.text

    def test_create_driver_needs_admin(self, toks):
        for t in (toks["m1"], toks["drv1"]):
            r = requests.post(f"{BASE_URL}/api/admin/drivers", headers=H(t),
                              json={"name": "TEST_No", "phone": "+15195550188", "vehicle": "Van", "pin": "583920"})
            assert r.status_code in (401, 403)
        assert not db.drivers.find_one({"name": "TEST_No"})


# --- business type on new stores ---
class TestBusinessType:
    @pytest.mark.parametrize("btype", TYPES)
    def test_create_store_types(self, toks, btype):
        r = new_store(toks["admin"], btype)
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["business_type"] == btype and m["signature_required"] is (btype == "law_office")
        doc = db.merchants.find_one({"id": m["id"]}, {"_id": 0})
        assert doc["business_type"] == btype and doc["signature_required"] is (btype == "law_office")

    def test_create_store_bad_type(self, toks):
        r = new_store(toks["admin"], "bank", "bad")
        assert r.status_code == 400
        assert not db.merchants.find_one({"name": "TEST_bank_bad"})
        assert not db.users.find_one({"email": "test_bank_bad@example.com"})

    def test_default_type_store(self, toks):
        s = uuid.uuid4().hex[:6]
        r = requests.post(f"{BASE_URL}/api/admin/merchant-accounts", headers=H(toks["admin"]),
                          json={"store_name": f"TEST_default_{s}", "email": f"test_default_{s}@example.com", "password": "TestPass2026"})
        assert r.status_code == 200 and r.json()["business_type"] == "store" and r.json()["signature_required"] is False

    def test_list_includes_fields(self, toks):
        r = requests.get(f"{BASE_URL}/api/admin/merchant-accounts", headers=H(toks["admin"]))
        assert r.status_code == 200
        law = [m for m in r.json() if m["name"].startswith("TEST_law_office")]
        assert law and law[0]["signature_required"] is True and law[0]["business_type"] == "law_office"

    def test_approve_law_office_signup(self, toks):
        s = uuid.uuid4().hex[:6]
        sid = str(uuid.uuid4())
        db.signup_requests.insert_one({"id": sid, "store_name": f"TEST_LawSignup_{s}", "contact_name": "TEST Lawyer", "email": f"test_law_{s}@example.com",
                                       "phone": "+15195550177", "address": "1 King St W, Kitchener", "message": "", "status": "pending",
                                       "business_type": "law_office", "terms_consent": True, "created_at": "2026-01-01T00:00:00+00:00"})
        r = requests.post(f"{BASE_URL}/api/admin/signup-requests/{sid}/approve", headers=H(toks["admin"]))
        m = db.merchants.find_one({"name": f"TEST_LawSignup_{s}"}, {"_id": 0})
        assert m, f"merchant not created: {r.status_code} {r.text}"
        assert m["business_type"] == "law_office" and m["signature_required"] is True


# --- admin settings endpoint ---
class TestMerchantSettings:
    @pytest.fixture(scope="class")
    def store(self, toks):
        r = new_store(toks["admin"], "store")
        assert r.status_code == 200
        return r.json()["id"]

    def test_update_type_and_signature(self, toks, store):
        r = requests.put(f"{BASE_URL}/api/admin/merchants/{store}/settings", headers=H(toks["admin"]),
                         json={"business_type": "flower_shop", "signature_required": True})
        assert r.status_code == 200, r.text
        assert r.json()["business_type"] == "flower_shop" and r.json()["signature_required"] is True and "_id" not in r.json()
        doc = db.merchants.find_one({"id": store})
        assert doc["business_type"] == "flower_shop" and doc["signature_required"] is True

    def test_signature_only(self, toks, store):
        r = requests.put(f"{BASE_URL}/api/admin/merchants/{store}/settings", headers=H(toks["admin"]), json={"signature_required": False})
        assert r.status_code == 200 and db.merchants.find_one({"id": store})["signature_required"] is False
        assert db.merchants.find_one({"id": store})["business_type"] == "flower_shop"

    @pytest.mark.parametrize("body", [{}, {"business_type": "bank"}])
    def test_bad_body(self, toks, store, body):
        r = requests.put(f"{BASE_URL}/api/admin/merchants/{store}/settings", headers=H(toks["admin"]), json=body)
        assert r.status_code == 400

    def test_unknown_store(self, toks):
        r = requests.put(f"{BASE_URL}/api/admin/merchants/mer-nope/settings", headers=H(toks["admin"]), json={"signature_required": True})
        assert r.status_code == 404

    def test_non_admin(self, toks, store):
        for h in (H(toks["m1"]), H(toks["drv1"]), NA):
            r = requests.put(f"{BASE_URL}/api/admin/merchants/{store}/settings", headers=h, json={"signature_required": True})
            assert r.status_code in (401, 403)
        assert db.merchants.find_one({"id": store})["signature_required"] is False


# --- deliver enforcement ---
class TestSignatureAtDoor:
    @pytest.fixture(scope="class")
    def law(self, toks):
        r = new_store(toks["admin"], "law_office")
        assert r.status_code == 200
        return r.json()["id"]

    def test_stop_get_flag(self, toks, law):
        sid = clone_stop(law)
        r = requests.get(f"{BASE_URL}/api/stops/{sid}", headers=H(toks["drv1"]))
        assert r.status_code == 200, r.text
        assert r.json()["signature_required"] is True
        plain = clone_stop(None)
        assert requests.get(f"{BASE_URL}/api/stops/{plain}", headers=H(toks["drv1"])).json()["signature_required"] is False

    def test_photo_only_rejected_then_signature_ok(self, toks, law):
        sid = clone_stop(law)
        r = deliver(sid, toks["drv1"], photo_path=upload(toks["drv1"]))
        assert r.status_code == 400 and r.json()["detail"] == SIG_MSG
        assert db.stops.find_one({"id": sid})["status"] == "pending"
        r = deliver(sid, toks["drv1"], photo_path=upload(toks["drv1"]), signature=SIG)
        assert r.status_code == 200, r.text
        assert db.stops.find_one({"id": sid})["status"] == "delivered"

    def test_signature_only_ok(self, toks, law):
        sid = clone_stop(law)
        assert deliver(sid, toks["drv1"], signature=SIG).status_code == 200

    def test_bad_signature_rejected(self, toks, law):
        sid = clone_stop(law)
        r = deliver(sid, toks["drv1"], photo_path=upload(toks["drv1"]), signature="hello")
        assert r.status_code == 400

    def test_switched_off_photo_ok(self, toks, law):
        assert requests.put(f"{BASE_URL}/api/admin/merchants/{law}/settings", headers=H(toks["admin"]),
                            json={"signature_required": False}).status_code == 200
        sid = clone_stop(law)
        assert requests.get(f"{BASE_URL}/api/stops/{sid}", headers=H(toks["drv1"])).json()["signature_required"] is False
        r = deliver(sid, toks["drv1"], photo_path=upload(toks["drv1"]))
        assert r.status_code == 200, r.text

    def test_pharmacy_rule_unchanged(self, toks):
        sid = clone_stop("mer-2")  # pharmacy partner
        r = deliver(sid, toks["drv1"], signature=SIG)
        assert r.status_code == 400 and "Prescription" in r.json()["detail"]
        r = deliver(sid, toks["drv1"], photo_path=upload(toks["drv1"]))
        assert r.status_code == 400 and "Prescription" in r.json()["detail"]
        assert deliver(sid, toks["drv1"], photo_path=upload(toks["drv1"]), signature=SIG).status_code == 200
