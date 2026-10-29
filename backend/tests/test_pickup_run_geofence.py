# Pickup-run labels on driver route/stop (pharmacy batch drops) + geofence auto-arrival regression
import os
import time
import uuid
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

CSV = ("customer_name,phone,address\n"
       "TEST_Run One,+15195550147,200 King St W Kitchener\n"
       "TEST_Run Two,+15195550150,100 Regina St S Waterloo\n")


def login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def driver_login():
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"})
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def admin():
    return login(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])


@pytest.fixture(scope="module")
def pharmacy_route(admin):
    """Import 2 evening-run pharmacy drops for mer-2, batch their zone to drv-3; teardown restores everything."""
    books = login("books@arrivewise.app", "Books2026")
    r = requests.post(f"{BASE_URL}/api/merchants/mer-2/import?run=evening&consent=true", headers=books,
                      files={"file": ("TEST_run.csv", CSV, "text/csv")})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["run"] == "evening" and job["manifest_id"].endswith(":evening")
    for _ in range(40):
        j = requests.get(f"{BASE_URL}/api/imports/{job['id']}", headers=books).json()
        if j["status"] != "running":
            break
        time.sleep(1)
    rows = list(db.import_rows.find({"job_id": job["id"]}, {"_id": 0}))
    stop_ids = [x["stop_id"] for x in rows if x.get("stop_id")]
    assert stop_ids, f"no stops created: {[x.get('error') for x in rows]}"
    stop = db.stops.find_one({"id": stop_ids[0]}, {"_id": 0})
    pre_assigned = {d["id"] for d in db.stops.find({"zone": stop["zone"], "route_id": None}, {"id": 1})} - set(stop_ids)
    b = requests.post(f"{BASE_URL}/api/routes/batch", headers=admin,
                      json={"zone": stop["zone"], "driver_id": "drv-3", "service_date": stop["window_date"]})
    assert b.status_code == 200, b.text
    route = b.json()
    yield {"route": route, "stop_ids": stop_ids, "job": job}
    db.stops.update_many({"id": {"$in": list(pre_assigned)}, "route_id": route["id"]}, {"$set": {"route_id": None}})
    db.stops.delete_many({"id": {"$in": stop_ids}})
    db.routes.delete_one({"id": route["id"]})
    db.import_rows.delete_many({"job_id": job["id"]})
    db.import_jobs.delete_one({"id": job["id"]})
    db.invoices.update_many({}, {"$pull": {"lines": {"stop_id": {"$in": stop_ids}}}})


# --- pickup_run labels
class TestPickupRunLabels:
    def test_batch_route_has_pickup_run(self, pharmacy_route):
        ours = [s for s in pharmacy_route["route"]["stops"] if s["id"] in pharmacy_route["stop_ids"]]
        assert ours
        for s in ours:
            pr = s["pickup_run"]
            assert pr["run"] == "evening"
            assert pr["label"] == "Evening Batch"
            assert pr["pickup_label"] == "5:00 PM – 5:30 PM"
            assert pr["pharmacy"] == "Bay Books"
            assert pr["pickup_address"] and "Fairway" in pr["pickup_address"]

    def test_non_batch_stops_null(self, pharmacy_route):
        others = [s for s in pharmacy_route["route"]["stops"] if s["id"] not in pharmacy_route["stop_ids"]]
        assert all(s["pickup_run"] is None for s in others)

    def test_get_route_and_stop(self, pharmacy_route, admin):
        rid = pharmacy_route["route"]["id"]
        r = requests.get(f"{BASE_URL}/api/routes/{rid}", headers=admin)
        assert r.status_code == 200
        body = r.json()
        rt = body.get("route", body)
        assert any(s.get("pickup_run") for s in rt["stops"])
        sid = pharmacy_route["stop_ids"][0]
        s = requests.get(f"{BASE_URL}/api/stops/{sid}", headers=admin)
        assert s.status_code == 200
        assert s.json()["pickup_run"]["label"] == "Evening Batch"
        assert s.json()["batch_run"] == "evening"

    def test_driver_route_pickup_run_key_present(self):
        h = driver_login()
        r = requests.get(f"{BASE_URL}/api/drivers/drv-1/route", headers=h)
        assert r.status_code == 200
        route = r.json()["route"]
        if route:
            assert all("pickup_run" in s for s in route["stops"])

    def test_stop_without_batch_null(self, admin):
        doc = db.stops.find_one({"batch_run": None, "route_id": {"$ne": None}}, {"_id": 0, "id": 1})
        s = requests.get(f"{BASE_URL}/api/stops/{doc['id']}", headers=admin)
        assert s.status_code == 200 and s.json()["pickup_run"] is None


# --- geofence auto arrival (drv-1 active route)
class TestGeofence:
    def test_far_location_no_arrival(self):
        h = driver_login()
        r = requests.post(f"{BASE_URL}/api/drivers/drv-1/location", headers=h, json={"lat": 43.30, "lng": -80.30})
        assert r.status_code == 200 and r.json()["geofence_arrived_stop_id"] is None

    def test_within_50m_arrives(self):
        h = driver_login()
        route = requests.get(f"{BASE_URL}/api/drivers/drv-1/route", headers=h).json()["route"]
        if not route or route["status"] != "active":
            pytest.skip("drv-1 has no active route")
        nxt = next(s for s in route["stops"] if s["id"] == route["next_stop_id"])
        if nxt["status"] != "pending":
            pytest.skip("next stop already arrived")
        r = requests.post(f"{BASE_URL}/api/drivers/drv-1/location", headers=h, json={"lat": nxt["lat"] + 0.0002, "lng": nxt["lng"]})
        assert r.status_code == 200 and r.json()["geofence_arrived_stop_id"] == nxt["id"]
        st = requests.get(f"{BASE_URL}/api/stops/{nxt['id']}", headers=h).json()
        assert st["status"] == "arrived" and st["arrival_method"] == "geofence"
        # regression: deliver with signature
        d = requests.post(f"{BASE_URL}/api/stops/{nxt['id']}/deliver", headers=h, json={"signature": "M10 10 L20 20 L30 15"})
        assert d.status_code == 200, d.text
        assert requests.get(f"{BASE_URL}/api/stops/{nxt['id']}", headers=h).json()["status"] == "delivered"

    def test_simulate_step(self):
        h = driver_login()
        r = requests.post(f"{BASE_URL}/api/drivers/drv-1/simulate-step", headers=h)
        assert r.status_code == 200 and "geofence_arrived_stop_id" in r.json()

    def test_location_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/drivers/drv-1/location", json={"lat": 43.4, "lng": -80.5},
                          headers={"X-No-Auto-Auth": "1"})
        assert r.status_code in (401, 403)
