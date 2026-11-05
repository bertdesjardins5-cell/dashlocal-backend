# Iteration 35: photo shred on erase/retention, missed-pickup alerts, patient "left the store" SMS on pickup check-in
import os
import re
import time
import uuid

import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
NA = {"X-No-Auto-Auth": "1"}
DAY = "2030-01-15"          # far-future day so the real background loop never touches test manifests
DAY2 = "2030-01-16"
CUST_ERASE = "+15195550191"  # fictional TEST numbers
CUST_RET = "+15195550192"
LS_PHONES = ["+15195550193", "+15195550194", "+15195550195"]
BANNED = re.compile(r"prescription|medication|pharmacy", re.I)
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
       b"\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")


def login(email, pw):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": pw}, headers=NA)
    if r.status_code != 200:
        pytest.skip(f"login {email} failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NA}


@pytest.fixture(scope="module")
def admin():
    return login("admin@dashlocal.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def merch():
    return login("books@dashlocal.app", "Books2026")


@pytest.fixture(scope="module")
def drv():
    r = requests.post(f"{BASE}/api/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"}, headers=NA)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NA}


@pytest.fixture(scope="module")
def route():
    r = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    if not r:
        pytest.skip("no active drv-1 route")
    return r["id"]


def mk_stop(**kw):
    sid = "TEST_" + uuid.uuid4().hex[:10]
    d = {"id": sid, "route_id": None, "seq": 99, "customer_name": "TEST_Cust", "customer_phone": "+15195550199",
         "address": "200 King St W Kitchener", "postal_code": "N2G 1A7", "zone": "Downtown", "lat": 43.45, "lng": -80.49,
         "status": "pending", "tracking_code": "TST" + uuid.uuid4().hex[:6].upper(), "notes": "", "created_at": "2026-10-26T10:00:00+00:00",
         "erased_at": None}
    d.update(kw)
    db.stops.insert_one(dict(d))
    return d


def upload(drv):
    r = requests.post(f"{BASE}/api/upload", headers=drv, files={"file": ("TEST_pod.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["path"]


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    ids = [s["id"] for s in db.stops.find({"id": {"$regex": "^TEST_"}}, {"id": 1})]
    db.stops.delete_many({"id": {"$in": ids}})
    db.contact_logs.delete_many({"stop_id": {"$in": ids}})
    db.invoices.delete_many({"stop_id": {"$in": ids}})
    db.pickups.delete_many({"manifest_id": {"$regex": "^mer-2:(2030-01-15|2030-01-16)"}})
    db.missed_pickup_alerts.delete_many({"id": {"$regex": ":(2030-01-15|2030-01-16):"}})
    db.data_requests.delete_many({"phone": {"$in": [CUST_ERASE, CUST_RET]}})
    db.files.delete_many({"original_filename": "TEST_pod.png"})


# ---- (1) photo shred
class TestPhotoShred:
    def test_upload_deliver_erase_shreds_photo(self, admin, drv, route):
        path = upload(drv)
        g = requests.get(f"{BASE}/api/files/{path}", headers=admin)
        assert g.status_code == 200 and g.content == PNG
        s = mk_stop(route_id=route, customer_phone=CUST_ERASE, customer_name="TEST_Erase Photo")
        r = requests.post(f"{BASE}/api/stops/{s['id']}/deliver", headers=drv, json={"photo_path": path})
        if r.status_code != 200:  # fallback: mark delivered directly
            print("deliver via API failed:", r.status_code, r.text)
            db.stops.update_one({"id": s["id"]}, {"$set": {"status": "delivered", "delivered_at": "2026-10-26T12:00:00+00:00", "pod_photo_path": path}})
        assert db.stops.find_one({"id": s["id"]})["pod_photo_path"] == path
        e = requests.post(f"{BASE}/api/admin/customers/erase", headers=admin, json={"phone": CUST_ERASE, "confirm": "ERASE"})
        assert e.status_code == 200, e.text
        assert e.json()["erased_orders"] == 1
        st = db.stops.find_one({"id": s["id"]})
        assert st["pod_photo_path"] is None and st["erased_at"]
        rec = db.files.find_one({"storage_path": path})
        assert rec["is_deleted"] is True and rec["size"] == 0 and rec.get("deleted_at")
        g = requests.get(f"{BASE}/api/files/{path}", headers=admin)
        assert g.status_code == 404 and "erased" in g.json()["detail"]
        # stored object really overwritten: briefly un-flag the record and read the bytes back
        db.files.update_one({"storage_path": path}, {"$set": {"is_deleted": False}})
        try:
            raw = requests.get(f"{BASE}/api/files/{path}", headers=admin)
            assert raw.status_code == 200 and raw.content == b"", f"object not empty: {len(raw.content)} bytes"
        finally:
            db.files.update_one({"storage_path": path}, {"$set": {"is_deleted": True}})

    def test_retention_shreds_photo(self, admin, drv):
        path = upload(drv)
        s = mk_stop(customer_phone=CUST_RET, status="delivered", delivered_at="2019-01-01T12:00:00+00:00",
                    created_at="2019-01-01T10:00:00+00:00", pod_photo_path=path)
        r = requests.post(f"{BASE}/api/admin/privacy/run-retention", headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["erased_orders"] >= 1
        st = db.stops.find_one({"id": s["id"]})
        assert st["erased_at"] and st["pod_photo_path"] is None and st["customer_phone"] == ""
        assert db.files.find_one({"storage_path": path})["is_deleted"] is True
        g = requests.get(f"{BASE}/api/files/{path}", headers=admin)
        assert g.status_code == 404 and "erased" in g.json()["detail"]

    def test_unerased_photo_still_served(self, admin, drv):
        path = upload(drv)
        assert requests.get(f"{BASE}/api/files/{path}", headers=admin).status_code == 200


# ---- (2) missed pickup alerts
def run(h, at):
    return requests.post(f"{BASE}/api/admin/missed-pickups/run", params={"at": at}, headers=h)


class TestMissedPickup:
    def test_admin_only_and_bad_at(self, admin, merch, drv):
        assert requests.post(f"{BASE}/api/admin/missed-pickups/run", headers=NA).status_code == 401
        assert run(merch, f"{DAY}T14:40:00").status_code == 403
        assert run(drv, f"{DAY}T14:40:00").status_code == 403
        r = run(admin, "not-a-date")
        assert r.status_code == 400 and "ISO" in r.json()["detail"]

    def test_outside_window_empty(self, admin, route):
        mk_stop(route_id=route, merchant_id="mer-2", manifest_id=f"mer-2:{DAY}:afternoon")
        for at in (f"{DAY}T14:29:59", f"{DAY}T10:00:00", f"{DAY}T22:00:00"):
            r = run(admin, at)
            assert r.status_code == 200 and r.json()["sent"] == [], (at, r.text)

    def test_trigger_then_dedupe(self, admin, route):
        mid = f"mer-2:{DAY}:afternoon"
        mk_stop(manifest_id=mid, merchant_id="mer-2")  # no route -> counts as missing
        r = run(admin, f"{DAY}T14:30:00")
        assert r.status_code == 200 and r.json()["sent"] == [mid]
        doc = db.missed_pickup_alerts.find_one({"id": mid})
        assert doc and doc["missing"] == 2 and doc["run"] == "afternoon"
        assert run(admin, f"{DAY}T14:45:00").json()["sent"] == []
        assert db.missed_pickup_alerts.count_documents({"id": mid}) == 1

    def test_grace_boundary_and_evening(self, admin, route):
        mid = f"mer-2:{DAY}:evening"
        mk_stop(route_id=route, merchant_id="mer-2", manifest_id=mid)
        assert mid not in run(admin, f"{DAY}T17:29:59").json()["sent"]
        assert run(admin, f"{DAY}T20:29:59").json()["sent"] == [mid]

    def test_after_grace_no_alert(self, admin, route):
        mid = f"mer-2:{DAY2}:afternoon"
        mk_stop(route_id=route, merchant_id="mer-2", manifest_id=mid)
        assert run(admin, f"{DAY2}T17:30:00").json()["sent"] == []
        assert not db.missed_pickup_alerts.find_one({"id": mid})

    def test_all_checked_in_no_alert(self, admin, drv, route):
        mid = f"mer-2:{DAY2}:evening"
        mk_stop(route_id=route, merchant_id="mer-2", manifest_id=mid, customer_phone="+15195550196")
        r = requests.post(f"{BASE}/api/routes/{route}/pickups/{mid}/check-in", headers=drv, json={"parcel_count": 1})
        assert r.status_code == 200, r.text
        assert run(admin, f"{DAY2}T17:45:00").json()["sent"] == []
        assert not db.missed_pickup_alerts.find_one({"id": mid})


# ---- (3) left-store SMS
class TestLeftStoreSMS:
    def test_check_in_texts_open_stops(self, drv, route):
        mid = f"mer-2:{DAY}:evening"
        db.stops.delete_many({"manifest_id": mid})
        db.pickups.delete_many({"manifest_id": mid})
        a = mk_stop(route_id=route, merchant_id="mer-2", manifest_id=mid, customer_phone=LS_PHONES[0])
        b = mk_stop(route_id=route, merchant_id="mer-2", manifest_id=mid, customer_phone=LS_PHONES[1], status="arrived")
        c = mk_stop(route_id=route, merchant_id="mer-2", manifest_id=mid, customer_phone=LS_PHONES[2], status="delivered")
        r = requests.post(f"{BASE}/api/routes/{route}/pickups/{mid}/check-in", headers=drv, json={"parcel_count": 3})
        assert r.status_code == 200, r.text
        time.sleep(3)
        logs = {x["stop_id"]: x for x in db.contact_logs.find({"kind": "left_store", "stop_id": {"$in": [a["id"], b["id"], c["id"]]}})}
        assert set(logs) == {a["id"], b["id"]}
        name = db.merchants.find_one({"id": "mer-2"})["name"]
        for s in (a, b):
            m = logs[s["id"]]["message"]
            assert m == (f"DashLocal: your delivery {s['tracking_code']} from {name} has left the store and is on its way. "
                         f"Expected today 5:30 PM–8:30 PM (Eastern time). Someone will need to sign for it."), m
            assert not BANNED.search(m)
            assert logs[s["id"]]["status"] == "skipped_fictional" and logs[s["id"]]["channel"] == "sms"
        # dedupe per stop: re-check-in (after removing the pickup record) must not text again
        db.pickups.delete_many({"manifest_id": mid, "route_id": route})
        r = requests.post(f"{BASE}/api/routes/{route}/pickups/{mid}/check-in", headers=drv, json={"parcel_count": 3})
        assert r.status_code == 200
        time.sleep(3)
        for s in (a, b):
            assert db.contact_logs.count_documents({"kind": "left_store", "stop_id": s["id"]}) == 1

    def test_duplicate_check_in_400(self, drv, route):
        mid = f"mer-2:{DAY}:evening"
        r = requests.post(f"{BASE}/api/routes/{route}/pickups/{mid}/check-in", headers=drv, json={"parcel_count": 3})
        assert r.status_code == 400

    def test_store_name_with_banned_word(self, drv, route):
        """Store names come from the merchant record; a store called '... Pharmacy' must not leak into the text."""
        mid = f"mer-2:{DAY}:afternoon"
        db.pickups.delete_many({"manifest_id": mid})
        s = mk_stop(route_id=route, merchant_id="mer-2", manifest_id=mid, customer_phone="+15195550197")
        orig = db.merchants.find_one({"id": "mer-2"})["name"]
        db.merchants.update_one({"id": "mer-2"}, {"$set": {"name": "TEST Main St Pharmacy"}})
        try:
            r = requests.post(f"{BASE}/api/routes/{route}/pickups/{mid}/check-in", headers=drv, json={"parcel_count": 1})
            assert r.status_code == 200, r.text
            time.sleep(3)
        finally:
            db.merchants.update_one({"id": "mer-2"}, {"$set": {"name": orig}})
        m = db.contact_logs.find_one({"kind": "left_store", "stop_id": s["id"]})["message"]
        assert "2:30 PM–6:00 PM" in m
        assert not BANNED.search(m), f"banned word leaked via store name: {m}"
