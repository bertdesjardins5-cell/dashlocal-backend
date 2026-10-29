# Iteration 37: Privacy Officer (public read/admin edit), Driver Feedback view, Pharmacy batch-savings badge
import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")
BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
NO = {"X-No-Auto-Auth": "1"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
TZ = ZoneInfo("America/Toronto")
DEFAULT = {"officer_name": "Albert Desjardins", "officer_email": "bertdesjardins@hotmail.com"}


def login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, headers=NO)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **NO}


def driver_login(phone, pin):
    r = requests.post(f"{API}/auth/driver-login", json={"phone": phone, "pin": pin}, headers=NO)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NO}


@pytest.fixture(scope="module")
def admin():
    return login("admin@arrivewise.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def drv1():
    return driver_login("+15195550101", "482913")


@pytest.fixture(scope="module")
def drv2():
    return driver_login("+15195550102", "730516")


# ---------------- Privacy officer
class TestPrivacyOfficer:
    @pytest.fixture(autouse=True, scope="class")
    def restore(self):
        yield
        db.settings.update_one({"key": "privacy"}, {"$unset": {"value.officer_name": "", "value.officer_email": ""}})

    def test_public_default(self):
        db.settings.update_one({"key": "privacy"}, {"$unset": {"value.officer_name": "", "value.officer_email": ""}})
        r = requests.get(f"{API}/privacy/officer", headers=NO)
        assert r.status_code == 200 and r.json() == DEFAULT

    def test_put_requires_admin(self, drv1):
        body = {"officer_name": "TEST Jane", "officer_email": "jane@x.com"}
        assert requests.put(f"{API}/admin/privacy/officer", json=body, headers=NO).status_code == 401
        assert requests.put(f"{API}/admin/privacy/officer", json=body, headers=drv1).status_code == 403
        m = login("bakery@arrivewise.app", "Bakery2026")
        assert requests.put(f"{API}/admin/privacy/officer", json=body, headers=m).status_code == 403

    @pytest.mark.parametrize("body,code", [
        ({"officer_name": "J", "officer_email": "a@b.com"}, 422),
        ({"officer_name": "x" * 81, "officer_email": "a@b.com"}, 422),
        ({"officer_name": "Jane Doe", "officer_email": "not-an-email"}, 400),
        ({"officer_name": "Jane Doe"}, 422),
    ])
    def test_validation(self, admin, body, code):
        r = requests.put(f"{API}/admin/privacy/officer", json=body, headers=admin)
        assert r.status_code == code, r.text

    def test_blank_name_rejected(self, admin):
        # whitespace-only name passes min_length on raw input; should be rejected after trimming
        r = requests.put(f"{API}/admin/privacy/officer", json={"officer_name": "   ", "officer_email": "a@b.com"}, headers=admin)
        assert r.status_code in (400, 422), f"blank name accepted: {r.status_code} {r.text}"

    def test_save_and_public_read(self, admin):
        r = requests.put(f"{API}/admin/privacy/officer", json={"officer_name": "  TEST   Jane  Doe ", "officer_email": " Jane.Doe@Example.COM "}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json() == {"officer_name": "TEST Jane Doe", "officer_email": "jane.doe@example.com"}
        g = requests.get(f"{API}/privacy/officer", headers=NO).json()
        assert g == {"officer_name": "TEST Jane Doe", "officer_email": "jane.doe@example.com"}
        # retention setting untouched
        assert db.settings.find_one({"key": "privacy"})["value"].get("retention_days") is not None


# ---------------- Driver feedback
TEST_STOPS = []


class TestDriverFeedback:
    @pytest.fixture(autouse=True, scope="class")
    def seed(self):
        route = db.routes.find_one({"driver_id": "drv-1"}, {"_id": 0, "id": 1})
        now = datetime.now(timezone.utc)
        rows = [(5, ["ON TIME", "DISCREET"], now - timedelta(minutes=1)), (5, ["ON TIME", "FRIENDLY DRIVER"], now - timedelta(minutes=2)),
                (2, ["LATE"], now - timedelta(minutes=3)), (4, ["FRIENDLY DRIVER"], now - timedelta(days=40))]
        for stars, tags, at in rows:
            sid = f"TEST_fb_{uuid.uuid4().hex[:8]}"
            TEST_STOPS.append(sid)
            base = db.stops.find_one({"route_id": route["id"]}, {"_id": 0})
            base.update({"id": sid, "tracking_code": f"TST{sid[-6:]}", "status": "delivered", "customer_name": "TEST Secret Patient", "share_token": None,
                         "address": "1 TEST Secret St", "rating": stars, "rating_tags": tags, "rated_at": at.isoformat()})
            db.stops.insert_one(base)
        yield
        db.stops.delete_many({"id": {"$in": TEST_STOPS}})

    def expected(self):
        rids = [r["id"] for r in db.routes.find({"driver_id": "drv-1"}, {"id": 1})]
        rows = list(db.stops.find({"route_id": {"$in": rids}, "rating": {"$ne": None}}, {"_id": 0}))
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        r30 = [r["rating"] for r in rows if (r.get("rated_at") or "") >= since]
        return rows, r30

    def test_numbers(self, drv1):
        r = requests.get(f"{API}/drivers/drv-1/feedback", headers=drv1)
        assert r.status_code == 200, r.text
        d = r.json()
        rows, r30 = self.expected()
        assert d["count"] == len(rows)
        assert d["avg"] == round(sum(x["rating"] for x in rows) / len(rows), 2)
        assert d["count_30d"] == len(r30) and d["avg_30d"] == round(sum(r30) / len(r30), 2)
        assert sum(d["stars"].values()) == len(rows) and list(d["stars"].keys()) == ["5", "4", "3", "2", "1"]
        comp = dict(d["compliments"])
        assert comp.get("ON TIME", 0) >= 2 and comp.get("FRIENDLY DRIVER", 0) >= 2 and comp.get("DISCREET", 0) >= 1
        assert all(t in {"ON TIME", "FRIENDLY DRIVER", "HANDLED WITH CARE", "EASY TO FOLLOW", "DISCREET"} for t in comp)
        assert dict(d["issues"]).get("LATE", 0) >= 1 and len(d["issues"]) <= 3 and len(d["compliments"]) <= 5
        assert len(d["recent"]) <= 8
        assert d["recent"][0]["stars"] == 5 and d["recent"][0]["tags"] == ["ON TIME", "DISCREET"]

    def test_no_patient_details(self, drv1):
        txt = requests.get(f"{API}/drivers/drv-1/feedback", headers=drv1).text
        assert "Secret" not in txt and "customer" not in txt and "address" not in txt

    def test_access_control(self, drv2, admin):
        assert requests.get(f"{API}/drivers/drv-1/feedback", headers=drv2).status_code == 403
        assert requests.get(f"{API}/drivers/drv-1/feedback", headers=NO).status_code == 401
        assert requests.get(f"{API}/drivers/drv-1/feedback", headers=admin).status_code == 200
        m = login("bakery@arrivewise.app", "Bakery2026")
        assert requests.get(f"{API}/drivers/drv-1/feedback", headers=m).status_code == 403

    def test_unknown_driver_404(self, admin):
        r = requests.get(f"{API}/drivers/drv-nope/feedback", headers=admin)
        assert r.status_code == 404, f"unknown driver returns {r.status_code}"


# ---------------- Pharmacy batch savings
TEST_INV = []


class TestBatchSavings:
    @pytest.fixture(autouse=True, scope="class")
    def seed(self):
        today = datetime.now(TZ)
        last = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d")
        for i, (tier, date) in enumerate([("Pharmacy batch", today.strftime("%Y-%m-%d"))] * 3 + [("Pharmacy batch", last), ("Standard", today.strftime("%Y-%m-%d"))]):
            iid = f"TEST_inv_{uuid.uuid4().hex[:8]}"
            TEST_INV.append(iid)
            db.invoices.insert_one({"id": iid, "number": f"TEST-{i}", "kind": "delivery", "merchant_id": "mer-2", "stop_id": iid, "date": date,
                                    "tier": tier, "total": 7.06, "subtotal": 6.25, "status": "unpaid", "lines": [], "hst": 0.81})
        yield
        db.invoices.delete_many({"id": {"$in": TEST_INV}})

    def test_mer2_savings(self):
        h = login("books@arrivewise.app", "Books2026")
        r = requests.get(f"{API}/merchants/mer-2/invoices", headers=h)
        assert r.status_code == 200, r.text
        bs = r.json()["batch_savings"]
        month = datetime.now(TZ).strftime("%Y-%m")
        n = db.invoices.count_documents({"merchant_id": "mer-2", "tier": "Pharmacy batch", "date": {"$regex": f"^{month}"}})
        assert n >= 3 and bs["drops"] == n and bs["month"] == month
        assert bs["amount"] == round(n * 2.74, 2)
        assert bs["label"] == datetime.now(TZ).strftime("%B %Y")

    def test_mer1_no_savings(self):
        h = login("bakery@arrivewise.app", "Bakery2026")
        r = requests.get(f"{API}/merchants/mer-1/invoices", headers=h)
        assert r.status_code == 200 and "batch_savings" not in r.json()

    def test_cross_merchant_forbidden(self):
        h = login("bakery@arrivewise.app", "Bakery2026")
        assert requests.get(f"{API}/merchants/mer-2/invoices", headers=h).status_code == 403
