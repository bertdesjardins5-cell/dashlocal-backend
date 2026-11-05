"""Iteration 17: (1) payment note required when marking a weekly bill paid; (2) auto-dispatch balances a busy zone.
Run with -n 0. Resets DB; restores coverage drv-1 North, drv-2 South, drv-3 East; deletes bill_payments; restores pricing."""
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
API = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") + "/api"
TZ = ZoneInfo("America/Toronto")
TODAY = datetime.now(TZ).date()
THIS_MON = TODAY - timedelta(days=TODAY.weekday())
LAST_MON = (THIS_MON - timedelta(weeks=1)).isoformat()
DEFAULTS = {"base_fee": 8.99, "included_km": 5, "per_km": 1.25, "priority_fee": 16, "charge_failed": True, "tax_pct": 0}
DEFAULT_COV = {"drv-1": "North", "drv-2": "South", "drv-3": "East"}
DEPOT = {"lat": 43.4516, "lng": -80.4925}


def H(t):
    return {"Authorization": f"Bearer {t}"}


def bearing(p):
    return math.atan2(p["lat"] - DEPOT["lat"], (p["lng"] - DEPOT["lng"]) * math.cos(math.radians(DEPOT["lat"])))


@pytest.fixture(scope="module")
def db():
    c = MongoClient(os.environ["MONGO_URL"])
    yield c[os.environ["DB_NAME"]]
    c.close()


@pytest.fixture(scope="module")
def admin(db):
    r = requests.post(f"{API}/auth/login", json={"email": "admin@localdash.app", "password": "DashLocalAdmin2026"})
    assert r.status_code == 200, r.text
    h = H(r.json()["token"])
    assert requests.post(f"{API}/admin/reset", headers=h).status_code == 200
    yield h
    requests.post(f"{API}/admin/reset", headers=h)
    for d, z in DEFAULT_COV.items():
        requests.put(f"{API}/admin/drivers/{d}/compass-zone", json={"zone": z}, headers=h)
    requests.put(f"{API}/admin/pricing", json=DEFAULTS, headers=h)
    db.bill_payments.delete_many({})


# ---------------- Bill payment note
@pytest.fixture(scope="module")
def pastbill(admin, db):
    t = requests.post(f"{API}/auth/login", json={"email": "bakery@localdash.app", "password": "Bakery2026"}).json()["token"]
    r = requests.post(f"{API}/merchants/mer-1/import?consent=true", headers=H(t),
                      files={"file": ("p.csv", b"customer_name,phone,address\nTEST_Note,5195550193,200 King St W Kitchener\n", "text/csv")})
    assert r.status_code == 200, r.text
    jid, end = r.json()["id"], time.time() + 60
    while time.time() < end and requests.get(f"{API}/imports/{jid}", headers=H(t)).json()["status"] != "done":
        time.sleep(1.5)
    st = db.stops.find_one({"merchant_id": "mer-1", "customer_name": "TEST_Note"}, {"_id": 0})
    assert st, "no mer-1 stop"
    when = datetime.combine(datetime.fromisoformat(LAST_MON).date() + timedelta(days=2), datetime.min.time(), TZ).replace(hour=12).astimezone(timezone.utc)
    fee = st.get("fee") or {"base": 8, "km": 1, "distance_fee": 0, "priority_fee": 0, "total": 8, "currency": "CAD", "priced_at": when.isoformat()}
    db.stops.update_one({"id": st["id"]}, {"$set": {"status": "delivered", "delivered_at": when.isoformat(), "fee": fee}})
    return f"{API}/admin/merchants/mer-1/bills/{LAST_MON}/paid"


class TestPaymentNote:
    @pytest.mark.parametrize("body", [{"paid": True}, {"paid": True, "note": ""}, {"paid": True, "note": "   "}, {"paid": True, "note": None}])
    def test_note_required(self, admin, db, pastbill, body):
        r = requests.put(pastbill, json=body, headers=admin)
        assert r.status_code == 400 and "Add a payment note" in r.json()["detail"], r.text
        assert db.bill_payments.count_documents({"merchant_id": "mer-1"}) == 0

    def test_current_week_rejected_with_note(self, admin):
        r = requests.put(f"{API}/admin/merchants/mer-1/bills/{THIS_MON.isoformat()}/paid", json={"paid": True, "note": "TEST_x"}, headers=admin)
        assert r.status_code == 400 and "isn't over" in r.json()["detail"], r.text

    def test_with_note_stored_trimmed(self, admin, pastbill):
        r = requests.put(pastbill, json={"paid": True, "note": "  TEST_ e-Transfer #A1B2  "}, headers=admin)
        assert r.status_code == 200, r.text
        p = r.json()["payment"]
        assert p["note"] == "TEST_ e-Transfer #A1B2" and p["amount"] == r.json()["total"] > 0 and p["by"] == "admin@localdash.app"
        s = {x["id"]: x for x in requests.get(f"{API}/admin/billing", params={"week": LAST_MON}, headers=admin).json()["stores"]}["mer-1"]
        assert s["payment"]["note"] == "TEST_ e-Transfer #A1B2"

    def test_note_capped_200(self, admin, pastbill):
        r = requests.put(pastbill, json={"paid": True, "note": "TEST_" + "x" * 300}, headers=admin)
        assert r.status_code == 200 and len(r.json()["payment"]["note"]) == 200

    def test_unpaid_removes(self, admin, db, pastbill):
        r = requests.put(pastbill, json={"paid": False}, headers=admin)
        assert r.status_code == 200 and r.json()["payment"] is None
        assert db.bill_payments.count_documents({"merchant_id": "mer-1", "week_start": LAST_MON}) == 0


# ---------------- Balance busy zones
def setup_cov(admin, cov):
    assert requests.post(f"{API}/admin/reset", headers=admin).status_code == 200
    for d, z in cov.items():
        assert requests.put(f"{API}/admin/drivers/{d}/compass-zone", json={"zone": z}, headers=admin).status_code == 200


def eligible_ids(db):
    today = TODAY.isoformat()
    return {s["id"] for s in db.stops.find({"route_id": None, "status": "pending", "$or": [{"window_date": None}, {"window_date": today}]}, {"_id": 0, "id": 1})}


def check_counts(db, res, ids):
    assert res["dispatched"] == len(ids) == sum(r["stops"] for r in res["routes"])
    got = {s["id"]: s["route_id"] for s in db.stops.find({"id": {"$in": list(ids)}}, {"_id": 0, "id": 1, "route_id": 1})}
    assert len(got) == len(ids) and all(got.values())
    per_route = {}
    for rid in got.values():
        per_route[rid] = per_route.get(rid, 0) + 1
    for r in res["routes"]:
        assert per_route.get(r["route_id"], 0) == r["stops"], r


class TestBalance:
    def test_split_north(self, admin, db):
        setup_cov(admin, {"drv-1": "West", "drv-2": None, "drv-3": None})
        ids = eligible_ids(db)
        r = requests.post(f"{API}/routes/auto-dispatch", json={}, headers=admin)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["splits"]) >= 1
        sp = d["splits"][0]
        assert sp["zone"] == "West" and sp["from_id"] == "drv-1" and sp["moved"] >= 1
        routes = {x["driver_id"]: x for x in d["routes"]}
        helper = routes[sp["helper_id"]]
        assert "West" in helper["helping"] and "West" not in helper["fallback"] and "West" in helper["zones"]
        assert routes["drv-1"]["helping"] == [] and routes["drv-1"]["fallback"] == []
        check_counts(db, d, ids)
        # contiguous by bearing: every moved North stop is on the far side of every kept one
        north = [s for s in db.stops.find({"id": {"$in": list(ids)}, "compass_zone": "West"}, {"_id": 0})]
        moved = [bearing(s) for s in north if s["route_id"] == helper["route_id"]]
        kept = [bearing(s) for s in north if s["route_id"] == routes["drv-1"]["route_id"]]
        assert len(moved) == sp["moved"] and kept  # contiguity across the West seam: see test_split_west_contiguous

    def test_balance_false_no_split(self, admin, db):
        setup_cov(admin, {"drv-1": "West", "drv-2": None, "drv-3": None})
        ids = eligible_ids(db)
        d = requests.post(f"{API}/routes/auto-dispatch", json={"balance": False}, headers=admin).json()
        assert d["splits"] == [] and all(x["helping"] == [] for x in d["routes"])
        routes = {x["driver_id"]: x for x in d["routes"]}
        assert routes["drv-1"]["stops"] == 10 and routes["drv-1"]["zones"] == ["West"]
        check_counts(db, d, ids)

    def test_split_west_contiguous(self, admin, db):
        """West wedge straddles atan2's +-pi seam: moved slice and kept slice must still be contiguous arcs."""
        setup_cov(admin, {"drv-1": "West", "drv-2": None, "drv-3": None})
        ids = eligible_ids(db)
        d = requests.post(f"{API}/routes/auto-dispatch", json={}, headers=admin).json()
        check_counts(db, d, ids)
        sp = [x for x in d["splits"] if x["zone"] == "West"]
        assert sp, d
        routes = {x["driver_id"]: x for x in d["routes"]}
        west = [s for s in db.stops.find({"id": {"$in": list(ids)}, "compass_zone": "West"}, {"_id": 0})]
        # angle measured clockwise from due-west-south seam-free: map to [0, 2pi) with 0 at due East
        ang = lambda s: bearing(s) % (2 * math.pi)
        moved = [ang(s) for s in west if s["route_id"] == routes[sp[0]["helper_id"]]["route_id"]]
        kept = [ang(s) for s in west if s["route_id"] == routes["drv-1"]["route_id"]]
        print("moved", sorted(moved), "kept", sorted(kept))
        assert min(moved) > max(kept) or max(moved) < min(kept), "West split not contiguous"

    def test_default_coverage_counts(self, admin, db):
        setup_cov(admin, DEFAULT_COV)
        ids = eligible_ids(db)
        d = requests.post(f"{API}/routes/auto-dispatch", json={}, headers=admin).json()
        print("default splits", d["splits"])
        check_counts(db, d, ids)
        for x in d["routes"]:
            assert not (set(x["helping"]) & set(x["fallback"]))
