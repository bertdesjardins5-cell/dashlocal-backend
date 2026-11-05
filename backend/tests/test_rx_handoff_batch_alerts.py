# Prescription hand-off (photo + signature for pharmacy drops) and Batch Progress Alert emails — iteration 33
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
API = f"{BASE_URL}/api"
NOAUTH = {"X-No-Auto-Auth": "1"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
for _p in ("uploads/TEST_rx_photo.jpg", "uploads/TEST_tier.jpg"):  # pharmacy hand-off needs a real uploaded file record
    db.files.update_one({"storage_path": _p}, {"$set": {"storage_path": _p, "content_type": "image/jpeg"}}, upsert=True)
TZ = ZoneInfo("America/Toronto")
SIG = "M10 10 L60 40 L80 20"  # drawn signature path (pharmacy hand-off checks the format)
PHOTO = "uploads/TEST_rx_photo.jpg"


def fresh_photo() -> str:
    """Delivery only accepts a photo uploaded by the delivering driver and not used on another stop."""
    import uuid
    path = f"uploads/TEST_rx_{uuid.uuid4().hex}.jpg"
    u = db.users.find_one({"role": "driver", "driver_id": "drv-3"}, {"_id": 0, "id": 1})
    db.files.insert_one({"storage_path": path, "content_type": "image/jpeg", "uploaded_by": u["id"]})
    return path


RX_MSG = "Prescription hand-off needs both a doorstep photo and the recipient's signature"
CREATED = {"stops": [], "manifests": []}


def today():
    return datetime.now(TZ).date().isoformat()


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"login failed {email} {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def admin():
    return _login("admin@dashlocal.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def books():
    return _login("books@dashlocal.app", "Books2026")


@pytest.fixture(scope="module")
def driver():
    r = requests.post(f"{API}/auth/driver-login", json={"phone": "+15195550103", "pin": "264871"}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code} {r.text}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    assert db.merchants.find_one({"id": "mer-2"})["pharmacy_partner"] is True
    yield
    db.invoices.delete_many({"stop_id": {"$in": CREATED["stops"]}})
    db.stops.delete_many({"id": {"$in": CREATED["stops"]}})
    db.stops.delete_many({"manifest_id": {"$in": CREATED["manifests"]}})
    db.batch_alerts.delete_many({"id": {"$in": CREATED["manifests"]}})
    db.routes.delete_many({"id": {"$regex": "^TEST_RX_route_"}})


@pytest.fixture(scope="module")
def route():
    rid = "TEST_RX_route_" + uuid.uuid4().hex[:6]
    db.routes.insert_one({"id": rid, "name": "TEST_RX Route", "zone": "Downtown", "driver_id": "drv-3", "status": "active",
                          "planned_distance_m": 0, "actual_distance_m": 0, "started_at": datetime.utcnow().isoformat() + "+00:00",
                          "completed_at": None, "resequenced_at": None, "resequence_seen": True, "service_date": today(),
                          "created_at": datetime.utcnow().isoformat() + "+00:00"})
    return rid


def _mk_stop(mid, route=None, manifest=None, run=None, qr=False, name="TEST_RX"):
    sid = str(uuid.uuid4())
    fee = {"base": 8.99, "km": 3.0, "distance_fee": 0.0, "priority_fee": 0.0, "total": 8.99, "currency": "CAD", "priced_at": "x"}
    db.stops.insert_one({"id": sid, "seq": 900, "customer_name": f"{name} {mid}", "customer_phone": "+15195550143",
                         "address": "200 King St W, Kitchener", "postal_code": "N2G 4V6", "zone": "Downtown", "lat": 43.4516, "lng": -80.4925,
                         "status": "pending", "tracking_code": f"TR{uuid.uuid4().hex[:6].upper()}", "notes": "", "merchant_id": mid,
                         "route_id": route, "priority": False, "fee": fee, "manifest_id": manifest, "batch_run": run,
                         "pod_qr_verified": qr, "created_at": datetime.utcnow().isoformat() + "+00:00"})
    CREATED["stops"].append(sid)
    return sid


# ---- Prescription hand-off on deliver
class TestRxHandoff:
    def test_pharmacy_photo_only_rejected(self, driver, route):
        sid = _mk_stop("mer-2", route)
        r = requests.post(f"{API}/stops/{sid}/deliver", json={"photo_path": PHOTO}, headers=driver)
        assert r.status_code == 400 and r.json()["detail"] == RX_MSG, r.text
        assert db.stops.find_one({"id": sid})["status"] == "pending"

    def test_pharmacy_signature_only_rejected(self, driver, route):
        sid = _mk_stop("mer-2", route)
        r = requests.post(f"{API}/stops/{sid}/deliver", json={"signature": SIG}, headers=driver)
        assert r.status_code == 400 and r.json()["detail"] == RX_MSG, r.text

    def test_pharmacy_qr_only_rejected(self, driver, route):
        sid = _mk_stop("mer-2", route, qr=True)
        r = requests.post(f"{API}/stops/{sid}/deliver", json={}, headers=driver)
        assert r.status_code == 400 and r.json()["detail"] == RX_MSG, r.text

    def test_pharmacy_qr_plus_photo_rejected(self, admin, route):
        sid = _mk_stop("mer-2", route, qr=True)
        r = requests.post(f"{API}/stops/{sid}/deliver", json={"photo_path": PHOTO}, headers=admin)
        assert r.status_code == 400, r.text

    def test_batch_run_stop_non_partner_requires_both(self, driver, route):
        # batch_run set on a non-partner merchant stop still counts as a pharmacy drop
        sid = _mk_stop("mer-1", route, run="evening")
        g = requests.get(f"{API}/stops/{sid}", headers=driver).json()
        assert g["pharmacy_drop"] is True
        r = requests.post(f"{API}/stops/{sid}/deliver", json={"signature": SIG}, headers=driver)
        assert r.status_code == 400 and r.json()["detail"] == RX_MSG

    def test_pharmacy_both_ok_invoice_rate(self, driver, route):
        man = f"mer-2:{today()}:TESTRX{uuid.uuid4().hex[:4]}"
        sid = _mk_stop("mer-2", route, manifest=man, run="evening")
        photo = fresh_photo()
        r = requests.post(f"{API}/stops/{sid}/deliver", json={"photo_path": photo, "signature": SIG}, headers=driver)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "delivered" and d["pod_photo_path"] == photo and d["pod_signature"] == SIG
        assert "customer_phone" not in d and "fee" not in d
        inv = db.invoices.find_one({"stop_id": sid}, {"_id": 0})
        assert inv, "no invoice"
        assert inv["lines"][0]["amount"] == 8.99 and "Standard rate" in inv["lines"][0]["description"], inv["lines"]
        assert inv.get("tier") == "Standard" and inv.get("manifest_drops") == 1

    def test_non_pharmacy_signature_only_ok(self, driver, route):
        sid = _mk_stop("mer-1", route)
        r = requests.post(f"{API}/stops/{sid}/deliver", json={"signature": SIG}, headers=driver)
        assert r.status_code == 200 and r.json()["status"] == "delivered", r.text

    def test_non_pharmacy_photo_only_and_qr_only_ok(self, admin, route):
        s1, s2 = _mk_stop("mer-1", route), _mk_stop("mer-1", route, qr=True)
        assert requests.post(f"{API}/stops/{s1}/deliver", json={"photo_path": fresh_photo()}, headers=admin).status_code == 200
        assert requests.post(f"{API}/stops/{s2}/deliver", json={}, headers=admin).status_code == 200

    def test_non_pharmacy_no_proof_rejected(self, admin, route):
        sid = _mk_stop("mer-1", route)
        r = requests.post(f"{API}/stops/{sid}/deliver", json={}, headers=admin)
        assert r.status_code == 400 and "Proof of delivery" in r.json()["detail"]

    def test_get_stop_pharmacy_flag_and_driver_view(self, driver, admin, route):
        p, n = _mk_stop("mer-2", route), _mk_stop("mer-1", route)
        gp = requests.get(f"{API}/stops/{p}", headers=driver).json()
        gn = requests.get(f"{API}/stops/{n}", headers=driver).json()
        assert gp["pharmacy_drop"] is True and gn["pharmacy_drop"] is False
        for g in (gp, gn):
            assert "customer_phone" not in g and "fee" not in g
        ga = requests.get(f"{API}/stops/{p}", headers=admin).json()
        assert ga["pharmacy_drop"] is True and ga["customer_phone"] == "+15195550143" and "fee" in ga


# ---- Batch progress alert (simulated dates far in the future so the live watcher never touches them)
def _manifest(date, run, n):
    man = f"mer-2:{date}:{run}"
    for i in range(n):
        db.stops.insert_one({"id": str(uuid.uuid4()), "customer_name": f"TEST_RXBA {i}", "merchant_id": "mer-2", "status": "pending",
                             "manifest_id": man, "batch_run": run, "tracking_code": f"TB{uuid.uuid4().hex[:6].upper()}"})
    CREATED["manifests"].append(man)
    return man


def _run(admin, at):
    r = requests.post(f"{API}/admin/batch-alerts/run", params={"at": at}, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["sent"]


class TestBatchAlerts:
    @pytest.mark.parametrize("n,day", [(7, "2030-01-15"), (8, "2030-01-16"), (9, "2030-01-17")])
    def test_sent_once_for_7_8_9(self, admin, n, day):
        man = _manifest(day, "afternoon", n)
        assert man in _run(admin, f"{day}T13:00:00")
        rec = db.batch_alerts.find_one({"id": man})
        assert rec and rec["drops"] == n and rec["run"] == "afternoon" and rec["merchant_id"] == "mer-2"
        assert man not in _run(admin, f"{day}T13:15:00")
        assert db.batch_alerts.count_documents({"id": man}) == 1

    @pytest.mark.parametrize("n,day", [(6, "2030-01-18"), (10, "2030-01-19")])
    def test_not_sent_for_6_or_10(self, admin, n, day):
        man = _manifest(day, "afternoon", n)
        assert man not in _run(admin, f"{day}T13:10:00")
        assert db.batch_alerts.find_one({"id": man}) is None

    def test_outside_window_not_sent(self, admin):
        day = "2030-01-20"
        man = _manifest(day, "afternoon", 8)
        for t in ("12:50:00", "12:59:59", "13:30:00", "13:31:00"):
            assert man not in _run(admin, f"{day}T{t}"), t
        assert db.batch_alerts.find_one({"id": man}) is None
        assert man in _run(admin, f"{day}T13:29:00")

    def test_evening_window(self, admin):
        day = "2030-01-21"
        man = _manifest(day, "evening", 9)
        assert man not in _run(admin, f"{day}T13:00:00")
        assert man not in _run(admin, f"{day}T15:59:00")
        assert man in _run(admin, f"{day}T16:00:00")
        assert man not in _run(admin, f"{day}T16:20:00")

    def test_recipients_recorded(self, admin):
        time.sleep(6)
        rec = db.batch_alerts.find_one({"id": "mer-2:2030-01-16:afternoon"})
        assert rec["recipients"], "no recipients recorded"
        r0 = rec["recipients"][0]
        assert r0["login"] == "books@dashlocal.app" and r0["to"] == "delivered@resend.dev", rec["recipients"]
        print("recipient status:", [x["status"] for x in rec["recipients"]])

    def test_non_admin_forbidden(self, books, driver):
        assert requests.post(f"{API}/admin/batch-alerts/run", headers=books).status_code == 403
        assert requests.post(f"{API}/admin/batch-alerts/run", headers=driver).status_code == 403
        assert requests.post(f"{API}/admin/batch-alerts/run", headers=NOAUTH).status_code == 401

    def test_invalid_at_param(self, admin):
        r = requests.post(f"{API}/admin/batch-alerts/run", params={"at": "not-a-date"}, headers=admin)
        assert r.status_code in (400, 422), f"invalid at → {r.status_code}"
