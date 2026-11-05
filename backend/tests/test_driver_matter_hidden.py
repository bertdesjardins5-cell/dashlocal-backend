# Iter54: matter_ref (law-office client file no.) is hidden from every driver stop view (dispatch.DRIVER_HIDDEN)
# but still visible to the merchant + admin. Clones one TSDH stop onto drv-1's active route; mer-1 is temporarily a
# law_office and is restored to {business_type:'store', signature_required:false} at teardown.
# Run: python -m pytest tests/test_driver_matter_hidden.py -n0
import os
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / "frontend" / ".env")

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
NA = {"X-No-Auto-Auth": "1"}
REF = "TEST-MATTER-2026/0142"


def H(t=None):
    return {"Authorization": f"Bearer {t}", "X-No-Auto-Auth": "1"} if t else dict(NA)


def login(e, p):
    r = requests.post(f"{API}/auth/login", json={"email": e, "password": p}, headers=NA)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def toks():
    db.login_attempts.delete_many({})
    r = requests.post(f"{API}/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"}, headers=NA)
    assert r.status_code == 200, r.text
    return {"admin": login(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"]), "drv1": r.json()["token"],
            "m1": login("bakery@localdash.app", "Bakery2026")}


@pytest.fixture(scope="module")
def setup(toks):
    route = db.routes.find_one({"driver_id": "drv-1", "status": "active"}, {"_id": 0})
    src = db.stops.find_one({"route_id": route["id"], "status": "pending"}, {"_id": 0}) if route else None
    if not src:
        pytest.skip("need an active drv-1 route with a pending stop (POST /api/admin/reset)")
    db.merchants.update_one({"id": "mer-1"}, {"$set": {"business_type": "law_office"}})
    sid = str(uuid.uuid4())
    code = f"TSDH{uuid.uuid4().hex[:5].upper()}"
    db.stops.insert_one({**src, "id": sid, "tracking_code": code, "merchant_id": "mer-1", "seq": 99, "arrived_at": None,
                         "share_token": None, "receipt_email_at": None, "receipt_email_status": None})
    r = requests.put(f"{API}/merchants/mer-1/stops/{sid}/matter", headers=H(toks["m1"]), json={"matter_ref": REF})
    assert r.status_code == 200 and r.json()["matter_ref"] == REF, r.text
    yield {"sid": sid, "rid": route["id"], "code": code}
    db.stops.delete_many({"tracking_code": {"$regex": "^TSDH"}})
    db.invoices.delete_many({"stop_id": sid})
    db.merchants.update_one({"id": "mer-1"}, {"$set": {"business_type": "store", "signature_required": False}})
    db.login_attempts.delete_many({})


# --- driver views must not carry matter_ref ---
class TestDriverHidden:
    def test_driver_route_manifest(self, toks, setup):
        r = requests.get(f"{API}/drivers/drv-1/route", headers=H(toks["drv1"]))
        assert r.status_code == 200, r.text
        stops = r.json()["route"]["stops"]
        mine = next((s for s in stops if s["id"] == setup["sid"]), None)
        assert mine is not None, "cloned stop not on manifest"
        assert all("matter_ref" not in s for s in stops)
        assert "customer_phone" not in mine and "fee" not in mine
        assert REF not in r.text

    def test_driver_route_by_id(self, toks, setup):
        r = requests.get(f"{API}/routes/{setup['rid']}", headers=H(toks["drv1"]))
        assert r.status_code == 200, r.text
        assert all("matter_ref" not in s for s in r.json()["stops"]) and REF not in r.text

    def test_driver_stop_detail(self, toks, setup):
        r = requests.get(f"{API}/stops/{setup['sid']}", headers=H(toks["drv1"]))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == setup["sid"] and "matter_ref" not in r.json() and REF not in r.text

    def test_driver_verify_qr_response(self, toks, setup):
        r = requests.post(f"{API}/stops/{setup['sid']}/verify-qr", headers=H(toks["drv1"]), json={"code": setup["code"]})
        assert r.status_code == 200, r.text
        assert r.json()["pod_qr_verified"] is True and "matter_ref" not in r.json()

    def test_driver_arrive_response(self, toks, setup):
        r = requests.post(f"{API}/stops/{setup['sid']}/arrive", headers=H(toks["drv1"]))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "arrived" and "matter_ref" not in r.json()

    @pytest.mark.parametrize("path", ["summary", "scorecard", "feedback", "coaching"])
    def test_other_driver_endpoints_no_ref(self, toks, setup, path):
        r = requests.get(f"{API}/drivers/drv-1/{path}", headers=H(toks["drv1"]))
        assert r.status_code == 200, r.text
        assert REF not in r.text and "matter_ref" not in r.text


# --- merchant + admin still see it ---
class TestMerchantAdminVisible:
    def test_db_persisted(self, setup):
        assert db.stops.find_one({"id": setup["sid"]})["matter_ref"] == REF

    def test_merchant_portal_orders(self, toks, setup):
        d = requests.get(f"{API}/merchants/mer-1", headers=H(toks["m1"])).json()
        o = next((x for x in d["orders"] if x["id"] == setup["sid"]), None)
        assert o is not None and o.get("matter_ref") == REF, o

    def test_admin_stop_and_route(self, toks, setup):
        r = requests.get(f"{API}/stops/{setup['sid']}", headers=H(toks["admin"]))
        assert r.status_code == 200 and r.json().get("matter_ref") == REF, r.text
        r = requests.get(f"{API}/routes/{setup['rid']}", headers=H(toks["admin"]))
        s = next(x for x in r.json()["stops"] if x["id"] == setup["sid"])
        assert s.get("matter_ref") == REF
