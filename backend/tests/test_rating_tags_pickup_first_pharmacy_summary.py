# Iteration 36: rating quick tags, driver pickup-first (pickup_lat/lng on route pickups), pharmacy weekly summary + email
import os
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
WEEK = "2030-01-14"  # Monday, far future so real loops never touch it
M1, M2 = "mer-2:2030-01-15:afternoon", "mer-2:2030-01-16:evening"
OUT = "mer-2:2030-01-21:afternoon"  # next week
PHONES = ["+15195550181", "+15195550182", "+15195550183", "+15195550184"]  # fictional only (Twilio LIVE)


def login(email, pw):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": pw}, headers=NA)
    if r.status_code != 200:
        pytest.skip(f"login {email} failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NA}


@pytest.fixture(scope="module")
def admin():
    return login("admin@arrivewise.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def books():
    return login("books@arrivewise.app", "Books2026")


@pytest.fixture(scope="module")
def bakery():
    return login("bakery@arrivewise.app", "Bakery2026")


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
    d = {"id": sid, "route_id": None, "seq": 99, "customer_name": "TEST_Cust", "customer_phone": PHONES[0], "merchant_id": "mer-2",
         "address": "200 King St W Kitchener", "postal_code": "N2G 1A7", "zone": "Downtown", "lat": 43.45, "lng": -80.49,
         "status": "pending", "tracking_code": "TST" + uuid.uuid4().hex[:6].upper(), "notes": "", "created_at": "2026-10-26T10:00:00+00:00",
         "erased_at": None}
    d.update(kw)
    db.stops.insert_one(dict(d))
    return d


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    ids = [s["id"] for s in db.stops.find({"id": {"$regex": "^TEST_"}}, {"id": 1})]
    db.stops.delete_many({"id": {"$in": ids}})
    db.contact_logs.delete_many({"stop_id": {"$in": ids}})
    db.invoices.delete_many({"stop_id": {"$regex": "^TEST_"}})
    db.pickups.delete_many({"manifest_id": {"$regex": "^mer-2:2030-0"}})
    db.login_attempts.delete_many({"key": {"$regex": "^trk:TST"}})


def unlock(stop):
    r = requests.post(f"{BASE}/api/track/{stop['tracking_code']}/verify", json={"last4": stop["customer_phone"][-4:]}, headers=NA)
    assert r.status_code == 200, r.text
    return {"x-order-token": r.json()["token"], **NA}


# ---- (1) rating tags
class TestRatingTags:
    def test_tags_normalised_deduped_unknown_dropped(self):
        s = mk_stop(status="delivered", delivered_at="2026-10-26T12:00:00+00:00", customer_phone=PHONES[1])
        h = unlock(s)
        r = requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=h,
                          json={"stars": 5, "comment": " great ", "tags": ["on time", " Discreet ", "ON TIME", "BOGUS", "handled with care"]})
        assert r.status_code == 200, r.text
        doc = db.stops.find_one({"id": s["id"]})
        assert doc["rating"] == 5 and doc["rating_comment"] == "great"
        assert doc["rating_tags"] == ["ON TIME", "DISCREET", "HANDLED WITH CARE"]
        assert "follow_up_status" not in doc  # 5 stars -> no apology flow

    def test_max_six_tags(self):
        s = mk_stop(status="delivered", delivered_at="2026-10-26T12:00:00+00:00", customer_phone=PHONES[2])
        tags = ["ON TIME", "FRIENDLY DRIVER", "HANDLED WITH CARE", "EASY TO FOLLOW", "DISCREET", "LATE", "WRONG ADDRESS", "NOT DISCREET"]
        r = requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=unlock(s), json={"stars": 4, "tags": tags})
        assert r.status_code == 200, r.text
        assert db.stops.find_one({"id": s["id"]})["rating_tags"] == tags[:6]

    def test_no_tags_ok_and_rerate_400(self):
        s = mk_stop(status="delivered", delivered_at="2026-10-26T12:00:00+00:00", customer_phone=PHONES[3])
        h = unlock(s)
        r = requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=h, json={"stars": 4})
        assert r.status_code == 200 and db.stops.find_one({"id": s["id"]})["rating_tags"] == []
        r = requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=h, json={"stars": 5, "tags": ["ON TIME"]})
        assert r.status_code == 400

    def test_not_delivered_400_no_token_401_bad_type_422(self):
        s = mk_stop(status="pending", customer_phone=PHONES[0])
        h = unlock(s)
        assert requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=h, json={"stars": 5, "tags": []}).status_code == 400
        assert requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=NA, json={"stars": 5}).status_code == 401
        db.stops.update_one({"id": s["id"]}, {"$set": {"status": "delivered"}})
        assert requests.post(f"{BASE}/api/stops/{s['id']}/rating", headers=h, json={"stars": 5, "tags": "ON TIME"}).status_code == 422


# ---- (2) pickup_lat/lng on route pickups; checked-in pickups carry picked_up_at
class TestRoutePickups:
    def test_pickup_coords_then_check_in(self, drv, route):
        mid = "mer-2:2030-02-05:evening"
        s = mk_stop(route_id=route, seq=0, manifest_id=mid, batch_run="evening", customer_phone=PHONES[0])
        r = requests.get(f"{BASE}/api/drivers/drv-1/route", headers=drv)
        assert r.status_code == 200, r.text
        rd = r.json()["route"]
        p = next(x for x in rd["pickups"] if x["manifest_id"] == mid)
        mer = db.merchants.find_one({"id": "mer-2"})["pickup"]
        assert p["pickup_lat"] == mer["lat"] and p["pickup_lng"] == mer["lng"]
        assert p["pickup_address"] == mer["address"] and p["pharmacy"] and p["picked_up_at"] is None and p["drops"] == 1
        st = next(x for x in rd["stops"] if x["id"] == s["id"])
        assert st["pickup_run"]["pickup_lat"] == mer["lat"]
        r = requests.post(f"{BASE}/api/routes/{route}/pickups/{mid}/check-in", headers=drv, json={"parcel_count": 1})
        assert r.status_code == 200, r.text
        p = next(x for x in requests.get(f"{BASE}/api/drivers/drv-1/route", headers=drv).json()["route"]["pickups"] if x["manifest_id"] == mid)
        assert p["picked_up_at"] and p["on_time"] is True


# ---- (3) pharmacy weekly summary
@pytest.fixture(scope="module")
def seeded():
    rows = [  # (manifest, status, rating, tags)
        (M1, "delivered", 5, ["ON TIME", "DISCREET"]), (M1, "delivered", 4, ["ON TIME"]), (M1, "failed", None, None),
        (M2, "delivered", 3, ["LATE"]), (M2, "pending", None, None),
        (OUT, "delivered", 1, ["RUDE DRIVER"]),
    ]
    for mid, st, rt, tg in rows:
        mk_stop(manifest_id=mid, batch_run=mid.split(":")[2], status=st, rating=rt, rating_tags=tg)
    mk_stop(merchant_id="mer-1", manifest_id="mer-1:2030-01-15:afternoon", status="delivered", rating=1)  # other merchant
    db.pickups.insert_many([{"id": f"{M1}|TESTR", "manifest_id": M1, "route_id": "TESTR", "on_time": True},
                            {"id": f"{M2}|TESTR", "manifest_id": M2, "route_id": "TESTR", "on_time": False}])
    for i, (dt, tier) in enumerate([("2030-01-15", "Pharmacy batch"), ("2030-01-15", "Pharmacy batch"), ("2030-01-16", "Pharmacy batch"),
                                    ("2030-01-16", "Standard"), ("2030-01-21", "Pharmacy batch")]):
        db.invoices.insert_one({"id": f"TEST_inv{i}", "merchant_id": "mer-2", "stop_id": f"TEST_inv{i}", "number": f"TEST-{i}", "date": dt, "tier": tier, "status": "unpaid"})
    return True


class TestPharmacySummary:
    def test_numbers(self, books, seeded):
        r = requests.get(f"{BASE}/api/merchants/mer-2/pharmacy-summary?week={WEEK}", headers=books)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["week_start"] == "2030-01-14" and d["week_end"] == "2030-01-20"
        assert (d["drops"], d["delivered"], d["failed"]) == (5, 3, 1)
        assert (d["pickups"], d["pickups_checked_in"], d["pickups_on_time"], d["on_time_pct"]) == (2, 2, 1, 50)
        assert d["batch_rate_drops"] == 3 and d["savings"] == 8.22
        assert d["avg_rating"] == 4.0 and d["ratings"] == 3
        assert d["top_tags"][0] == ["ON TIME", 2] and ["LATE", 1] in d["top_tags"] and ["RUDE DRIVER", 1] not in d["top_tags"]

    def test_mid_week_date_and_next_week(self, books, seeded):
        d = requests.get(f"{BASE}/api/merchants/mer-2/pharmacy-summary?week=2030-01-19", headers=books).json()
        assert d["week_start"] == "2030-01-14" and d["drops"] == 5
        n = requests.get(f"{BASE}/api/merchants/mer-2/pharmacy-summary?week=2030-01-21", headers=books).json()
        assert n["week_start"] == "2030-01-21" and n["drops"] == 1 and n["batch_rate_drops"] == 1 and n["savings"] == 2.74
        assert n["on_time_pct"] is None and n["avg_rating"] == 1.0

    def test_default_week_and_bad_week(self, books):
        d = requests.get(f"{BASE}/api/merchants/mer-2/pharmacy-summary", headers=books)
        assert d.status_code == 200 and d.json()["week_start"] <= d.json()["week_end"]
        assert requests.get(f"{BASE}/api/merchants/mer-2/pharmacy-summary?week=nope", headers=books).status_code == 400

    def test_non_pharmacy_and_access(self, admin, bakery, books):
        assert requests.get(f"{BASE}/api/merchants/mer-1/pharmacy-summary", headers=bakery).status_code == 400
        assert requests.get(f"{BASE}/api/merchants/mer-1/pharmacy-summary", headers=admin).status_code == 400
        assert requests.get(f"{BASE}/api/merchants/mer-2/pharmacy-summary", headers=bakery).status_code == 403
        assert requests.get(f"{BASE}/api/merchants/mer-1/pharmacy-summary", headers=books).status_code == 403
        assert requests.get(f"{BASE}/api/merchants/mer-2/pharmacy-summary", headers=NA).status_code == 401
        assert requests.get(f"{BASE}/api/merchants/mer-2/pharmacy-summary", headers=admin).status_code == 200

    def test_email(self, books, bakery, seeded):
        r = requests.post(f"{BASE}/api/merchants/mer-2/pharmacy-summary/email?week={WEEK}", headers=books)
        assert r.status_code == 200, r.text
        assert r.json()["sent_to"] == ["delivered@resend.dev"] or "delivered@resend.dev" in r.json()["sent_to"]
        assert requests.post(f"{BASE}/api/merchants/mer-1/pharmacy-summary/email", headers=bakery).status_code == 400
        assert requests.post(f"{BASE}/api/merchants/mer-2/pharmacy-summary/email", headers=bakery).status_code == 403
        assert requests.post(f"{BASE}/api/merchants/mer-2/pharmacy-summary/email", headers=NA).status_code == 401
