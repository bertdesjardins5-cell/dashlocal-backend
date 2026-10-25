# Retest iter19: window/rating responses for an order-token holder are trimmed (no PII); staff get full stop
import os
import re

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().strip('"')
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"
NOAUTH = {"X-No-Auto-Auth": "1"}
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

PII = {"customer_name", "customer_phone", "address", "notes", "lat", "lng", "pod_photo_path", "pod_signature",
       "share_token", "postal_code", "fail_reason", "fee", "rating_comment"}
PUBLIC = {"id", "tracking_code", "status", "arrived_at", "delivered_at", "pod_qr_verified", "window_start",
          "window_end", "window_date", "rating", "nearby_sms_status", "has_photo", "has_signature"}


def last4(code):
    return re.sub(r"\D", "", mdb.stops.find_one({"tracking_code": code})["customer_phone"])[-4:]


def sid(code):
    return mdb.stops.find_one({"tracking_code": code})["id"]


def clean():
    mdb.login_attempts.delete_many({"key": {"$regex": "^(trk|drv)"}})


def order_token(code):
    r = requests.post(f"{API}/track/{code}/verify", json={"last4": last4(code)}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin():
    clean()
    r = requests.post(f"{API}/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}, headers=NOAUTH)
    assert r.status_code == 200
    tok = r.json()["token"]
    assert requests.post(f"{API}/admin/reset", headers={"Authorization": f"Bearer {tok}", **NOAUTH}).status_code == 200
    yield tok
    requests.post(f"{API}/admin/reset", headers={"Authorization": f"Bearer {tok}", **NOAUTH})
    clean()


@pytest.fixture(scope="module")
def drv1(admin):
    r = requests.post(f"{API}/auth/driver-login", json={"phone": "+14155550101", "pin": "482913"}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---- FIX 4: window
class TestWindowTrim:
    body = {"date": None, "start": None, "end": None}

    def test_customer_window_trimmed(self, admin):
        r = requests.put(f"{API}/stops/{sid('MD1001')}/window", json=self.body, headers={"X-Order-Token": order_token("MD1001"), **NOAUTH})
        assert r.status_code == 200, r.text
        d = r.json()
        assert not (PII & d.keys()), f"leaked: {PII & d.keys()}"
        assert set(d.keys()) == PUBLIC | {"moved_off_route", "sms_status"}
        assert d["tracking_code"] == "MD1001" and d["window_start"] is None

    def test_customer_window_booking_trimmed(self, admin):
        # book tomorrow 10-12 (never in the past)
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        tmrw = (datetime.now(ZoneInfo("America/Los_Angeles")) + timedelta(days=1)).strftime("%Y-%m-%d")
        r = requests.put(f"{API}/stops/{sid('MD1005')}/window", json={"date": tmrw, "start": "10:00", "end": "12:00"},
                         headers={"X-Order-Token": order_token("MD1005"), **NOAUTH})
        assert r.status_code == 200, r.text
        d = r.json()
        assert not (PII & d.keys())
        assert d["window_start"] == "10:00" and d["window_date"] == tmrw and "moved_off_route" in d

    def test_customer_with_staff_bearer_still_trimmed(self, admin):
        # web app sends both Authorization (staff session) and X-Order-Token
        r = requests.put(f"{API}/stops/{sid('MD1001')}/window", json=self.body,
                         headers={"X-Order-Token": order_token("MD1001"), "Authorization": f"Bearer {admin}", **NOAUTH})
        assert r.status_code == 200
        assert not (PII & r.json().keys())

    def test_admin_window_full(self, admin):
        r = requests.put(f"{API}/stops/{sid('MD1001')}/window", json=self.body, headers={"Authorization": f"Bearer {admin}", **NOAUTH})
        assert r.status_code == 200
        d = r.json()
        assert {"customer_phone", "address", "notes", "customer_name"} <= d.keys()
        assert d["customer_phone"].endswith("0100") and "sms_status" in d


# ---- FIX 4: rating
class TestRatingTrim:
    @pytest.fixture(scope="class")
    def delivered(self, admin, drv1):
        out = {}
        for code in ("MD1002", "MD1003"):
            r = requests.post(f"{API}/stops/{sid(code)}/deliver", json={"signature": "M10 10 L50 50"},
                              headers={"Authorization": f"Bearer {drv1}", **NOAUTH})
            if r.status_code != 200:
                pytest.skip(f"deliver {code} failed {r.status_code} {r.text}")
            out[code] = sid(code)
        return out

    def test_customer_low_rating_trimmed_with_apology(self, delivered):
        r = requests.post(f"{API}/stops/{delivered['MD1002']}/rating", json={"stars": 2, "comment": "TEST_ late"},
                          headers={"X-Order-Token": order_token("MD1002"), **NOAUTH})
        assert r.status_code == 200, r.text
        d = r.json()
        assert not (PII & d.keys()), f"leaked: {PII & d.keys()}"
        assert set(d.keys()) == PUBLIC | {"apology_code", "apology_sms_status"}
        assert d["rating"] == 2 and d["apology_code"].startswith("SORRY-")
        assert d["apology_sms_status"]

    def test_admin_rating_full(self, admin, delivered):
        r = requests.post(f"{API}/stops/{delivered['MD1003']}/rating", json={"stars": 5},
                          headers={"Authorization": f"Bearer {admin}", **NOAUTH})
        assert r.status_code == 200, r.text
        d = r.json()
        assert {"customer_phone", "address", "notes"} <= d.keys() and d["rating"] == 5
