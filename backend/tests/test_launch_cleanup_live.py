# DESTRUCTIVE: live-host launch cleanup simulation (wipes everything except the admin login).
# Only runs with LAUNCH_CLEANUP_DESTRUCTIVE=1. Phase "wipe" runs the cleanup; phase "after_restart" (set
# LAUNCH_CLEANUP_PHASE=after_restart after `sudo supervisorctl restart backend`) checks nothing was re-seeded.
# Restore afterwards: db.settings.delete_one({key:'demo_removed'}) + restart backend (startup re-seeds demo data).
import os
import requests
import pytest
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
pytestmark = pytest.mark.skipif(os.environ.get("LAUNCH_CLEANUP_DESTRUCTIVE") != "1", reason="destructive; opt-in only")
PHASE = os.environ.get("LAUNCH_CLEANUP_PHASE", "wipe")
LOCAL = "http://localhost:8001"
LIVE = {"Host": "dashlocal.app", "X-No-Auto-Auth": "1"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def admin_tok():
    r = requests.post(f"{LOCAL}/api/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}, headers=LIVE)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def H(tok):
    return {**LIVE, "Authorization": f"Bearer {tok}"}


@pytest.mark.skipif(PHASE != "wipe", reason="wipe phase")
def test_live_wipe():
    keep = {c: db[c].count_documents({}) for c in ("audit_log", "consent_log")}
    settings_before = db.settings.count_documents({})
    tok = admin_tok()
    r = requests.post(f"{LOCAL}/api/admin/launch-cleanup", json={"confirm": "DELETE"}, headers=H(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    print("removed:", body["removed"])
    assert body["ok"] is True and body["removed"]["drivers"] >= 3 and body["removed"]["users"] >= 1
    users = list(db.users.find({}, {"_id": 0, "email": 1, "role": 1}))
    assert users and all(u["role"] == "admin" for u in users), users
    for c in ("merchants", "drivers", "stops", "routes", "invoices"):
        assert db[c].count_documents({}) == 0, c
    for c, n in keep.items():
        assert db[c].count_documents({}) >= n, c
    assert db.settings.count_documents({}) >= settings_before
    assert db.files.count_documents({"is_deleted": {"$ne": True}}) == 0
    assert db.settings.find_one({"key": "demo_removed"})
    r = requests.post(f"{LOCAL}/api/admin/reset", headers=H(tok))
    assert r.status_code == 400 and "Demo data was removed" in r.json()["detail"], r.text
    s = requests.get(f"{LOCAL}/api/admin/launch-cleanup", headers=H(tok)).json()
    assert s["done_at"] and s["available"] is False and s["counts"]["drivers"] == 0 and s["counts"]["logins"] == 0
    again = requests.post(f"{LOCAL}/api/admin/launch-cleanup", json={"confirm": "DELETE"}, headers=H(tok))
    print("second run:", again.status_code, again.text[:200])


@pytest.mark.skipif(PHASE != "after_restart", reason="after-restart phase")
def test_after_restart_nothing_reseeded():
    assert db.drivers.count_documents({}) == 0
    assert db.merchants.count_documents({}) == 0 and db.stops.count_documents({}) == 0
    assert db.users.count_documents({"role": {"$ne": "admin"}}) == 0
    r = requests.post(f"{LOCAL}/api/auth/login", json={"email": "bakery@dashlocal.app", "password": "Bakery2026"}, headers=LIVE)
    assert r.status_code == 401, r.text
    tok = admin_tok()
    s = requests.get(f"{LOCAL}/api/admin/launch-cleanup", headers=H(tok)).json()
    assert s["done_at"] and s["counts"]["drivers"] == 0
