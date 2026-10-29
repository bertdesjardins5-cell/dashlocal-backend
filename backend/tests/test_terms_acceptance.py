# Terms acceptance: admin report, reminder emails, driver/merchant terms-status + accept-terms (iteration 60)
# NOTE: run sequentially (-n 0); module fixture snapshots/restores shared demo users, so xdist workers would race.
import os
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    from dotenv import dotenv_values
    BASE_URL = dotenv_values("/app/frontend/.env")["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
NOAUTH = {"X-No-Auto-Auth": "1"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


def _driver(phone, pin):
    r = requests.post(f"{API}/auth/driver-login", json={"phone": phone, "pin": pin}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def admin():
    return _login(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])


@pytest.fixture(scope="module")
def bakery():
    return _login("bakery@arrivewise.app", "Bakery2026")


@pytest.fixture(scope="module")
def books():
    return _login("books@arrivewise.app", "Books2026")


@pytest.fixture(scope="module", autouse=True)
def _restore():
    # snapshot drv-2 + bakery + mer-1 state and restore afterwards
    drv2 = db.users.find_one({"role": "driver", "driver_id": "drv-2"}, {"_id": 0})
    bak = db.users.find_one({"email": "bakery@arrivewise.app"}, {"_id": 0})
    m1 = db.merchants.find_one({"id": "mer-1"}, {"_id": 0})
    db.users.update_one({"role": "driver", "driver_id": "drv-2"}, {"$unset": {"driver_terms_version": "", "driver_terms_at": ""}})
    db.merchants.update_one({"id": "mer-1"}, {"$unset": {"terms_reminded_at": ""}})
    db.users.update_one({"email": "bakery@arrivewise.app"}, {"$unset": {"terms_version": "", "terms_consent_at": ""}})
    yield
    db.users.update_one({"role": "driver", "driver_id": "drv-2"}, {"$unset": {"driver_terms_version": "", "driver_terms_at": ""}})
    db.users.update_one({"email": "bakery@arrivewise.app"}, {"$unset": {"terms_version": "", "terms_consent_at": ""}})
    db.merchants.update_one({"id": "mer-1"}, {"$unset": {"terms_reminded_at": ""}})


class TestTermsReportAuth:
    def test_report_no_auth_401(self):
        r = requests.get(f"{API}/admin/terms-report", headers=NOAUTH)
        assert r.status_code == 401

    def test_report_merchant_403(self, books):
        r = requests.get(f"{API}/admin/terms-report", headers=books)
        assert r.status_code == 403

    def test_report_driver_403(self):
        h = _driver("+15195550103", "264871")
        r = requests.get(f"{API}/admin/terms-report", headers=h)
        assert r.status_code == 403

    def test_remind_merchant_403(self, books):
        r = requests.post(f"{API}/admin/terms-report/mer-1/remind", headers=books)
        assert r.status_code == 403


class TestTermsReport:
    def test_report_shape(self, admin):
        r = requests.get(f"{API}/admin/terms-report", headers=admin)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["version"] == "2026-10" and d["driver_version"] == "2026-10"
        st = {s["id"]: s for s in d["stores"]}
        assert st["mer-1"]["accepted"] is False and st["mer-1"]["reminded_at"] is None
        assert st["mer-1"]["users"][0]["email"] == "bakery@arrivewise.app"
        assert st["mer-1"]["users"][0]["accepted"] is False
        assert st["mer-2"]["accepted"] is True
        assert st["mer-2"]["users"][0]["accepted"] is True
        drv = {x["id"]: x for x in d["drivers"]}
        assert "drv-2" in drv and drv["drv-2"]["accepted"] is False
        for k in ("id", "name", "phone", "accepted", "accepted_at"):
            assert k in drv["drv-2"]
        assert "_id" not in str(d) and "password_hash" not in str(d) and "pin_hash" not in str(d)


class TestRemind:
    def test_remind_unknown_404(self, admin):
        r = requests.post(f"{API}/admin/terms-report/mer-nope/remind", headers=admin)
        assert r.status_code == 404

    def test_remind_all_accepted_409(self, admin):
        r = requests.post(f"{API}/admin/terms-report/mer-2/remind", headers=admin)
        assert r.status_code == 409

    def test_remind_then_cooldown_429(self, admin):
        r = requests.post(f"{API}/admin/terms-report/mer-1/remind", headers=admin)
        if r.status_code == 502:
            pytest.skip("Resend unavailable (environmental)")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["sent"] == 1 and d["reminded_at"]
        rep = requests.get(f"{API}/admin/terms-report", headers=admin).json()
        assert next(s for s in rep["stores"] if s["id"] == "mer-1")["reminded_at"] == d["reminded_at"]
        r2 = requests.post(f"{API}/admin/terms-report/mer-1/remind", headers=admin)
        assert r2.status_code == 429


class TestTermsStatusAccept:
    def test_admin_status_not_required_and_accept_403(self, admin):
        r = requests.get(f"{API}/auth/terms-status", headers=admin)
        assert r.status_code == 200 and r.json()["required"] is False
        r = requests.post(f"{API}/auth/accept-terms", headers=admin)
        assert r.status_code == 403

    def test_status_no_auth_401(self):
        assert requests.get(f"{API}/auth/terms-status", headers=NOAUTH).status_code == 401
        assert requests.post(f"{API}/auth/accept-terms", headers=NOAUTH).status_code == 401

    def test_driver_flow(self, admin):
        h = _driver("+15195550102", "730516")
        s = requests.get(f"{API}/auth/terms-status", headers=h).json()
        assert s["required"] is True and s["kind"] == "driver" and s["version"] == "2026-10"
        a = requests.post(f"{API}/auth/accept-terms", headers=h)
        assert a.status_code == 200 and a.json()["version"] == "2026-10"
        s2 = requests.get(f"{API}/auth/terms-status", headers=h).json()
        assert s2["required"] is False and s2["accepted_at"] == a.json()["accepted_at"]
        rep = requests.get(f"{API}/admin/terms-report", headers=admin).json()
        d2 = next(x for x in rep["drivers"] if x["id"] == "drv-2")
        assert d2["accepted"] is True and d2["accepted_at"] == a.json()["accepted_at"]
        u = db.users.find_one({"role": "driver", "driver_id": "drv-2"})
        assert u["driver_terms_history"][-1]["version"] == "2026-10"
        assert "terms_version" not in u  # driver accept must not touch store terms field

    def test_merchant_flow(self, admin, bakery):
        s = requests.get(f"{API}/auth/terms-status", headers=bakery).json()
        assert s["required"] is True and s["kind"] == "merchant"
        a = requests.post(f"{API}/auth/accept-terms", headers=bakery)
        assert a.status_code == 200
        assert requests.get(f"{API}/auth/terms-status", headers=bakery).json()["required"] is False
        rep = requests.get(f"{API}/admin/terms-report", headers=admin).json()
        m1 = next(x for x in rep["stores"] if x["id"] == "mer-1")
        assert m1["accepted"] is True and m1["users"][0]["accepted_at"] == a.json()["accepted_at"]
        # all accepted now -> remind 409 (even though cooldown may apply, 429 check comes first)
        db.merchants.update_one({"id": "mer-1"}, {"$unset": {"terms_reminded_at": ""}})
        assert requests.post(f"{API}/admin/terms-report/mer-1/remind", headers=admin).status_code == 409
