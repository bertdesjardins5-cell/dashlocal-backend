"""Server-side terms enforcement: drivers can't work manifests and stores can't send/change deliveries until they accept."""
import os
import requests
from pymongo import MongoClient

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://dashlocal.preview.emergentagent.com").rstrip("/")
users = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]].users
NO = {"X-No-Auto-Auth": "1"}


def login(path, body):
    r = requests.post(f"{BASE}/api/auth/{path}", json=body, headers=NO)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **NO}


def test_driver_blocked_until_terms_accepted():
    u = users.find_one({"role": "driver", "driver_id": "drv-3"}, {"_id": 0, "id": 1, "driver_terms_version": 1})
    users.update_one({"id": u["id"]}, {"$unset": {"driver_terms_version": ""}})
    try:
        h = login("driver-login", {"phone": "(519) 555-0103", "pin": "264871"})
        r = requests.get(f"{BASE}/api/drivers/drv-3/route", headers=h)
        assert r.status_code == 403 and "courier terms" in r.json()["detail"]
        assert requests.post(f"{BASE}/api/auth/accept-terms", headers=h).status_code == 200
        assert requests.get(f"{BASE}/api/drivers/drv-3/route", headers=h).status_code != 403
    finally:
        users.update_one({"id": u["id"]}, {"$set": {"driver_terms_version": u["driver_terms_version"]}} if u.get("driver_terms_version") else {"$unset": {"driver_terms_version": ""}})


def test_store_reads_ok_writes_blocked_payments_allowed():
    u = users.find_one({"email": "books@dashlocal.app"}, {"_id": 0, "id": 1, "terms_version": 1})
    users.update_one({"id": u["id"]}, {"$unset": {"terms_version": ""}})
    try:
        h = login("login", {"email": "books@dashlocal.app", "password": "Books2026"})
        assert requests.get(f"{BASE}/api/merchants/mer-2", headers=h).status_code == 200
        r = requests.put(f"{BASE}/api/merchants/mer-2/stops/nope/amount-due", json={"amount": 5}, headers=h)
        assert r.status_code == 403 and "Terms of Service" in r.json()["detail"]
        r = requests.post(f"{BASE}/api/merchants/mer-2/invoices/square-pay", json={"ids": []}, headers=h)
        assert r.status_code != 403  # paying what they owe stays possible
        assert requests.post(f"{BASE}/api/auth/accept-terms", headers=h).status_code == 200
        r = requests.put(f"{BASE}/api/merchants/mer-2/stops/nope/amount-due", json={"amount": 5}, headers=h)
        assert r.status_code == 404
    finally:
        users.update_one({"id": u["id"]}, {"$set": {"terms_version": u["terms_version"]}} if u.get("terms_version") else {"$unset": {"terms_version": ""}})
