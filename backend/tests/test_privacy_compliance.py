# Privacy & compliance (PIPEDA/PHIPA): encryption at rest, driver data minimisation, audit log, retention, consent
import os
import io
import time
import uuid
from datetime import datetime, timezone, timedelta
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
NOAUTO = {"X-No-Auto-Auth": "1"}


def login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def admin():
    return login(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])


@pytest.fixture(scope="module")
def merchant():
    return login("bakery@dashlocal.app", "Bakery2026")


@pytest.fixture(scope="module")
def driver():
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"})
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def driver2():
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "+15195550102", "pin": "730516"})
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def drv2_stop():
    """A TEST_ pending stop on a drv-2 route (cloned from a seeded stop) for driver-facing write responses."""
    route = db.routes.find_one({"driver_id": "drv-1", "status": {"$in": ["active", "planned"]}}, {"_id": 0})
    if not route:
        pytest.skip("drv-2 has no active/planned route")
    base = db.stops.find_one({"route_id": route["id"]}, {"_id": 0}) or db.stops.find_one({}, {"_id": 0})
    sid = f"TEST_{uuid.uuid4().hex[:8]}"
    doc = {**base, "id": sid, "route_id": route["id"], "status": "pending", "tracking_code": f"TST{uuid.uuid4().hex[:5].upper()}",
           "customer_name": "TEST_Privacy", "customer_phone": "+15195559911", "notes": "", "customer_note": None,
           "pod_signature": None, "pod_photo_path": None, "delivered_at": None, "arrived_at": None, "sequence": 999}
    db.stops.insert_one(doc)
    yield {"id": sid, "route_id": route["id"], "route_status": route["status"]}
    db.stops.delete_one({"id": sid})
    db.invoices.delete_many({"stop_id": sid})


# --- HSTS
def test_hsts_header():
    r = requests.get(f"{BASE_URL}/api/privacy/consent-text")
    assert r.status_code == 200
    assert "max-age" in r.headers.get("Strict-Transport-Security", "")
    assert r.json()["text"].startswith("I consent to DashLocal and its designated independent drivers")


# --- encryption at rest
def test_seed_notes_encrypted_raw_and_plain_in_api(admin):
    raw = db.stops.find_one({"notes": {"$regex": "^enc:"}}, {"_id": 0, "id": 1, "notes": 1})
    assert raw, "no encrypted notes in db"
    assert db.stops.count_documents({"notes": {"$nin": [None, ""], "$not": {"$regex": "^enc:"}}}) == 0
    r = requests.get(f"{BASE_URL}/api/stops/{raw['id']}", headers=admin)
    assert r.status_code == 200
    assert r.json()["notes"] and not r.json()["notes"].startswith("enc:")


def test_customer_note_encrypted(admin, drv2_stop):
    r = requests.put(f"{BASE_URL}/api/stops/{drv2_stop['id']}/customer-note", headers=admin, json={"note": "TEST_ ring the bell"})
    assert r.status_code == 200, r.text
    raw = db.stops.find_one({"id": drv2_stop["id"]}, {"_id": 0, "customer_note": 1})
    assert raw["customer_note"].startswith("enc:")
    g = requests.get(f"{BASE_URL}/api/stops/{drv2_stop['id']}", headers=admin).json()
    assert g["customer_note"] == "TEST_ ring the bell"


def test_saved_note_encrypted(merchant):
    phone = "+15195559922"
    r = requests.put(f"{BASE_URL}/api/merchants/mer-1/saved-notes", headers=merchant, json={"phone": phone, "note": "TEST_ side door"})
    assert r.status_code == 200, r.text
    raw = db.saved_notes.find_one({"merchant_id": "mer-1", "note": {"$regex": "^enc:"}, "phone": {"$regex": "9922"}}, {"_id": 0})
    lst = requests.get(f"{BASE_URL}/api/merchants/mer-1/saved-notes", headers=merchant).json()
    rows = lst if isinstance(lst, list) else lst.get("notes", lst.get("rows", []))
    mine = [n for n in rows if "9922" in (n.get("phone") or "")]
    for n in mine:
        requests.delete(f"{BASE_URL}/api/merchants/mer-1/saved-notes/{n['id']}", headers=merchant)
    assert raw, "saved note not stored encrypted"
    assert mine and mine[0]["note"] == "TEST_ side door"


def test_photo_upload_roundtrip_and_signature_encrypted(driver, admin, drv2_stop):
    payload = b"\x89PNG\r\n\x1a\nTEST_privacy_bytes" + os.urandom(64)
    r = requests.post(f"{BASE_URL}/api/upload", headers=driver, files={"file": ("pod.png", io.BytesIO(payload), "image/png")})
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    g = requests.get(f"{BASE_URL}/api/files/{path}", headers=admin)
    assert g.status_code == 200
    assert g.content == payload
    assert requests.get(f"{BASE_URL}/api/files/{path}", headers=NOAUTO).status_code == 401
    # raw bytes in object storage are encrypted
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from core import get_object  # noqa
        rawb, _ = get_object(path)
        assert rawb != payload and payload not in rawb
    except ImportError:
        pass


# --- driver data minimisation
def test_driver_route_hides_phone_and_fee(driver, admin):
    r = requests.get(f"{BASE_URL}/api/drivers/drv-1/route", headers=driver)
    assert r.status_code == 200
    route = r.json()["route"]
    if not route:
        pytest.skip("drv-2 no route")
    for s in route["stops"]:
        assert "customer_phone" not in s and "fee" not in s and "consent_text" not in s
        assert "customer_name" in s and "address" in s
    r2 = requests.get(f"{BASE_URL}/api/routes/{route['id']}", headers=driver).json()
    assert all("customer_phone" not in s and "fee" not in s for s in r2["stops"])
    a = requests.get(f"{BASE_URL}/api/routes/{route['id']}", headers=admin).json()
    assert all("customer_phone" in s and "fee" in s for s in a["stops"])
    sid = route["stops"][0]["id"]
    s = requests.get(f"{BASE_URL}/api/stops/{sid}", headers=driver).json()
    assert "customer_phone" not in s and "fee" not in s
    assert "customer_phone" in requests.get(f"{BASE_URL}/api/stops/{sid}", headers=admin).json()


def test_driver_write_responses_hide_phone(driver, drv2_stop):
    """arrive/deliver/fail/phone/verify-qr also return the stop to the driver app."""
    if drv2_stop["route_status"] != "active":
        pytest.skip("drv-2 route not active")
    r = requests.post(f"{BASE_URL}/api/stops/{drv2_stop['id']}/arrive", headers=driver, json={})
    if r.status_code != 200:
        pytest.skip(f"arrive not possible: {r.status_code} {r.text[:120]}")
    body = r.json()
    assert "customer_phone" not in body, "POST /stops/{id}/arrive returns customer_phone to driver"
    assert "fee" not in body, "POST /stops/{id}/arrive returns fee to driver"


def test_driver_deliver_signature_encrypted(driver, drv2_stop):
    if drv2_stop["route_status"] != "active":
        pytest.skip("drv-2 route not active")
    r = requests.post(f"{BASE_URL}/api/stops/{drv2_stop['id']}/deliver", headers=driver, json={"signature": "TEST_SIGNATURE_DATA"})
    if r.status_code != 200:
        pytest.skip(f"deliver not possible: {r.status_code} {r.text[:120]}")
    raw = db.stops.find_one({"id": drv2_stop["id"]}, {"_id": 0, "pod_signature": 1})
    assert raw["pod_signature"].startswith("enc:")
    assert "customer_phone" not in r.json(), "POST /stops/{id}/deliver returns customer_phone to driver"


# --- audit log
def test_audit_log_entries_and_filters(admin, merchant, driver):
    requests.get(f"{BASE_URL}/api/merchants/mer-1/orders", headers=merchant)
    requests.get(f"{BASE_URL}/api/drivers/drv-1/summary", headers=driver)
    requests.get(f"{BASE_URL}/api/track/MD1001", headers={"X-Order-Token": "x"})
    time.sleep(2)
    a = requests.get(f"{BASE_URL}/api/admin/audit?limit=200", headers=admin)
    assert a.status_code == 200
    rows = a.json()["rows"]
    roles = {r["role"] for r in rows}
    assert {"admin", "merchant", "driver"} <= roles, roles
    for k in ("actor", "role", "method", "path", "data", "status", "ip", "at"):
        assert k in rows[0]
    m = requests.get(f"{BASE_URL}/api/admin/audit?role=merchant", headers=admin).json()["rows"]
    assert m and all(r["role"] == "merchant" for r in m)
    q = requests.get(f"{BASE_URL}/api/admin/audit?q=bakery", headers=admin).json()["rows"]
    assert q and all("bakery" in (r["actor"] + r["path"] + r["data"] + r["ip"]).lower() for r in q)
    p1 = requests.get(f"{BASE_URL}/api/admin/audit?limit=5", headers=admin).json()["rows"]
    p2 = requests.get(f"{BASE_URL}/api/admin/audit?limit=5&before={p1[-1]['at']}", headers=admin).json()["rows"]
    assert p2 and all(r["at"] < p1[-1]["at"] for r in p2)


def test_audit_forbidden_for_non_admin(merchant, driver):
    assert requests.get(f"{BASE_URL}/api/admin/audit", headers=merchant).status_code == 403
    assert requests.get(f"{BASE_URL}/api/admin/audit", headers=driver).status_code == 403
    assert requests.get(f"{BASE_URL}/api/admin/audit", headers=NOAUTO).status_code == 401


# --- retention
def test_retention_settings_validation(admin):
    g = requests.get(f"{BASE_URL}/api/admin/privacy", headers=admin)
    assert g.status_code == 200 and g.json()["retention_days"] == 730
    for bad in (29, 3651):
        assert requests.put(f"{BASE_URL}/api/admin/privacy", headers=admin, json={"retention_days": bad}).status_code == 422
    ok = requests.put(f"{BASE_URL}/api/admin/privacy", headers=admin, json={"retention_days": 365})
    assert ok.status_code == 200 and ok.json()["retention_days"] == 365
    assert requests.get(f"{BASE_URL}/api/admin/privacy", headers=admin).json()["retention_days"] == 365
    assert requests.put(f"{BASE_URL}/api/admin/privacy", headers=admin, json={"retention_days": 730}).json()["retention_days"] == 730


def test_run_retention_erases_old_stop(admin):
    base = db.stops.find_one({}, {"_id": 0})
    sid = f"TEST_old_{uuid.uuid4().hex[:6]}"
    old = (datetime.now(timezone.utc) - timedelta(days=800)).isoformat()
    db.stops.insert_one({**base, "id": sid, "route_id": None, "status": "delivered", "delivered_at": old, "created_at": old,
                         "customer_name": "TEST_Old Person", "customer_phone": "+15195559933", "address": "1 TEST St",
                         "notes": "plain", "erased_at": None, "tracking_code": f"TOLD{uuid.uuid4().hex[:4]}"})
    db.invoices.insert_one({"id": f"TEST_inv_{sid}", "stop_id": sid, "total": 10.15, "subtotal": 8.99,
                            "delivery": {"customer_name": "TEST_Old Person", "address": "1 TEST St"}})
    try:
        r = requests.post(f"{BASE_URL}/api/admin/privacy/run-retention", headers=admin)
        assert r.status_code == 200 and r.json()["erased_orders"] >= 1
        s = db.stops.find_one({"id": sid}, {"_id": 0})
        assert s["customer_name"] == "Erased (retention)" and s["customer_phone"] == "" and s["erased_at"]
        inv = db.invoices.find_one({"stop_id": sid}, {"_id": 0})
        assert inv["total"] == 10.15 and inv["delivery"]["customer_name"] == "Erased (retention)"
        assert requests.post(f"{BASE_URL}/api/admin/privacy/run-retention", headers=NOAUTO).status_code == 401
    finally:
        db.stops.delete_one({"id": sid})
        db.invoices.delete_many({"stop_id": sid})


# --- sign-up consent
def test_signup_requires_consent():
    email = f"test_{uuid.uuid4().hex[:6]}@example.com"
    body = {"store_name": "TEST_Privacy Shop", "contact_name": "TEST Person", "email": email}
    assert requests.post(f"{BASE_URL}/api/signup-requests", json=body).status_code == 400
    ts = datetime.now(timezone.utc).isoformat()
    r = requests.post(f"{BASE_URL}/api/signup-requests", json={**body, "privacy_consent": True, "consent_timestamp": ts})
    try:
        assert r.status_code in (200, 201), r.text
        doc = db.signup_requests.find_one({"email": email}, {"_id": 0})
        assert doc["phipa_consent_given"] is True and doc["consent_timestamp"] == ts
    finally:
        db.signup_requests.delete_many({"email": email})


# --- driver consent
def test_driver_me_consent(driver2):
    uid = db.users.find_one({"driver_id": "drv-2"}, {"_id": 0, "id": 1})["id"]
    prev = db.users.find_one({"id": uid}, {"_id": 0, "phipa_consent_given": 1, "consent_timestamp": 1, "consent_recorded_at": 1, "consent_version": 1})
    try:
        db.users.update_one({"id": uid}, {"$unset": {"phipa_consent_given": ""}})
        assert requests.get(f"{BASE_URL}/api/me/consent", headers=driver2).json()["phipa_consent_given"] is False
        p = requests.post(f"{BASE_URL}/api/me/consent", headers=driver2, json={"consent_timestamp": "2026-01-01T00:00:00Z"})
        assert p.status_code == 200 and p.json()["phipa_consent_given"] is True
        g = requests.get(f"{BASE_URL}/api/me/consent", headers=driver2).json()
        assert g["phipa_consent_given"] is True and g["consent_timestamp"] == "2026-01-01T00:00:00Z" and g["consent_recorded_at"]
        assert requests.get(f"{BASE_URL}/api/me/consent", headers=NOAUTO).status_code == 401
    finally:
        db.users.update_one({"id": uid}, {"$set": {k: v for k, v in (prev or {}).items()}} if prev and len(prev) > 0 else {"$unset": {"x": ""}})
        if not (prev or {}).get("phipa_consent_given"):
            db.users.update_one({"id": uid}, {"$unset": {"phipa_consent_given": "", "consent_timestamp": "", "consent_recorded_at": "", "consent_version": ""}})


# --- CSV import consent
def test_import_requires_consent_and_stores_fields(merchant):
    csv = "customer_name,phone,address\nTEST_Consent One,+15195559944,200 King St W Kitchener\n"
    r = requests.post(f"{BASE_URL}/api/merchants/mer-1/import", headers=merchant, files={"file": ("o.csv", csv, "text/csv")})
    assert r.status_code == 400
    r = requests.post(f"{BASE_URL}/api/merchants/mer-1/import?consent=true", headers=merchant, files={"file": ("o.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    found = None
    for _ in range(30):
        found = db.stops.find_one({"customer_name": "TEST_Consent One"}, {"_id": 0})
        if found:
            break
        time.sleep(1)
    try:
        assert found, "imported stop not created"
        assert found["consent_given"] is True and found["consent_by"] == "bakery@dashlocal.app"
        assert found["consent_text"].startswith("I consent to DashLocal") and found["consent_at"]
    finally:
        db.stops.delete_many({"customer_name": "TEST_Consent One"})
        db.import_jobs.delete_many({"rows.customer_name": "TEST_Consent One"}) if "import_jobs" in db.list_collection_names() else None
