# Iteration 38: driver compliment shout-outs (5★ + positive tag -> db.driver_shoutouts; new/seen endpoints + access control)
import os
import uuid
from datetime import datetime, timezone

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
PHONE = "+15195550185"  # fictional only (Twilio LIVE)


def _login_driver(phone, pin):
    r = requests.post(f"{BASE}/api/auth/driver-login", json={"phone": phone, "pin": pin}, headers=NA)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NA}


@pytest.fixture(scope="module")
def admin():
    r = requests.post(f"{BASE}/api/auth/login", json={"email": "admin@arrivewise.app", "password": "DashLocalAdmin2026"}, headers=NA)
    if r.status_code != 200:
        pytest.skip("admin login failed")
    return {"Authorization": f"Bearer {r.json()['token']}", **NA}


@pytest.fixture(scope="module")
def drv1():
    return _login_driver("+15195550101", "482913")


@pytest.fixture(scope="module")
def drv2():
    return _login_driver("+15195550102", "730516")


def _route(driver):
    r = db.routes.find_one({"driver_id": driver, "status": {"$ne": "active"}}) or db.routes.find_one({"driver_id": driver})
    if not r:
        pytest.skip(f"no {driver} route")
    return r["id"]


def mk_stop(driver="drv-1", **kw):
    sid = "TEST_SO_" + uuid.uuid4().hex[:10]
    d = {"id": sid, "route_id": _route(driver) if driver else None, "seq": 99, "customer_name": "TEST_Shout Patient",
         "customer_phone": PHONE, "merchant_id": "mer-2", "address": "200 King St W Kitchener", "postal_code": "N2G 1A7",
         "zone": "Downtown", "lat": 43.45, "lng": -80.49, "status": "delivered", "delivered_at": datetime.now(timezone.utc).isoformat(),
         "tracking_code": "TSO" + uuid.uuid4().hex[:6].upper(), "notes": "", "created_at": "2026-01-10T10:00:00+00:00", "erased_at": None}
    d.update(kw)
    db.stops.insert_one(dict(d))
    return d


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    before = {x["id"] for x in db.driver_shoutouts.find({}, {"id": 1})}
    yield
    ids = [s["id"] for s in db.stops.find({"id": {"$regex": "^TEST_SO_"}}, {"id": 1})]
    db.driver_shoutouts.delete_many({"stop_id": {"$in": ids}})
    db.driver_shoutouts.delete_many({"id": {"$nin": list(before)}, "stop_id": {"$regex": "^TEST_SO_"}})
    db.stops.delete_many({"id": {"$in": ids}})
    db.contact_logs.delete_many({"stop_id": {"$in": ids}})
    db.login_attempts.delete_many({"key": {"$regex": "^trk:TSO"}})


def rate(admin, s, stars, tags):
    r = requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=admin, json={"stars": stars, "tags": tags})
    assert r.status_code == 200, r.text
    return r


def shout(sid):
    return list(db.driver_shoutouts.find({"stop_id": sid}, {"_id": 0}))


# ---- creation rules
class TestShoutoutCreation:
    def test_5star_positive_creates_with_positive_only_tags(self, admin):
        s = mk_stop()
        rate(admin, s, 5, ["on time", "LATE", "Discreet", "BOGUS"])
        rows = shout(s["id"])
        assert len(rows) == 1
        so = rows[0]
        assert so["driver_id"] == "drv-1" and so["seen_at"] is None
        assert so["tags"] == ["ON TIME", "DISCREET"]
        assert set(so) == {"id", "driver_id", "stop_id", "tags", "created_at", "seen_at"}

    @pytest.mark.parametrize("stars,tags", [(5, []), (5, ["LATE", "NOT DISCREET"]), (4, ["ON TIME", "FRIENDLY DRIVER"]),
                                            (1, ["ON TIME"]), (5, ["BOGUS", "CAREFUL WITH PARCEL"])])
    def test_no_shoutout_cases(self, admin, stars, tags):
        s = mk_stop()
        rate(admin, s, stars, tags)
        assert shout(s["id"]) == []

    def test_one_per_stop(self, admin):
        s = mk_stop()
        rate(admin, s, 5, ["FRIENDLY DRIVER"])
        assert requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=admin, json={"stars": 5, "tags": ["ON TIME"]}).status_code == 400
        # force a re-rate (rating cleared) -> unique index keeps one doc, no 500
        db.stops.update_one({"id": s["id"]}, {"$set": {"rating": None}})
        rate(admin, s, 5, ["ON TIME"])
        rows = shout(s["id"])
        assert len(rows) == 1 and rows[0]["tags"] == ["FRIENDLY DRIVER"]

    def test_unrouted_stop_no_shoutout(self, admin):
        s = mk_stop(driver=None)
        rate(admin, s, 5, ["ON TIME"])
        assert shout(s["id"]) == []

    def test_customer_unlock_rating_creates(self):
        s = mk_stop(driver="drv-2")
        r = requests.post(f"{BASE}/api/track/{s['tracking_code']}/verify", json={"last4": PHONE[-4:]}, headers=NA)
        assert r.status_code == 200, r.text
        h = {"x-order-token": r.json()["token"], **NA}
        r = requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=h, json={"stars": 5, "tags": ["HANDLED WITH CARE", "EASY TO FOLLOW"]})
        assert r.status_code == 200, r.text
        rows = shout(s["id"])
        assert len(rows) == 1 and rows[0]["driver_id"] == "drv-2"
        assert rows[0]["tags"] == ["HANDLED WITH CARE", "EASY TO FOLLOW"]


# ---- new / seen endpoints
class TestShoutoutEndpoints:
    def test_new_seen_flow_and_access(self, admin, drv1, drv2):
        db.driver_shoutouts.update_many({"driver_id": "drv-1", "seen_at": None}, {"$set": {"seen_at": "2026-01-01T00:00:00+00:00"}})
        s = mk_stop()
        rate(admin, s, 5, ["ON TIME", "DISCREET"])
        r = requests.get(f"{BASE}/api/drivers/drv-1/shoutouts/new", headers=drv1)
        assert r.status_code == 200, r.text
        rows = r.json()["shoutouts"]
        assert len(rows) == 1
        assert "stop_id" not in rows[0] and "_id" not in rows[0]
        assert rows[0]["tags"] == ["ON TIME", "DISCREET"]
        assert not any(k in str(rows[0]) for k in ["TEST_Shout", PHONE, "King St"])
        # admin can read too
        assert len(requests.get(f"{BASE}/api/drivers/drv-1/shoutouts/new", headers=admin).json()["shoutouts"]) == 1
        # other driver / anon
        assert requests.get(f"{BASE}/api/drivers/drv-1/shoutouts/new", headers=drv2).status_code == 403
        assert requests.post(f"{BASE}/api/drivers/drv-1/shoutouts/seen", headers=drv2).status_code == 403
        assert requests.get(f"{BASE}/api/drivers/drv-1/shoutouts/new", headers=NA).status_code == 401
        assert requests.post(f"{BASE}/api/drivers/drv-1/shoutouts/seen", headers=NA).status_code == 401
        # still unseen after forbidden calls
        assert len(requests.get(f"{BASE}/api/drivers/drv-1/shoutouts/new", headers=drv1).json()["shoutouts"]) == 1
        # mark seen
        r = requests.post(f"{BASE}/api/drivers/drv-1/shoutouts/seen", headers=drv1)
        assert r.status_code == 200 and r.json()["marked"] == 1
        assert db.driver_shoutouts.find_one({"stop_id": s["id"]})["seen_at"] is not None
        assert requests.get(f"{BASE}/api/drivers/drv-1/shoutouts/new", headers=drv1).json()["shoutouts"] == []
        assert requests.post(f"{BASE}/api/drivers/drv-1/shoutouts/seen", headers=drv1).json()["marked"] == 0

    def test_seen_scoped_to_driver(self, admin, drv1, drv2):
        s1, s2 = mk_stop(), mk_stop(driver="drv-2")
        rate(admin, s1, 5, ["ON TIME"])
        rate(admin, s2, 5, ["DISCREET"])
        assert requests.post(f"{BASE}/api/drivers/drv-1/shoutouts/seen", headers=drv1).json()["marked"] >= 1
        assert db.driver_shoutouts.find_one({"stop_id": s2["id"]})["seen_at"] is None
        r = requests.get(f"{BASE}/api/drivers/drv-2/shoutouts/new", headers=drv2)
        assert r.status_code == 200 and any(x["tags"] == ["DISCREET"] for x in r.json()["shoutouts"])
        requests.post(f"{BASE}/api/drivers/drv-2/shoutouts/seen", headers=drv2)

    def test_unknown_driver_admin(self, admin):
        r = requests.get(f"{BASE}/api/drivers/drv-nope/shoutouts/new", headers=admin)
        print("unknown driver ->", r.status_code, r.text[:100])
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            assert r.json()["shoutouts"] == []
