# Store-centred pricing (iter 71): PUT /merchants/{id}/pickup-address, free_zone origin, fee-estimate & new-order km from store
import os
import time
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
STORE = "mer-review"
DEPOT_STORE = "mer-a14d11dc"
ADDR = "550 King St N, Waterloo"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
created_orders = []


def login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def admin():
    return login("admin@dashlocal.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def bakery():
    return login("bakery@dashlocal.app", "Bakery2026")


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    before = {m["id"]: m.get("pickup") for m in db.merchants.find({"id": {"$in": ["mer-1", "mer-2"]}}, {"_id": 0, "id": 1, "pickup": 1})}
    yield
    db.merchants.update_many({"id": {"$in": [STORE, DEPOT_STORE]}}, {"$unset": {"pickup": "", "pickup_updated_at": ""}})
    for sid in created_orders:
        db.stops.delete_one({"id": sid})
    after = {m["id"]: m.get("pickup") for m in db.merchants.find({"id": {"$in": ["mer-1", "mer-2"]}}, {"_id": 0, "id": 1, "pickup": 1})}
    assert before == after, "mer-1/mer-2 pickup changed!"


# --- auth / validation
def test_put_no_auth_401():
    r = requests.put(f"{API}/merchants/{STORE}/pickup-address", json={"address": ADDR}, headers={"X-No-Auto-Auth": "1"})
    assert r.status_code == 401


def test_put_other_store_forbidden(bakery):
    r = requests.put(f"{API}/merchants/{STORE}/pickup-address", json={"address": ADDR}, headers=bakery)
    assert r.status_code == 403, r.text
    assert not (db.merchants.find_one({"id": STORE}) or {}).get("pickup")


def test_put_short_address_400(admin):
    r = requests.put(f"{API}/merchants/{STORE}/pickup-address", json={"address": " ab "}, headers=admin)
    assert r.status_code == 400 and "street address" in r.json()["detail"]


def test_put_unknown_address_400(admin):
    time.sleep(1)
    r = requests.put(f"{API}/merchants/{STORE}/pickup-address", json={"address": "99999 Zzqxv Nowhere Blvd"}, headers=admin)
    assert r.status_code == 400, r.text
    assert "Kitchener-Waterloo" in r.json()["detail"]


def test_put_outside_kw_400(admin):
    time.sleep(1)
    r = requests.put(f"{API}/merchants/{STORE}/pickup-address", json={"address": "100 Queen St W, Toronto"}, headers=admin)
    print("outside KW:", r.status_code, r.text[:200])
    assert r.status_code == 400, r.text


def test_put_unknown_merchant_404(admin):
    r = requests.put(f"{API}/merchants/mer-doesnotexist/pickup-address", json={"address": ADDR}, headers=admin)
    assert r.status_code in (403, 404)


# --- depot fallback before save
def test_depot_store_free_zone(admin):
    r = requests.get(f"{API}/merchants/{DEPOT_STORE}", headers=admin)
    assert r.status_code == 200
    fz = r.json()["free_zone"]
    assert fz["from"] == "depot" and abs(fz["lat"] - 43.4516) < 1e-4 and abs(fz["lng"] + 80.4925) < 1e-4


def test_pharmacy_unchanged(admin):
    fz = requests.get(f"{API}/merchants/mer-2", headers=admin).json()["free_zone"]
    assert fz["from"] == "pharmacy" and fz["address"] == "385 Fairway Rd S, Kitchener"


def test_order_before_save_priced_from_depot(admin):
    time.sleep(1)
    r = requests.post(f"{API}/merchants/{STORE}/orders", headers=admin, json={
        "customer_name": "TEST_Before Save", "phone": "+15195559872", "address": ADDR, "consent": True})
    assert r.status_code == 200, r.text
    created_orders.append(r.json()["id"])
    assert r.json()["fee"]["km"] > 3.0


# --- save + effects
def test_save_and_free_zone_from_store(admin):
    old = db.stops.find_one({"merchant_id": STORE, "fee": {"$ne": None}}, {"_id": 0, "id": 1, "fee": 1})
    assert old, "no pre-existing priced order"
    time.sleep(1)
    r = requests.put(f"{API}/merchants/{STORE}/pickup-address", json={"address": "  550   King St N,  Waterloo "}, headers=admin)
    assert r.status_code == 200, r.text
    pk = r.json()["pickup"]
    assert pk["address"] == ADDR and 43.4 < pk["lat"] < 43.55 and -80.6 < pk["lng"] < -80.4
    m = db.merchants.find_one({"id": STORE}, {"_id": 0})
    assert m["pickup"] == pk and m.get("pickup_updated_at")
    fz = requests.get(f"{API}/merchants/{STORE}", headers=admin).json()["free_zone"]
    assert fz["from"] == "store" and fz["address"] == ADDR and fz["lat"] == pk["lat"] and fz["lng"] == pk["lng"]
    if old:  # existing orders keep their stored fee
        assert db.stops.find_one({"id": old["id"]}, {"_id": 0, "fee": 1})["fee"] == old["fee"]


def test_fee_estimate_from_store_zero_km(admin):
    time.sleep(1)
    r = requests.post(f"{API}/merchants/{STORE}/fee-estimate", json={"address": ADDR}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["km"] == 0.0 and r.json()["distance_fee"] == 0.0


def test_fee_estimate_depot_store_nonzero(admin):
    time.sleep(1)
    r = requests.post(f"{API}/merchants/{DEPOT_STORE}/fee-estimate", json={"address": ADDR}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["km"] > 3.0  # measured from depot (~6 km straight × 1.3)


def test_new_order_priced_from_store(admin):
    time.sleep(1)
    r = requests.post(f"{API}/merchants/{STORE}/orders", headers=admin, json={
        "customer_name": "TEST_Store Pickup", "phone": "+15195559871", "address": ADDR, "consent": True})
    assert r.status_code == 200, r.text
    s = r.json()
    created_orders.append(s["id"])
    assert s["fee"]["km"] == 0.0 and s["fee"]["distance_fee"] == 0.0


def test_merchant_sets_own_store(bakery, admin):
    # mer-1 may hit its own endpoint: verify with an invalid address only (do NOT change mer-1 pickup)
    r = requests.put(f"{API}/merchants/mer-1/pickup-address", json={"address": "ab"}, headers=bakery)
    assert r.status_code == 400  # passed merchant_access, failed validation
