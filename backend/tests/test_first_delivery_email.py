# New Shop Welcome Email: one-time "first delivery done — tips to grow" email for signup-approved shops (iteration 26)
import asyncio
import io
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import requests
import websockets
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
NOAUTH = {"X-No-Auto-Auth": "1"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
S = {}
SIG = "M10 10 L20 20 L30 15"


def _login(body):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=body, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_token():
    return _login({"email": "admin@arrivewise.app", "password": "DashLocalAdmin2026"})


@pytest.fixture(scope="module")
def admin(admin_token):
    return {"Authorization": f"Bearer {admin_token}", **NOAUTH}


def _cleanup():
    for sr in db.signup_requests.find({"store_name": {"$regex": "^TEST_FD"}}):
        if sr.get("merchant_id"):
            mid = sr["merchant_id"]
            db.merchants.delete_one({"id": mid})
            for col in ("users", "saved_notes", "import_jobs", "import_rows", "stops"):
                db[col].delete_many({"merchant_id": mid})
    db.stops.delete_many({"id": {"$regex": "^TEST_FD"}})
    db.routes.delete_many({"name": {"$regex": "^TEST_FD"}})
    db.merchants.delete_many({"name": {"$regex": "^TEST_FD"}})
    db.signup_requests.delete_many({"store_name": {"$regex": "^TEST_FD"}})
    db.login_attempts.delete_many({"key": {"$regex": "^(drv|trk)"}})


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    _cleanup()
    yield
    _cleanup()


@pytest.fixture(scope="module")
def route():
    """A dedicated active route for today (copies the service_date of the seeded active R-101) so demo routes stay untouched."""
    r101 = db.routes.find_one({"status": "active", "driver_id": "drv-1"}, {"_id": 0})
    assert r101, "seeded active route missing"
    rid = "TEST_FD_route_" + uuid.uuid4().hex[:6]
    db.routes.insert_one({"id": rid, "name": "TEST_FD Route", "zone": "Downtown", "driver_id": "drv-2", "status": "active", "planned_distance_m": 0,
                          "actual_distance_m": 0, "started_at": r101["started_at"], "completed_at": None, "resequenced_at": None,
                          "resequence_seen": True, "service_date": r101["service_date"], "created_at": r101["created_at"]})
    return rid


def _stop(mid, route_id, seq):
    sid = "TEST_FD_stop_" + uuid.uuid4().hex[:8]
    db.stops.insert_one({"id": sid, "route_id": route_id, "seq": seq, "customer_name": "TEST Customer", "customer_phone": "+15195550199",
                         "address": "200 King St W, Kitchener", "postal_code": "N2G 4V6", "zone": "Downtown", "lat": 43.47, "lng": -80.42,
                         "status": "pending", "tracking_code": "TFD" + uuid.uuid4().hex[:5].upper(), "notes": "", "merchant_id": mid})
    return sid


def _logs():
    out = subprocess.run("tail -n 400 /var/log/supervisor/backend.*.log", shell=True, capture_output=True, text=True).stdout
    return out


@pytest.fixture(scope="module")
def shop(admin):
    email = f"test+fd{uuid.uuid4().hex[:8]}@resend.dev"
    r = requests.post(f"{BASE_URL}/api/signup-requests", json={"store_name": "TEST_FD Shop " + uuid.uuid4().hex[:4], "contact_name": "TEST Owner", "email": email, "privacy_consent": True, "business_type": "pharmacy", "consent_timestamp": "2026-01-01T00:00:00Z"}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    sr = db.signup_requests.find_one({"email": email})
    time.sleep(3)
    r = requests.post(f"{BASE_URL}/api/admin/signup-requests/{sr['id']}/approve", headers=admin)
    assert r.status_code == 200, r.text
    S["approve_email_status"] = r.json().get("email_status")
    return {"mid": r.json()["merchant_id"], "email": email}


class TestApprove:
    def test_merchant_flags(self, shop):
        m = db.merchants.find_one({"id": shop["mid"]}, {"_id": 0})
        assert m["approved_via_signup"] is True
        assert "first_delivery_email_at" in m and m["first_delivery_email_at"] is None
        assert m.get("first_delivery_email_status") is None


class TestFirstDelivery:
    def test_csv_import_creates_stop(self, shop, route, admin):
        """Real path: merchant CSV import -> stop with merchant_id; then attach it to today's route."""
        csv = "customer_name,phone,address\nTEST Csv Customer,+15195550198,\"200 King St W, Kitchener\"\n"
        r = requests.post(f"{BASE_URL}/api/merchants/{shop['mid']}/import?consent=true", files={"file": ("o.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=admin)
        assert r.status_code == 200, r.text
        job = r.json()["id"]
        for _ in range(30):
            j = requests.get(f"{BASE_URL}/api/imports/{job}", headers=admin).json()
            if (j.get("job") or j).get("status") != "running":
                break
            time.sleep(1)
        st = db.stops.find_one({"merchant_id": shop["mid"]}, {"_id": 0})
        if not st:
            pytest.skip(f"CSV import did not create a stop (geocode?): {j}")
        db.stops.update_one({"id": st["id"]}, {"$set": {"route_id": route, "seq": 1}})
        S["s1"] = st["id"]
        assert st["status"] == "pending"

    def test_first_deliver_sets_email(self, shop, route, admin, admin_token):
        s1 = S.get("s1") or _stop(shop["mid"], route, 1)
        S["s1"] = s1
        S["s2"] = _stop(shop["mid"], route, 2)
        S["s3"] = _stop(shop["mid"], route, 3)  # keeps route active after 2 deliveries

        async def deliver_with_ws():
            uri = BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + f"/api/ws/route/{route}?token={admin_token}"
            async with websockets.connect(uri, open_timeout=10) as ws:
                await asyncio.sleep(1)
                r = await asyncio.to_thread(requests.post, f"{BASE_URL}/api/stops/{s1}/deliver", json={"signature": SIG}, headers=admin)
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    msg = None
                return r, msg

        time.sleep(5)  # space out from the approve email
        r, msg = asyncio.run(deliver_with_ws())
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "delivered" and d["delivered_at"] and d["pod_signature"] == SIG
        assert d["arrived_at"] and d["arrival_method"] == "manual"
        assert "_id" not in d
        S["ws_msg"] = msg
        m = db.merchants.find_one({"id": shop["mid"]}, {"_id": 0})
        assert m["first_delivery_email_at"], m
        S["fd_at"] = m["first_delivery_email_at"]

    def test_route_broadcast(self):
        msg = S.get("ws_msg")
        assert msg, "no route broadcast received over /api/ws/route"
        body = json.loads(msg)
        stops = body.get("stops") or (body.get("route") or {}).get("stops") or []
        assert any(s.get("id") == S["s1"] and s.get("status") == "delivered" for s in stops), str(body)[:500]

    def test_status_becomes_sent(self, shop):
        status = None
        for _ in range(20):
            status = (db.merchants.find_one({"id": shop["mid"]}, {"_id": 0}) or {}).get("first_delivery_email_status")
            if status:
                break
            time.sleep(1)
        assert status in ("sent", "failed"), status
        if status == "failed":
            logs = _logs()
            assert "429" in logs, "first-delivery email failed without a provider 429 — real bug"
            pytest.xfail("provider 429 rate limit")
        assert status == "sent"

    def test_second_delivery_no_resend(self, shop, admin):
        time.sleep(3)
        r = requests.post(f"{BASE_URL}/api/stops/{S['s2']}/deliver", json={"signature": SIG}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "delivered"
        time.sleep(4)
        m = db.merchants.find_one({"id": shop["mid"]}, {"_id": 0})
        assert m["first_delivery_email_at"] == S["fd_at"]

    def test_redeliver_rejected(self, admin):
        r = requests.post(f"{BASE_URL}/api/stops/{S['s1']}/deliver", json={"signature": SIG}, headers=admin)
        assert r.status_code == 400 and "already delivered" in r.text

    def test_proof_required(self, admin):
        r = requests.post(f"{BASE_URL}/api/stops/{S['s3']}/deliver", json={}, headers=admin)
        assert r.status_code == 400 and "Proof" in r.text

    def test_route_completes(self, route, admin):
        r = requests.post(f"{BASE_URL}/api/stops/{S['s3']}/deliver", json={"photo_path": "uploads/test.jpg"}, headers=admin)
        assert r.status_code == 200, r.text
        assert db.routes.find_one({"id": route})["status"] == "completed"


class TestDemoMerchant:
    def test_mer1_never_flagged(self, route, admin):
        before = db.merchants.find_one({"id": "mer-1"}, {"_id": 0})
        db.routes.update_one({"id": route}, {"$set": {"status": "active", "completed_at": None}})
        sid = _stop("mer-1", route, 9)
        r = requests.post(f"{BASE_URL}/api/stops/{sid}/deliver", json={"signature": SIG}, headers=admin)
        assert r.status_code == 200, r.text
        time.sleep(2)
        m = db.merchants.find_one({"id": "mer-1"}, {"_id": 0})
        assert "first_delivery_email_at" not in m and "first_delivery_email_status" not in m
        assert {k: v for k, v in m.items()} == before

    def test_legacy_merchant_without_flag(self, route, admin):
        """A merchant created by admin (no approved_via_signup) is never emailed."""
        mid = "mer-TESTFD" + uuid.uuid4().hex[:4]
        db.merchants.insert_one({"id": mid, "name": "TEST_FD Legacy", "contact": "x@resend.dev"})
        sid = _stop(mid, route, 10)
        r = requests.post(f"{BASE_URL}/api/stops/{sid}/deliver", json={"signature": SIG}, headers=admin)
        assert r.status_code == 200
        time.sleep(2)
        m = db.merchants.find_one({"id": mid}, {"_id": 0})
        assert "first_delivery_email_at" not in m
        db.stops.delete_many({"merchant_id": mid})
