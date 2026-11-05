# Self-service account deletion (App Store 5.1.1(v)): GET/POST /api/me/delete-account for stores and drivers.
# Uses throwaway TEST merchant/driver inserted straight into Mongo; never touches seeded demo accounts.
import os
import sys
import uuid
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import hash_pw, TERMS_VERSION, DRIVER_TERMS_VERSION  # noqa: E402

BASE_URL = (os.environ.get("EXPO_BACKEND_URL") or os.environ.get("EXPO_PUBLIC_BACKEND_URL")).rstrip("/")
API = f"{BASE_URL}/api"
NO = {"X-No-Auto-Auth": "1"}
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def H(tok):
    return {"Authorization": f"Bearer {tok}", **NO}


@pytest.fixture(scope="module")
def admin_tok():
    r = requests.post(f"{API}/auth/login", json={"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"}, headers=NO)
    assert r.status_code == 200
    return r.json()["token"]


@pytest.fixture(scope="module")
def throwaway():
    sfx = uuid.uuid4().hex[:8]
    mid, did = f"mer-deltest-{sfx}", f"drv-deltest-{sfx}"
    email, pw = f"test_deltest_{sfx}@example.com", f"TEST_Pw_{sfx}!"
    phone = "+151955501" + str(10 + uuid.uuid4().int % 80)  # fictional 555-01xx: deletion now texts the driver
    pin = "583" + str(uuid.uuid4().int)[:3]
    mdb.merchants.insert_one({"id": mid, "name": f"TEST_DelStore {sfx}", "contact": "test@example.com", "address": "1 King St W, Kitchener"})
    muid, duid = str(uuid.uuid4()), str(uuid.uuid4())
    mdb.users.insert_one({"id": muid, "email": email, "password_hash": hash_pw(pw), "role": "merchant", "name": "TEST", "merchant_id": mid,
                          "disabled": False, "terms_version": TERMS_VERSION, "terms_consent": True})
    mdb.drivers.insert_one({"id": did, "name": f"TEST_Driver {sfx}", "phone": phone, "vehicle": "TEST-1", "lat": 43.45, "lng": -80.49})
    mdb.users.insert_one({"id": duid, "role": "driver", "driver_id": did, "pin_hash": hash_pw(pin), "disabled": False,
                          "driver_terms_version": DRIVER_TERMS_VERSION, "phipa_consent_given": True})
    yield {"mid": mid, "did": did, "email": email, "pw": pw, "phone": phone, "pin": pin, "muid": muid, "duid": duid}
    # cleanup by exact ids only
    mdb.users.delete_many({"id": {"$in": [muid, duid]}})
    mdb.merchants.delete_one({"id": mid})
    mdb.drivers.delete_one({"id": did})
    mdb.stops.delete_many({"id": {"$regex": f"^TEST_deltest_{sfx}"}})
    mdb.invoices.delete_many({"id": {"$regex": f"^TEST_deltest_{sfx}"}})
    mdb.routes.delete_many({"id": {"$regex": f"^TEST_deltest_{sfx}"}})


def mlogin(t):
    return requests.post(f"{API}/auth/login", json={"email": t["email"], "password": t["pw"]}, headers=NO)


def dlogin(t):
    return requests.post(f"{API}/auth/driver-login", json={"phone": t["phone"], "pin": t["pin"]}, headers=NO)


class TestDeleteCheckDemo:
    """GET check on real demo accounts (read-only)."""

    def test_unauth_401(self):
        assert requests.get(f"{API}/me/delete-account", headers=NO).status_code == 401

    def test_books_check_shape(self):
        r = requests.post(f"{API}/auth/login", json={"email": "books@dashlocal.app", "password": "Books2026"}, headers=NO)
        assert r.status_code == 200
        c = requests.get(f"{API}/me/delete-account", headers=H(r.json()["token"]))
        assert c.status_code == 200
        d = c.json()
        assert d["role"] == "merchant" and isinstance(d["blockers"], list) and d["can_delete"] == (not d["blockers"])
        print("books blockers:", d)

    def test_marcus_check_blocked_by_route(self):
        r = requests.post(f"{API}/auth/driver-login", json={"phone": "(519) 555-0101", "pin": "482913"}, headers=NO)
        assert r.status_code == 200
        tok = r.json()["token"]
        d = requests.get(f"{API}/me/delete-account", headers=H(tok)).json()
        print("marcus:", d)
        assert d["role"] == "driver"
        has_route = mdb.routes.find_one({"driver_id": "drv-1", "status": {"$in": ["planned", "active"]}}) is not None
        assert d["can_delete"] is (not has_route)
        # wrong PIN -> 403 (doesn't delete, doesn't sign out)
        w = requests.post(f"{API}/me/delete-account", json={"secret": "000001"}, headers=H(tok))
        assert w.status_code == 403 and "PIN" in w.json()["detail"]
        mdb.users.update_one({"driver_id": "drv-1", "role": "driver"}, {"$unset": {"delete_fails": ""}})  # undo the counter this test added

    def test_admin_cannot_delete(self, admin_tok):
        d = requests.get(f"{API}/me/delete-account", headers=H(admin_tok)).json()
        assert d["role"] == "admin" and d["can_delete"] is False


class TestMerchantDeletion:
    def test_wrong_password_401(self, throwaway):
        tok = mlogin(throwaway).json()["token"]
        r = requests.post(f"{API}/me/delete-account", json={"secret": "nope"}, headers=H(tok))
        assert r.status_code == 403 and "password" in r.json()["detail"].lower()

    def test_blocked_unpaid_bill_409(self, throwaway):
        t = throwaway
        sfx = t["mid"].split("-")[-1]
        iid = f"TEST_deltest_{sfx}_inv"
        mdb.invoices.insert_one({"id": iid, "merchant_id": t["mid"], "status": "unpaid", "total": 12.5})
        tok = mlogin(t).json()["token"]
        c = requests.get(f"{API}/me/delete-account", headers=H(tok)).json()
        assert c["can_delete"] is False and any("bill" in b for b in c["blockers"])
        r = requests.post(f"{API}/me/delete-account", json={"secret": t["pw"]}, headers=H(tok))
        assert r.status_code == 409
        mdb.invoices.update_one({"id": iid}, {"$set": {"status": "paid"}})
        sid = f"TEST_deltest_{sfx}_stop"
        mdb.stops.insert_one({"id": sid, "merchant_id": t["mid"], "status": "pending"})
        c = requests.get(f"{API}/me/delete-account", headers=H(tok)).json()
        assert c["can_delete"] is False and any("open" in b for b in c["blockers"])
        assert requests.post(f"{API}/me/delete-account", json={"secret": t["pw"]}, headers=H(tok)).status_code == 409
        mdb.stops.update_one({"id": sid}, {"$set": {"status": "delivered"}})
        assert requests.get(f"{API}/me/delete-account", headers=H(tok)).json()["can_delete"] is True

    def test_successful_delete(self, throwaway, admin_tok):
        t = throwaway
        tok = mlogin(t).json()["token"]
        r = requests.post(f"{API}/me/delete-account", json={"secret": t["pw"]}, headers=H(tok))
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert requests.get(f"{API}/me/delete-account", headers=H(tok)).status_code == 401
        assert requests.get(f"{API}/merchants/{t['mid']}", headers=H(tok)).status_code == 401
        assert mlogin(t).status_code == 401
        u = mdb.users.find_one({"id": t["muid"]})
        assert u["disabled"] is True and "password_hash" not in u and u["email"].endswith("@deleted.dashlocal.invalid") and u["name"] == "Deleted account"
        m = mdb.merchants.find_one({"id": t["mid"]})
        assert m["deleted"] is True and m["disabled"] is True and m["contact"] == "" and m["address"] == "" and m["name"].startswith("TEST_DelStore")
        assert mdb.invoices.find_one({"merchant_id": t["mid"]}) is not None  # billing records kept


class TestDriverDeletion:
    def test_wrong_pin_401(self, throwaway):
        tok = dlogin(throwaway).json()["token"]
        assert requests.post(f"{API}/me/delete-account", json={"secret": "111119"}, headers=H(tok)).status_code == 403

    def test_blocked_by_route_409(self, throwaway):
        t = throwaway
        rid = f"TEST_deltest_{t['did'].split('-')[-1]}_route"
        mdb.routes.insert_one({"id": rid, "driver_id": t["did"], "status": "planned", "name": "TEST route"})
        tok = dlogin(t).json()["token"]
        c = requests.get(f"{API}/me/delete-account", headers=H(tok)).json()
        assert c["can_delete"] is False and "route" in c["blockers"][0]
        assert requests.post(f"{API}/me/delete-account", json={"secret": t["pin"]}, headers=H(tok)).status_code == 409
        mdb.routes.update_one({"id": rid}, {"$set": {"status": "completed"}})

    def test_successful_delete(self, throwaway, admin_tok):
        t = throwaway
        tok = dlogin(t).json()["token"]
        assert t["did"] in [d["id"] for d in requests.get(f"{API}/drivers", headers=H(admin_tok)).json()]
        r = requests.post(f"{API}/me/delete-account", json={"secret": t["pin"]}, headers=H(tok))
        assert r.status_code == 200
        assert requests.get(f"{API}/me/delete-account", headers=H(tok)).status_code == 401
        assert dlogin(t).status_code == 401
        d = mdb.drivers.find_one({"id": t["did"]})
        assert d["deleted"] is True and d["name"] == "Deleted driver" and d["phone"] != t["phone"]
        assert "pin_hash" not in mdb.users.find_one({"id": t["duid"]})
        assert t["did"] not in [x["id"] for x in requests.get(f"{API}/drivers", headers=H(admin_tok)).json()]
        assert t["did"] not in [x["id"] for x in requests.get(f"{API}/admin/driver-accounts", headers=H(admin_tok)).json()]


# Lockout: 5 wrong secrets within 15 min -> 429 (even for the correct secret); uses its own throwaway driver
@pytest.fixture
def lock_driver():
    sfx = uuid.uuid4().hex[:8]
    did, duid, phone, pin = f"drv-deltest-lk-{sfx}", str(uuid.uuid4()), "+151955501" + str(10 + uuid.uuid4().int % 80), "583" + str(uuid.uuid4().int)[:3]  # fictional 555-01xx phone
    mdb.drivers.insert_one({"id": did, "name": f"TEST_LockDrv {sfx}", "phone": phone, "vehicle": "TEST", "lat": 43.45, "lng": -80.49})
    mdb.users.insert_one({"id": duid, "role": "driver", "driver_id": did, "pin_hash": hash_pw(pin), "disabled": False,
                          "driver_terms_version": DRIVER_TERMS_VERSION, "phipa_consent_given": True})
    yield {"did": did, "duid": duid, "phone": phone, "pin": pin}
    mdb.users.delete_one({"id": duid})
    mdb.drivers.delete_one({"id": did})


class TestDeleteLockout:
    def test_five_wrong_then_429(self, lock_driver):
        t = lock_driver
        tok = dlogin(t).json()["token"]
        codes = [requests.post(f"{API}/me/delete-account", json={"secret": "000000"}, headers=H(tok)).status_code for _ in range(5)]
        assert codes == [403] * 5
        r = requests.post(f"{API}/me/delete-account", json={"secret": "000000"}, headers=H(tok))
        assert r.status_code == 429 and "Too many wrong tries" in r.json()["detail"]
        # correct PIN is also refused while locked, and the account is NOT deleted
        r = requests.post(f"{API}/me/delete-account", json={"secret": t["pin"]}, headers=H(tok))
        assert r.status_code == 429
        assert mdb.users.find_one({"id": t["duid"]})["disabled"] is False
        # the session still works (429 must not sign out)
        assert requests.get(f"{API}/me/delete-account", headers=H(tok)).status_code == 200

    def test_lock_expires_after_15_min(self, lock_driver):
        t = lock_driver
        mdb.users.update_one({"id": t["duid"]}, {"$set": {"delete_fails": {"n": 5, "since": "2020-01-01T00:00:00+00:00"}}})
        tok = dlogin(t).json()["token"]
        r = requests.post(f"{API}/me/delete-account", json={"secret": "000000"}, headers=H(tok))
        assert r.status_code == 403
        assert mdb.users.find_one({"id": t["duid"]})["delete_fails"]["n"] == 1

    def test_old_fails_should_not_count(self, lock_driver):
        """4 wrong tries long ago + 1 now should NOT lock (window is 15 minutes)."""
        t = lock_driver
        mdb.users.update_one({"id": t["duid"]}, {"$set": {"delete_fails": {"n": 4, "since": "2020-01-01T00:00:00+00:00"}}})
        tok = dlogin(t).json()["token"]
        assert requests.post(f"{API}/me/delete-account", json={"secret": "000000"}, headers=H(tok)).status_code == 403
        r = requests.post(f"{API}/me/delete-account", json={"secret": t["pin"]}, headers=H(tok))
        assert r.status_code == 200, f"locked by stale fails: {r.status_code} {r.text}"
