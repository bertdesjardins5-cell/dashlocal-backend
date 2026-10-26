# Iteration 34: PIPEDA customer data requests (lookup/export/erase) + pharmacy pickup check-in + batch tracker data
import os
import time
import json
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
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
CUST = "+15195550977"   # TEST erase customer
PICK = ["+15195550978", "+15195550979"]  # TEST pickup drops


def login(email, pw):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": pw}, headers=NA)
    if r.status_code != 200:
        pytest.skip(f"login {email} failed {r.status_code} {r.text}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NA}


def driver(phone, pin):
    r = requests.post(f"{BASE}/api/auth/driver-login", json={"phone": phone, "pin": pin}, headers=NA)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code} {r.text}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NA}


def import_csv(h, rows, fname):
    csv = "customer_name,phone,address,notes\n" + "".join(f"{n},{p},{a},{nt}\n" for n, p, a, nt in rows)
    r = requests.post(f"{BASE}/api/merchants/mer-2/import?run=evening&consent=true", headers=h, files={"file": (fname, csv, "text/csv")})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    for _ in range(40):
        if requests.get(f"{BASE}/api/imports/{jid}", headers=h).json()["status"] != "running":
            break
        time.sleep(1)
    return jid, [x["stop_id"] for x in db.import_rows.find({"job_id": jid}) if x.get("stop_id")]


def cleanup(jids):
    for jid in jids:
        ids = [x["stop_id"] for x in db.import_rows.find({"job_id": jid}) if x.get("stop_id")]
        mids = {s.get("manifest_id") for s in db.stops.find({"id": {"$in": ids}})}
        db.stops.delete_many({"id": {"$in": ids}})
        db.invoices.delete_many({"stop_id": {"$in": ids}})
        db.import_rows.delete_many({"job_id": jid})
        db.import_jobs.delete_one({"id": jid})
        for m in mids:
            if m and not db.stops.find_one({"manifest_id": m}):
                db.pickups.delete_many({"manifest_id": m})
    db.saved_notes.delete_many({"phone": {"$in": [CUST, *PICK]}})
    db.data_requests.delete_many({"phone": {"$in": [CUST, *PICK, "+15195550105"]}})


@pytest.fixture(scope="module")
def admin():
    return login("admin@dashlocal.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def merch():
    return login("books@dashlocal.app", "Books2026")


@pytest.fixture(scope="module")
def cust_job(merch):
    jid, ids = import_csv(merch, [("TEST_Erase Me", CUST, "200 King St W Kitchener", "Gate code 4411")], "TEST_erase.csv")
    assert ids, "import produced no stop"
    r = requests.put(f"{BASE}/api/merchants/mer-2/saved-notes", headers=merch, json={"phone": CUST, "note": "TEST leave at side door"})
    assert r.status_code == 200, r.text
    yield ids
    cleanup([jid])


# ---- customer lookup / validation
class TestCustomerLookup:
    def test_bad_phone_400(self, admin):
        r = requests.get(f"{BASE}/api/admin/customers/lookup?phone=abc", headers=admin)
        assert r.status_code == 400

    def test_non_admin_403(self, merch):
        r = requests.get(f"{BASE}/api/admin/customers/lookup?phone={CUST}", headers=merch)
        assert r.status_code == 403
        r = requests.post(f"{BASE}/api/admin/customers/erase", headers=merch, json={"phone": CUST, "confirm": "ERASE"})
        assert r.status_code == 403

    def test_no_auth_401(self):
        r = requests.get(f"{BASE}/api/admin/customers/lookup?phone={CUST}", headers=NA)
        assert r.status_code == 401

    def test_lookup_demo(self, admin):
        r = requests.get(f"{BASE}/api/admin/customers/lookup", params={"phone": "(519) 555-0105"}, headers=admin)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["phone"] == "+15195550105" and d["orders"] >= 1 and isinstance(d["requests"], list)
        assert "_id" not in json.dumps(d)

    def test_demo_open_order_409(self, admin):
        r = requests.post(f"{BASE}/api/admin/customers/erase", headers=admin, json={"phone": "+15195550105", "confirm": "ERASE"})
        assert r.status_code == 409, r.text


# ---- export + erase full flow on a TEST customer
class TestExportErase:
    def test_lookup_test_customer(self, admin, cust_job):
        d = requests.get(f"{BASE}/api/admin/customers/lookup?phone={CUST}", headers=admin).json()
        assert d["orders"] == 1 and d["saved_notes"] == 1 and "TEST_Erase Me" in d["names"]

    def test_export_link_and_file(self, admin, cust_job):
        r = requests.post(f"{BASE}/api/admin/customers/export-link?phone={CUST}", headers=admin)
        assert r.status_code == 200 and r.json()["expires_in"] == 300
        f = requests.get(f"{BASE}{r.json()['path']}", headers=NA)
        assert f.status_code == 200, f.text
        assert "attachment" in f.headers.get("content-disposition", "") and "15195550977" in f.headers["content-disposition"]
        body = f.json()
        assert body["phone"] == CUST and len(body["orders"]) == 1
        o = body["orders"][0]
        assert "Gate code 4411" in (o["notes"] or ""), o["notes"]
        assert not str(o["notes"]).startswith("enc:")
        assert body["saved_notes"][0]["note"] == "TEST leave at side door"
        d = requests.get(f"{BASE}/api/admin/customers/lookup?phone={CUST}", headers=admin).json()
        assert any(q["kind"] == "export" for q in d["requests"])

    def test_export_invalid_expired_token_401(self):
        assert requests.get(f"{BASE}/api/admin/customers/export.json?token=garbage", headers=NA).status_code == 401
        now = datetime.now(timezone.utc)
        exp = pyjwt.encode({"sub": "x", "role": "admin", "purpose": "customer-export", "phone": CUST, "iat": now - timedelta(minutes=10),
                            "exp": now - timedelta(minutes=5)}, os.environ["JWT_SECRET"], algorithm="HS256")
        assert requests.get(f"{BASE}/api/admin/customers/export.json?token={exp}", headers=NA).status_code == 401

    def test_session_token_not_export_token(self, admin):
        tok = admin["Authorization"][7:]
        assert requests.get(f"{BASE}/api/admin/customers/export.json?token={tok}", headers=NA).status_code == 401

    def test_erase_pending_409(self, admin, cust_job):
        r = requests.post(f"{BASE}/api/admin/customers/erase", headers=admin, json={"phone": CUST, "confirm": "ERASE"})
        assert r.status_code == 409, r.text

    def test_erase_wrong_confirm_400(self, admin, cust_job):
        r = requests.post(f"{BASE}/api/admin/customers/erase", headers=admin, json={"phone": CUST, "confirm": "erase"})
        assert r.status_code == 400

    def test_erase_closed_orders(self, admin, cust_job):
        db.stops.update_many({"id": {"$in": cust_job}}, {"$set": {"status": "delivered", "delivered_at": datetime.now(timezone.utc).isoformat()}})
        r = requests.post(f"{BASE}/api/admin/customers/erase", headers=admin, json={"phone": CUST, "confirm": "ERASE"})
        assert r.status_code == 200, r.text
        assert r.json() == {"erased_orders": 1, "deleted_saved_notes": 1}
        s = db.stops.find_one({"id": cust_job[0]})
        assert s["customer_name"] == "Erased (customer request)" and s["customer_phone"] == "" and s["notes"] == "" and s["erased_at"]
        assert db.saved_notes.count_documents({"phone": CUST}) == 0
        d = requests.get(f"{BASE}/api/admin/customers/lookup?phone={CUST}", headers=admin).json()
        assert d["orders"] == 0 and d["saved_notes"] == 0
        kinds = [q["kind"] for q in d["requests"]]
        assert "erase" in kinds and "export" in kinds


# ---- pickup check-in
@pytest.fixture(scope="module")
def pick_setup(merch):
    jid, ids = import_csv(merch, [(f"TEST_Pick {i}", p, "200 King St W Kitchener", "") for i, p in enumerate(PICK)], "TEST_pick.csv")
    route = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    if not route or len(ids) != 2:
        cleanup([jid])
        pytest.skip("no active drv-1 route or import failed")
    db.stops.update_many({"id": {"$in": ids}}, {"$set": {"route_id": route["id"]}})
    s = db.stops.find_one({"id": ids[0]})
    yield {"route": route["id"], "manifest": s["manifest_id"], "ids": ids}
    cleanup([jid])


class TestPickupCheckIn:
    def test_route_has_pickup(self, pick_setup):
        h = driver("+15195550101", "482913")
        d = requests.get(f"{BASE}/api/routes/{pick_setup['route']}", headers=h).json()
        p = [x for x in d.get("pickups", []) if x["manifest_id"] == pick_setup["manifest"]]
        assert len(p) == 1, d.get("pickups")
        p = p[0]
        assert p["drops"] == 2 and p["run"] == "evening" and p["picked_up_at"] is None and p["pharmacy"]
        assert p["pickup_label"] and "pickup_address" in p

    def test_other_driver_403(self, pick_setup):
        h = driver("+15195550102", "730516")
        r = requests.post(f"{BASE}/api/routes/{pick_setup['route']}/pickups/{pick_setup['manifest']}/check-in", headers=h, json={"parcel_count": 2})
        assert r.status_code == 403

    def test_bad_count_422_and_404(self, pick_setup):
        h = driver("+15195550101", "482913")
        r = requests.post(f"{BASE}/api/routes/{pick_setup['route']}/pickups/{pick_setup['manifest']}/check-in", headers=h, json={"parcel_count": -1})
        assert r.status_code == 422
        r = requests.post(f"{BASE}/api/routes/{pick_setup['route']}/pickups/mer-2:2020-01-01:evening/check-in", headers=h, json={"parcel_count": 1})
        assert r.status_code == 404

    def test_check_in_then_duplicate(self, pick_setup, merch):
        h = driver("+15195550101", "482913")
        url = f"{BASE}/api/routes/{pick_setup['route']}/pickups/{pick_setup['manifest']}/check-in"
        r = requests.post(url, headers=h, json={"parcel_count": 2})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["parcel_count"] == 2 and d["expected"] >= 2 and d["on_time"] is True and d["driver_id"] == "drv-1"
        assert requests.post(url, headers=h, json={"parcel_count": 2}).status_code == 400
        rd = requests.get(f"{BASE}/api/routes/{pick_setup['route']}", headers=h).json()
        p = [x for x in rd["pickups"] if x["manifest_id"] == pick_setup["manifest"]][0]
        assert p["picked_up_at"] and p["parcel_count"] == 2 and p["on_time"] is True
        m = requests.get(f"{BASE}/api/merchants/mer-2", headers=merch).json()
        ev = [x for x in m["runs"] if x["key"] == "evening"][0]
        assert ev["cutoff_at"] and ev["drops_today"] >= 2
        assert ev["pickup"] and ev["pickup"]["parcel_count"] == 2
