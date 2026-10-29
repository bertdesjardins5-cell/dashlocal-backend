# Square regression (iteration 58): autopay retry after card replace uses a fresh idempotency key; invoice locks stop manual pay + autopay double charges
import glob
import time
from datetime import date, timedelta

import pytest
import requests

from tests.test_square_payments import API, NOAUTH, admin, bakery, complete, db, mk_invoice, tok  # noqa: F401

LAST_WEEK = (date.today() - timedelta(days=date.today().weekday() + 3)).isoformat()


def reuse_count():
    n = 0
    for f in glob.glob("/var/log/supervisor/backend.*.log"):
        with open(f, errors="ignore") as fh:
            n += fh.read().count("IDEMPOTENCY_KEY_REUSED")
    return n


@pytest.fixture(scope="module", autouse=True)
def cleanup_locks():
    yield
    ids = [i["id"] for i in db.invoices.find({"id": {"$regex": "^TEST_"}}, {"id": 1})]
    db.square_locks.delete_many({"key": {"$regex": "^inv:TEST_"}})
    db.square_charges.delete_many({"invoice_ids": {"$in": ids}, "status": {"$ne": "paid"}})
    db.invoices.delete_many({"id": {"$regex": "^TEST_"}})


def charges_for(inv_id):
    return list(db.square_charges.find({"invoice_ids": inv_id}, {"_id": 0}))


# ---------------- retry after card replacement
class TestRetryAfterCardReplace:
    def test_fresh_idempotency_key(self, bakery, admin):
        before = reuse_count()
        inv = mk_invoice("mer-1", LAST_WEEK, 7.0)
        real = db.merchants.find_one({"id": "mer-1"})["square"]
        db.merchants.update_one({"id": "mer-1"}, {"$set": {"square.card_id": "ccof:TEST_bogus_card"}})
        try:
            r = requests.post(f"{API}/admin/square/run-billing", headers=admin, timeout=90)
            assert r.status_code == 200
            res = [x for x in r.json()["results"] if x["key"].startswith("week:mer-1")]
            assert res and res[0]["status"] == "failed", r.json()
            doc = charges_for(inv["id"])[0]
            assert doc["status"] == "failed" and doc["attempts"] == 1
        finally:
            if db.merchants.find_one({"id": "mer-1"})["square"].get("card_id") == "ccof:TEST_bogus_card":
                db.merchants.update_one({"id": "mer-1"}, {"$set": {"square": real}})
        # merchant replaces the card -> overdue charge retried on the new card
        link = requests.post(f"{API}/merchants/mer-1/square/card-link", headers=bakery, timeout=30).json()["url"]
        r = complete(tok(link))
        assert r.status_code == 200 and r.json()["status"] == "saved", r.text
        for _ in range(30):
            doc = db.square_charges.find_one({"id": doc["id"]}, {"_id": 0})
            if doc["status"] == "paid":
                break
            time.sleep(1)
        assert doc["status"] == "paid", doc
        assert db.invoices.find_one({"id": inv["id"]})["status"] == "paid"
        assert reuse_count() == before, "IDEMPOTENCY_KEY_REUSED appeared in backend log"


# ---------------- invoice locks (manual pay vs autopay)
class TestInvoiceLocks:
    def test_autopay_skips_locked_invoice(self, admin):
        inv = mk_invoice("mer-1", LAST_WEEK, 8.0)
        db.square_locks.insert_one({"key": f"inv:{inv['id']}", "at": "test"})
        try:
            r = requests.post(f"{API}/admin/square/run-billing", headers=admin, timeout=90)
            assert r.status_code == 200
            assert not [x for x in r.json()["results"] if x["key"].startswith("week:mer-1") and x["status"] == "paid"]
            assert db.invoices.find_one({"id": inv["id"]})["status"] == "unpaid"
            assert all(c["status"] != "paid" for c in charges_for(inv["id"]))
        finally:
            db.square_locks.delete_many({"key": f"inv:{inv['id']}"})
        # manual pay now succeeds, then autopay must not charge it again
        b = requests.post(f"{API}/auth/login", json={"email": "bakery@arrivewise.app", "password": "Bakery2026"}, headers=NOAUTH).json()["token"]
        h = {"Authorization": f"Bearer {b}", **NOAUTH}
        link = requests.post(f"{API}/merchants/mer-1/invoices/square-pay", json={"ids": [inv["id"]]}, headers=h, timeout=30).json()["url"]
        r = complete(tok(link))
        assert r.status_code == 200 and r.json()["status"] == "paid", r.text
        pid = db.invoices.find_one({"id": inv["id"]})["square_payment_id"]
        requests.post(f"{API}/admin/square/run-billing", headers=admin, timeout=90)
        assert db.invoices.find_one({"id": inv["id"]})["square_payment_id"] == pid
        assert len([c for c in charges_for(inv["id"]) if c["status"] == "paid" and c.get("payment_id")]) == 1
        assert db.square_locks.count_documents({"key": f"inv:{inv['id']}"}) == 0

    def test_manual_pay_all_or_nothing_lock(self, bakery):
        a = mk_invoice("mer-1", date.today().isoformat(), 5.0)
        b = mk_invoice("mer-1", date.today().isoformat(), 6.0)
        link = requests.post(f"{API}/merchants/mer-1/invoices/square-pay", json={"ids": [a["id"], b["id"]]}, headers=bakery, timeout=30).json()["url"]
        db.square_locks.insert_one({"key": f"inv:{b['id']}", "at": "test"})  # autopay "busy" with b
        try:
            r = complete(tok(link))
            assert r.status_code == 409, r.text
            assert db.square_locks.count_documents({"key": f"inv:{a['id']}"}) == 0  # partial lock released
            assert db.invoices.find_one({"id": a["id"]})["status"] == "unpaid"
        finally:
            db.square_locks.delete_many({"key": f"inv:{b['id']}"})
