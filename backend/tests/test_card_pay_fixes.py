# Iteration 29: retest of card-payment fixes (old checkout sessions expired, 424 on Stripe lookup failure, credit fields)
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]).rstrip("/")
API = BASE + "/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
NOAUTO = {"X-No-Auto-Auth": "1"}


def stripe_client():
    import stripe
    stripe.api_key = os.environ["STRIPE_API_KEY"]
    if "sk_test_emergent" in stripe.api_key:
        stripe.api_base = "https://integrations.emergentagent.com/stripe"
    return stripe


@pytest.fixture(scope="module")
def bakery():
    r = requests.post(f"{API}/auth/login", json={"email": "bakery@dashlocal.app", "password": "Bakery2026"})
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}")
    return {"Authorization": "Bearer " + r.json()["token"], **NOAUTO}


def _fake_invoice(number, status="unpaid"):
    inv = {"id": f"TEST_{uuid.uuid4().hex[:10]}", "number": number, "merchant_id": "mer-1", "stop_id": f"TEST_stop_{uuid.uuid4().hex[:8]}",
           "date": datetime.now(timezone.utc).strftime("%Y-%m-01"), "issued_at": datetime.now(timezone.utc).isoformat(), "currency": "CAD",
           "business": {"name": "DashLocal", "address": "x", "hst_number": "x", "hst_pct": 13.0}, "merchant": {"name": "x", "address": None},
           "delivery": {"tracking_code": "TESTX", "customer_name": "TEST_fake", "address": "x", "delivered_at": None, "delivered_local": "", "km": 1, "priority": False},
           "lines": [{"description": "Same-day delivery fee", "amount": 10.0}], "subtotal": 10.0, "hst_pct": 13.0, "hst": 1.3, "total": 11.3,
           "status": status, "paid_at": None, "paid_note": None}
    db.invoices.insert_one(dict(inv))
    return inv


@pytest.fixture(scope="module")
def invs():
    a, b = _fake_invoice("TEST-FIX-01"), _fake_invoice("TEST-FIX-02")
    yield a, b
    ids = [a["id"], b["id"]]
    stripe = stripe_client()
    for ps in db.payment_sessions.find({"invoice_ids": {"$in": ids}, "status": "open"}):
        try:
            stripe.checkout.Session.expire(ps["session_id"])
        except Exception:
            pass
    db.payment_sessions.delete_many({"invoice_ids": {"$in": ids}})
    db.invoices.delete_many({"id": {"$in": ids}})


def checkout(h, ids):
    r = requests.post(f"{API}/merchants/mer-1/invoices/checkout", json={"ids": ids}, headers=h, timeout=60)
    assert r.status_code == 200, r.text
    return r.json()


class TestOldSessionsClosed:
    # double-charge fix: the proxy can't expire sessions, so the same invoice set reuses the open session
    # and an overlapping different set is refused (409) until the old one is paid or expires (31 min)
    def test_second_checkout_reuses_first(self, bakery, invs):
        x, y = invs
        a = checkout(bakery, [x["id"], y["id"]])
        b = checkout(bakery, [x["id"], y["id"]])
        assert a["session_id"] == b["session_id"] and b.get("reused") is True
        assert db.payment_sessions.count_documents({"invoice_ids": {"$in": [x["id"], y["id"]]}, "status": "open"}) == 1

    def test_partial_overlap_refused(self, bakery, invs):
        x, y = invs
        r = requests.post(f"{API}/merchants/mer-1/invoices/checkout", json={"ids": [x["id"]]}, headers=bakery, timeout=60)
        assert r.status_code == 409, r.text
        assert db.payment_sessions.count_documents({"invoice_ids": {"$in": [x["id"], y["id"]]}, "status": "open"}) == 1

    def test_status_response_shape(self, bakery, invs):
        ps = db.payment_sessions.find_one({"invoice_ids": invs[0]["id"], "status": "open"})
        r = requests.get(f"{API}/merchants/mer-1/payments/{ps['session_id']}", headers=bakery, timeout=60)
        assert r.status_code == 200
        assert r.json() == {"status": "open", "amount": 22.6, "count": 2, "credit": 0}


class TestStripeLookupFailure:
    # a session the processor doesn't know must give 424, not 500
    def test_fake_session_424(self, bakery):
        sid = f"cs_test_fake_{uuid.uuid4().hex[:8]}"
        db.payment_sessions.insert_one({"session_id": sid, "merchant_id": "mer-1", "invoice_ids": ["TEST_none"], "amount_cents": 1130,
                                        "currency": "cad", "status": "open", "created_at": datetime.now(timezone.utc).isoformat(), "by": "test"})
        try:
            r = requests.get(f"{API}/merchants/mer-1/payments/{sid}", headers=bakery, timeout=60)
            assert r.status_code == 424, r.text
            assert "card processor" in r.json()["detail"]
        finally:
            db.payment_sessions.delete_one({"session_id": sid})

    def test_checkout_survives_broken_old_session(self, bakery):
        # an old session that Stripe can't find must not block a new checkout
        inv = _fake_invoice("TEST-FIX-03")
        sid = f"cs_test_fake_{uuid.uuid4().hex[:8]}"
        db.payment_sessions.insert_one({"session_id": sid, "merchant_id": "mer-1", "invoice_ids": [inv["id"]], "amount_cents": 1130,
                                        "currency": "cad", "status": "open", "created_at": datetime.now(timezone.utc).isoformat(), "by": "test"})
        try:
            r = requests.post(f"{API}/merchants/mer-1/invoices/checkout", json={"ids": [inv["id"]]}, headers=bakery, timeout=60)
            assert r.status_code == 200, r.text
            try:
                stripe_client().checkout.Session.expire(r.json()["session_id"])
            except Exception:
                pass
        finally:
            db.payment_sessions.delete_many({"invoice_ids": inv["id"]})
            db.invoices.delete_one({"id": inv["id"]})
