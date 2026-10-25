# Welcome checklist for newly approved shops + one-time review-request reminder emails (iteration 25)
import io
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
NOAUTH = {"X-No-Auto-Auth": "1"}
SECRET = os.environ["JWT_SECRET"]
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
S = {}


def mint(sub, purpose, **extra):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": sub, "role": "merchant", "purpose": purpose, "iat": now, "exp": now + timedelta(hours=1), **extra}, SECRET, algorithm="HS256")


def _login(body):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=body, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def admin():
    return _login({"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"})


@pytest.fixture(scope="module")
def bakery():
    return _login({"email": "bakery@dashlocal.app", "password": "Bakery2026"})


def _cleanup():
    for sr in db.signup_requests.find({"store_name": {"$regex": "^TEST_"}}):
        if sr.get("merchant_id"):
            mid = sr["merchant_id"]
            for col in ("merchants",):
                db[col].delete_one({"id": mid})
            for col in ("users", "saved_notes", "import_jobs", "import_rows", "stops"):
                db[col].delete_many({"merchant_id": mid})
    db.merchants.delete_many({"name": {"$regex": "^TEST_"}})
    db.signup_requests.delete_many({"store_name": {"$regex": "^TEST_"}})
    db.review_requests.delete_many({})
    db.testimonials.delete_many({})
    db.login_attempts.delete_many({"key": {"$regex": "^(drv|trk)"}})


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    _cleanup()
    yield
    _cleanup()


@pytest.fixture(scope="module")
def shop(admin):
    """Signup -> approve -> set password -> merchant session."""
    email = f"test+wc{uuid.uuid4().hex[:8]}@resend.dev"
    r = requests.post(f"{BASE_URL}/api/signup-requests", json={"store_name": "TEST_Welcome " + uuid.uuid4().hex[:4], "contact_name": "TEST Owner", "email": email}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    sr = db.signup_requests.find_one({"email": email})
    time.sleep(2)
    r = requests.post(f"{BASE_URL}/api/admin/signup-requests/{sr['id']}/approve", headers=admin)
    assert r.status_code == 200, r.text
    mid = r.json()["merchant_id"]
    u = db.users.find_one({"email": email})
    r = requests.post(f"{BASE_URL}/api/auth/set-password", json={"token": mint(u["id"], "setpw", n=u["pw_nonce"]), "password": "TestPass2026!"}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return {"mid": mid, "h": {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}}


# ---------------- checklist
class TestChecklist:
    def test_approve_sets_onboarding(self, shop):
        assert db.merchants.find_one({"id": shop["mid"]})["onboarding"] is True

    def test_initial_checklist(self, shop):
        r = requests.get(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist", headers=shop["h"])
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["show"] is True and d["total"] == 4 and d["done"] == 1
        assert [s["key"] for s in d["steps"]] == ["password", "upload", "note", "delivered"]
        assert [s["done"] for s in d["steps"]] == [True, False, False, False]

    def test_admin_can_read(self, shop, admin):
        r = requests.get(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist", headers=admin)
        assert r.status_code == 200 and r.json()["show"] is True

    def test_other_merchant_403(self, shop, bakery):
        assert requests.get(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist", headers=bakery).status_code == 403
        assert requests.post(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist/dismiss", headers=bakery).status_code == 403
        assert db.merchants.find_one({"id": shop["mid"]})["onboarding"] is True

    def test_unauth_401(self, shop):
        assert requests.get(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist", headers=NOAUTH).status_code == 401
        assert requests.post(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist/dismiss", headers=NOAUTH).status_code == 401

    def test_note_step(self, shop):
        r = requests.put(f"{BASE_URL}/api/merchants/{shop['mid']}/saved-notes", json={"phone": "+14155559911", "note": "TEST gate 1234"}, headers=shop["h"])
        assert r.status_code == 200, r.text
        d = requests.get(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist", headers=shop["h"]).json()
        assert {s["key"]: s["done"] for s in d["steps"]}["note"] is True and d["done"] == 2

    def test_upload_step(self, shop):
        csv = b"customer_name,phone,address\nTEST Person,+14155559912,1 Market St San Francisco CA\n"
        h = {k: v for k, v in shop["h"].items()}
        r = requests.post(f"{BASE_URL}/api/merchants/{shop['mid']}/import", files={"file": ("orders.csv", io.BytesIO(csv), "text/csv")}, headers=h)
        assert r.status_code == 200, r.text
        d = requests.get(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist", headers=shop["h"]).json()
        assert {s["key"]: s["done"] for s in d["steps"]}["upload"] is True and d["done"] == 3

    def test_delivered_step(self, shop):
        db.stops.insert_one({"id": "TEST_stop_" + uuid.uuid4().hex[:6], "merchant_id": shop["mid"], "status": "delivered"})
        d = requests.get(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist", headers=shop["h"]).json()
        assert all(s["done"] for s in d["steps"]) and d["done"] == 4 and d["total"] == 4

    def test_dismiss(self, shop):
        r = requests.post(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist/dismiss", headers=shop["h"])
        assert r.status_code == 200 and r.json()["ok"] is True
        assert requests.get(f"{BASE_URL}/api/merchants/{shop['mid']}/checklist", headers=shop["h"]).json()["show"] is False

    def test_demo_merchant_hidden(self, bakery):
        r = requests.get(f"{BASE_URL}/api/merchants/mer-1/checklist", headers=bakery)
        assert r.status_code == 200 and r.json()["show"] is False

    def test_unknown_merchant_admin_404(self, admin):
        assert requests.get(f"{BASE_URL}/api/merchants/mer-nope123/checklist", headers=admin).status_code == 404


# ---------------- review reminders
def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _fake_rr(days, status="sent"):
    rid = "TEST_rr_" + uuid.uuid4().hex[:6]
    db.review_requests.insert_one({"id": rid, "merchant_id": "mer-1", "shop": "Mission Bakery Co.", "emails": ["bakery@dashlocal.app"], "status": status,
                                   "created_at": _ago(days), "submitted_at": _ago(1) if status == "submitted" else None, "reminded_at": None})
    return rid


class TestReminders:
    def test_auth(self, bakery):
        assert requests.post(f"{BASE_URL}/api/admin/review-requests/run-reminders", headers=NOAUTH).status_code == 401
        assert requests.post(f"{BASE_URL}/api/admin/review-requests/run-reminders", headers=bakery).status_code == 403

    def test_reminder_sent_once(self, admin):
        r = requests.post(f"{BASE_URL}/api/admin/review-requests", json={"merchant_id": "mer-1"}, headers=admin)
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        assert r.json()["reminded_at"] is None
        S["rid"] = rid
        S["young"] = _fake_rr(1)
        S["submitted"] = _fake_rr(8, "submitted")
        S["late"] = _fake_rr(13.9)
        db.review_requests.update_one({"id": rid}, {"$set": {"created_at": _ago(8)}})
        time.sleep(2)
        r = requests.post(f"{BASE_URL}/api/admin/review-requests/run-reminders", headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["sent"] >= 1, r.json()
        doc = db.review_requests.find_one({"id": rid})
        assert doc["reminded_at"] and doc["reminder_status"] == "sent", doc
        time.sleep(1)
        r2 = requests.post(f"{BASE_URL}/api/admin/review-requests/run-reminders", headers=admin)
        assert r2.status_code == 200 and r2.json()["sent"] == 0, r2.json()
        assert db.review_requests.find_one({"id": rid})["reminded_at"] == doc["reminded_at"]

    def test_skipped(self):
        for k in ("young", "submitted", "late"):
            doc = db.review_requests.find_one({"id": S[k]})
            assert doc["reminded_at"] is None and "reminder_status" not in doc, (k, doc)

    def test_admin_list_shows_reminded(self, admin):
        rows = {x["id"]: x for x in requests.get(f"{BASE_URL}/api/admin/review-requests", headers=admin).json()}
        assert rows[S["rid"]]["reminded_at"] and rows[S["rid"]]["reminder_status"] == "sent"

    def test_token_still_opens(self):
        r = requests.get(f"{BASE_URL}/api/review-requests/{mint(S['rid'], 'review')}", headers=NOAUTH)
        assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["shop"] == "Mission Bakery Co."
