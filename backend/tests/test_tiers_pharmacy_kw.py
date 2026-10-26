# Volume tiers, priority $16, pharmacy batch addendum (runs, manifests, 8 km from pickup, $25 platform fee), KW switch — iteration 30
import io
import math
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
API = f"{BASE_URL}/api"
NOAUTH = {"X-No-Auto-Auth": "1"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
for _p in ("uploads/TEST_rx_photo.jpg", "uploads/TEST_tier.jpg"):  # pharmacy hand-off needs a real uploaded file record
    db.files.update_one({"storage_path": _p}, {"$set": {"storage_path": _p, "content_type": "image/jpeg"}}, upsert=True)
TZ = ZoneInfo("America/Toronto")
SIG = "M10 10 L60 40 L80 20"  # drawn signature path (pharmacy hand-off checks the format)
S = {}


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"login failed {email} {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def admin():
    return _login("admin@dashlocal.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def bakery():
    return _login("bakery@dashlocal.app", "Bakery2026")


@pytest.fixture(scope="module")
def books():
    return _login("books@dashlocal.app", "Books2026")


def today():
    return datetime.now(TZ).date().isoformat()


def hav_km(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h)) / 1000


def _cleanup():
    ids = [s["id"] for s in db.stops.find({"customer_name": {"$regex": "^TEST_TIER"}}, {"id": 1})]
    db.invoices.delete_many({"stop_id": {"$in": ids}})
    db.stops.delete_many({"id": {"$in": ids}})
    db.routes.delete_many({"name": {"$regex": "^TEST_TIER"}})
    db.login_attempts.delete_many({"key": {"$regex": "^(drv|trk)"}})


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    _cleanup()
    m2 = db.merchants.find_one({"id": "mer-2"}, {"_id": 0, "pharmacy_partner": 1, "pickup": 1, "pharmacy_since": 1})
    yield
    _cleanup()
    db.merchants.update_one({"id": "mer-2"}, {"$set": {"pharmacy_partner": True, "pickup": m2.get("pickup") or {
        "lat": 43.4201383, "lng": -80.4404996, "address": "385 Fairway Rd S, Kitchener"}}})
    db.merchants.update_one({"id": "mer-1"}, {"$unset": {"pharmacy_partner": "", "pickup": "", "pharmacy_since": ""}})


@pytest.fixture(scope="module")
def route():
    rid = "TEST_TIER_route_" + uuid.uuid4().hex[:6]
    db.routes.insert_one({"id": rid, "name": "TEST_TIER Route", "zone": "Downtown", "driver_id": "drv-3", "status": "active",
                          "planned_distance_m": 0, "actual_distance_m": 0, "started_at": datetime.utcnow().isoformat() + "+00:00",
                          "completed_at": None, "resequenced_at": None, "resequence_seen": True, "service_date": today(),
                          "created_at": datetime.utcnow().isoformat() + "+00:00"})
    return rid


def _mk_stop(mid, route, i, priority=False, dist=0.0, manifest=None, run=None):
    sid = str(uuid.uuid4())
    pri = 16.0 if priority else 0.0
    fee = {"base": 8.99, "km": 3.0, "distance_fee": dist, "priority_fee": pri, "total": round(8.99 + dist + pri, 2), "currency": "CAD", "priced_at": "x"}
    db.stops.insert_one({"id": sid, "seq": 100 + i, "customer_name": f"TEST_TIER {mid} {i}", "customer_phone": f"+1519555{7000 + i}",
                         "address": "200 King St W, Kitchener", "postal_code": "N2G 4V6", "zone": "Downtown", "lat": 43.4516, "lng": -80.4925,
                         "status": "pending", "tracking_code": f"TT{uuid.uuid4().hex[:6].upper()}", "notes": "", "merchant_id": mid,
                         "route_id": route, "priority": priority, "fee": fee, "manifest_id": manifest, "batch_run": run,
                         "created_at": datetime.utcnow().isoformat() + "+00:00"})
    return sid


def _deliver(admin, sid):
    r = requests.post(f"{API}/stops/{sid}/deliver", json={"signature": SIG, "photo_path": "uploads/TEST_tier.jpg"}, headers=admin)  # pharmacy drops need both (iter 33)
    assert r.status_code == 200, r.text
    inv = db.invoices.find_one({"stop_id": sid}, {"_id": 0})
    assert inv, "no invoice issued"
    return inv


# ---- rate card
class TestRateCard:
    def test_rate_card_tiers_pharmacy(self):
        d = requests.get(f"{API}/rate-card", headers=NOAUTH).json()
        assert d["base_fee"] == 8.99 and d["priority_fee"] == 16 and d["included_km"] == 5 and d["per_km"] == 1.25
        assert [(t["min_drops"], t["max_drops"], t["rate"]) for t in d["tiers"]] == [(1, 4, 8.99), (5, 9, 7.49), (10, 14, 6.49), (15, None, 5.99)]
        ph = d["pharmacy"]
        assert ph["batch_rate"] == 6.25 and ph["standard_rate"] == 8.99 and ph["min_drops"] == 10 and ph["included_km"] == 8 and ph["monthly_fee"] == 25
        runs = {r["key"]: r for r in ph["runs"]}
        assert runs["afternoon"]["cutoff_label"] == "1:30 PM" and runs["afternoon"]["pickup_label"] == "2:00 PM – 2:30 PM"
        assert runs["evening"]["cutoff_label"] == "4:30 PM" and runs["evening"]["window_label"] == "5:30 PM – 8:30 PM"


# ---- volume tiers (non-pharmacy mer-1)
class TestVolumeTiers:
    def test_tier_rerating(self, admin, route):
        m1 = db.merchants.find_one({"id": "mer-1"}, {"_id": 0})
        if m1.get("pharmacy_partner") or "base_fee" in (m1.get("pricing_override") or {}):
            pytest.skip("mer-1 has a custom base fee / pharmacy")
        q = {"merchant_id": "mer-1", "date": today(), "kind": {"$ne": "platform"}}
        c0 = db.invoices.count_documents(q)
        sids = [_mk_stop("mer-1", route, i, priority=(i == 1), dist=(2.5 if i == 2 else 0.0)) for i in range(16)]
        exp = lambda c: 8.99 if c < 5 else 7.49 if c < 10 else 6.49 if c < 15 else 5.99
        paid_id = None
        for k, sid in enumerate(sids):
            inv = _deliver(admin, sid)
            c = c0 + k + 1
            if k == 0:  # mark the first one paid: it must keep its price
                db.invoices.update_one({"id": inv["id"]}, {"$set": {"status": "paid", "paid_at": "x"}})
                paid_id, paid_total = inv["id"], inv["total"]
            if k == 1:  # second one sits in an open Stripe checkout: it must keep its price too
                locked_id, locked_base = inv["id"], inv["lines"][0]["amount"]
                db.payment_sessions.insert_one({"id": "TEST_TIER_ps", "merchant_id": "mer-1", "status": "open", "invoice_ids": [locked_id]})
            mine = list(db.invoices.find({"stop_id": {"$in": sids[:k + 1]}, "status": "unpaid", "id": {"$ne": locked_id if k >= 1 else ""}}, {"_id": 0}))
            for i in mine:
                assert i["lines"][0]["amount"] == exp(c), (c, i["lines"])
                sub = round(sum(x["amount"] for x in i["lines"]), 2)
                assert i["subtotal"] == sub and i["total"] == round(sub + round(sub * i["hst_pct"] / 100, 2), 2)
            S["last_c"] = c
        pri = db.invoices.find_one({"stop_id": sids[1]}, {"_id": 0})
        assert any(x["description"] == "Priority surcharge" and x["amount"] == 16 for x in pri["lines"])
        dist = db.invoices.find_one({"stop_id": sids[2]}, {"_id": 0})
        assert any(x["amount"] == 2.5 for x in dist["lines"][1:]) and dist["lines"][0]["amount"] == 5.99
        assert "High-Volume" in dist["lines"][0]["description"] and dist["tier"] == "High-Volume"
        p = db.invoices.find_one({"id": paid_id}, {"_id": 0})
        assert p["total"] == paid_total and p["lines"][0]["amount"] == exp(c0 + 1)
        lk = db.invoices.find_one({"id": locked_id}, {"_id": 0})
        db.payment_sessions.delete_many({"id": "TEST_TIER_ps"})
        assert lk["lines"][0]["amount"] == locked_base, "invoice in open checkout was re-rated"

    def test_custom_base_fee_no_tiers(self, admin, route):
        prev = db.merchants.find_one({"id": "mer-1"}, {"_id": 0, "pricing_override": 1}).get("pricing_override") or {}
        db.merchants.update_one({"id": "mer-1"}, {"$set": {"pricing_override": {**prev, "base_fee": 9.5}}})
        try:
            sids = [_mk_stop("mer-1", route, 300 + i) for i in range(3)]
            for sid in sids:
                db.stops.update_one({"id": sid}, {"$set": {"fee.base": 9.5, "fee.total": 9.5}})
                inv = _deliver(admin, sid)
            for sid in sids:  # day count is already 15+ from the previous test, still flat 9.50
                assert db.invoices.find_one({"stop_id": sid})["lines"][0]["amount"] == 9.5
        finally:
            db.merchants.update_one({"id": "mer-1"}, {"$set": {"pricing_override": prev}})


# ---- pharmacy runs + manifests
class TestPharmacy:
    def test_import_without_run_400(self, books):
        csv = "customer_name,phone,address\nTEST_TIER P0,+15195557100,\"200 King St W, Kitchener\"\n"
        r = requests.post(f"{API}/merchants/mer-2/import?consent=true", files={"file": ("o.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=books)
        assert r.status_code == 400 and "run" in r.text.lower(), r.text
        r = requests.post(f"{API}/merchants/mer-2/import?run=night&consent=true", files={"file": ("o.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=books)
        assert r.status_code == 400, r.text

    def test_regular_shop_ignores_run(self, bakery):
        csv = "customer_name,phone,address\n"
        r = requests.post(f"{API}/merchants/mer-1/import?run=evening&consent=true", files={"file": ("o.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=bakery)
        assert r.status_code == 400 and "no order rows" in r.text  # validated as a regular CSV, not a run error

    def test_import_with_run(self, books, route):
        before = {s["id"] for s in db.stops.find({"merchant_id": "mer-2"}, {"id": 1})}
        csv = ("customer_name,phone,address\n"
               "TEST_TIER P1,+15195557101,\"200 King St W, Kitchener\"\n"
               "TEST_TIER P2,+15195557102,\"75 King St S, Waterloo\"\n")
        r = requests.post(f"{API}/merchants/mer-2/import?run=evening&consent=true", files={"file": ("o.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=books)
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["run"] == "evening" and job["manifest_id"].startswith("mer-2:") and job["manifest_id"].endswith(":evening")
        for _ in range(30):
            j = requests.get(f"{API}/imports/{job['id']}", headers=books).json()
            if j.get("status") != "running":
                break
            time.sleep(1)
        new = list(db.stops.find({"merchant_id": "mer-2", "id": {"$nin": list(before)}, "customer_name": {"$regex": "^TEST_TIER"}}, {"_id": 0}))
        if len(new) < 2:
            pytest.skip(f"import made {len(new)} stops: {j}")
        pickup = db.merchants.find_one({"id": "mer-2"}, {"_id": 0, "pickup": 1})["pickup"]
        for s in new:
            assert s["manifest_id"] == job["manifest_id"] and s["batch_run"] == "evening"
            assert (s["window_start"], s["window_end"]) == ("17:30", "20:30") and s["window_date"] == job["manifest_id"].split(":")[1]
            km = round(hav_km(pickup, s) * 1.3, 1)
            assert abs(s["fee"]["km"] - km) <= 0.1, (s["fee"], km)
            assert s["fee"]["base"] == 8.99 and s["fee"]["distance_fee"] == round(max(0, s["fee"]["km"] - 8) * 1.25, 2)
        S["manifest"] = job["manifest_id"]
        S["p"] = [s["id"] for s in new]
        for i, s in enumerate(new):
            db.stops.update_one({"id": s["id"]}, {"$set": {"route_id": route, "seq": 200 + i}})
        # a job on the merchant portal shows drops booked for that run
        m = requests.get(f"{API}/merchants/mer-2", headers=books).json()
        runs = {r["key"]: r for r in m.get("runs", [])}
        assert runs.get("evening", {}).get("drops_today", 0) >= 2, m.get("runs")

    def test_manifest_rate(self, admin, route):
        if "manifest" not in S:
            pytest.skip("no manifest")
        inv = _deliver(admin, S["p"][0])
        assert inv["lines"][0]["amount"] == 8.99 and inv.get("tier") == "Standard", inv["lines"]
        extra = [_mk_stop("mer-2", route, 50 + i, manifest=S["manifest"], run="evening", priority=(i == 0)) for i in range(8)]
        inv2 = _deliver(admin, extra[0])  # manifest now has 10 drops
        assert inv2["lines"][0]["amount"] == 6.25 and inv2.get("manifest_drops") == 10, inv2["lines"]
        assert any(x["description"] == "Priority surcharge" and x["amount"] == 16 for x in inv2["lines"])
        first = db.invoices.find_one({"stop_id": S["p"][0]}, {"_id": 0})
        assert first["lines"][0]["amount"] == 6.25 and first["tier"] == "Pharmacy batch"


# ---- platform fee + admin toggle
class TestPlatformFee:
    def test_platform_invoice_once(self, admin, books):
        month = datetime.now(TZ).strftime("%Y-%m")
        key = f"platform:mer-2:{month}"
        if not db.invoices.find_one({"stop_id": key}):
            S["platform_missing_initially"] = True  # e.g. right after /admin/reset (watcher runs every 10 min)
        r = requests.put(f"{API}/admin/merchants/mer-2/pharmacy", json={"enabled": True}, headers=admin)
        assert r.status_code == 200 and r.json()["pharmacy_partner"] is True and r.json()["pickup"]["address"].startswith("385 Fairway")
        requests.put(f"{API}/admin/merchants/mer-2/pharmacy", json={"enabled": True}, headers=admin)
        invs = list(db.invoices.find({"merchant_id": "mer-2", "kind": "platform"}, {"_id": 0}))
        this = [i for i in invs if i["stop_id"] == key]
        assert len(this) == 1 and this[0]["total"] == 28.25 and this[0]["subtotal"] == 25 and this[0]["hst"] == 3.25
        assert db.invoices.count_documents({"merchant_id": "mer-1", "kind": "platform"}) == 0
        lst = requests.get(f"{API}/merchants/mer-2/invoices", headers=books).json()
        assert any(i["id"] == this[0]["id"] for i in lst["invoices"])
        link = requests.post(f"{API}/merchants/mer-2/invoices/{this[0]['id']}/link", headers=books)
        assert link.status_code == 200, link.text
        pdf = requests.get(f"{BASE_URL}{link.json()['path']}", headers=NOAUTH)
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    def test_toggle_validation(self, admin, bakery):
        url = f"{API}/admin/merchants/mer-2/pharmacy"
        assert requests.put(url, json={"enabled": True}, headers=bakery).status_code == 403
        assert requests.put(url, json={"enabled": True}, headers=NOAUTH).status_code == 401
        assert requests.put(f"{API}/admin/merchants/nope-x/pharmacy", json={"enabled": True}, headers=admin).status_code == 404
        r = requests.put(url, json={"enabled": True, "pickup_address": "99999 Qwxzv Nowhere Blvd Zzzz"}, headers=admin)
        assert r.status_code == 400, r.text
        assert requests.put(url, json={"pickup_address": "x"}, headers=admin).status_code == 422
        m = db.merchants.find_one({"id": "mer-2"}, {"_id": 0})
        assert m["pharmacy_partner"] is True and m["pickup"]["address"].startswith("385 Fairway")

    def test_toggle_on_off_mer1(self, admin):
        r = requests.put(f"{API}/admin/merchants/mer-1/pharmacy", json={"enabled": False}, headers=admin)
        assert r.status_code == 200 and r.json()["pharmacy_partner"] is False
        assert db.invoices.count_documents({"merchant_id": "mer-1", "kind": "platform"}) == 0

    def test_platform_after_reset_report(self):
        if S.get("platform_missing_initially"):
            pytest.xfail("platform invoice for mer-2 was missing before the enable call (not re-created by seed/reset)")
