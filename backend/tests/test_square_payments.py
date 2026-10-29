# Square payments (sandbox): merchant card on file, invoice card pay, auto billing, amount due, customer order pay, hosted page, pricing
import os
import uuid
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
BASE_URL = (os.environ.get("EXPO_BACKEND_URL") or os.environ.get("EXPO_PUBLIC_BACKEND_URL")).rstrip("/")
API = f"{BASE_URL}/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
NOAUTH = {"X-No-Auto-Auth": "1"}


def login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"login failed {email}: {r.status_code} {r.text[:120]}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def bakery():
    return login("bakery@dashlocal.app", "Bakery2026")


@pytest.fixture(scope="module")
def books():
    return login("books@dashlocal.app", "Books2026")


@pytest.fixture(scope="module")
def admin():
    return login(os.environ["ADMIN_EMAIL"], "DashLocalAdmin2026" if not os.environ.get("ADMIN_PASSWORD") else os.environ["ADMIN_PASSWORD"])


def tok(url):
    return parse_qs(urlparse(url).query)["t"][0]


def complete(t, nonce="cnon:card-nonce-ok", email=None):
    return requests.post(f"{API}/square/complete", json={"t": t, "source_id": nonce, "email": email}, headers=NOAUTH, timeout=60)


def mk_invoice(merchant_id, date, subtotal=10.0, kind=None):
    n = db.counters.find_one_and_update({"key": "invoice"}, {"$inc": {"n": 1}}, upsert=True, return_document=True)["n"]
    hst = round(subtotal * 0.13, 2)
    inv = {"id": f"TEST_{uuid.uuid4()}", "number": f"DL-{n:06d}", "merchant_id": merchant_id, "stop_id": f"TEST_stop_{uuid.uuid4()}",
           "date": date, "issued_at": datetime.utcnow().isoformat() + "+00:00", "currency": "CAD", "business": {"hst_pct": 13.0},
           "merchant": {"name": "Mission Bakery Co.", "address": None},
           "delivery": {"tracking_code": "TESTSQ", "customer_name": "TEST customer", "address": "1 King St W", "delivered_at": None,
                        "delivered_local": None, "km": 1.0, "priority": False},
           "lines": [{"description": "Same-day delivery fee", "amount": subtotal}], "subtotal": subtotal, "hst_pct": 13.0, "hst": hst,
           "total": round(subtotal + hst, 2), "status": "unpaid", "paid_at": None, "paid_note": None}
    if kind:
        inv["kind"] = kind
    db.invoices.insert_one(dict(inv))
    return inv


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    db.invoices.delete_many({"id": {"$regex": "^TEST_"}})
    db.stops.delete_many({"id": {"$regex": "^TEST_"}})


# ---------------- merchant card on file
class TestCardOnFile:
    def test_get_square_info(self, bakery):
        r = requests.get(f"{API}/merchants/mer-1/square", headers=bakery)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["configured"] is True and d["sandbox"] is True
        assert d["card"] and len(d["card"]["last4"]) == 4
        assert isinstance(d["charges"], list)

    def test_other_merchant_forbidden(self, books):
        for m, u in (("get", "/merchants/mer-1/square"), ("post", "/merchants/mer-1/square/card-link"),
                     ("delete", "/merchants/mer-1/square/card")):
            r = getattr(requests, m)(f"{API}{u}", headers=books)
            assert r.status_code == 403, f"{m} {u} -> {r.status_code}"
        r = requests.post(f"{API}/merchants/mer-1/invoices/square-pay", json={"ids": ["x"]}, headers=books)
        assert r.status_code == 403
        r = requests.put(f"{API}/merchants/mer-1/stops/x/amount-due", json={"amount": 5}, headers=books)
        assert r.status_code == 403

    def test_unauth(self):
        r = requests.get(f"{API}/merchants/mer-1/square", headers=NOAUTH)
        assert r.status_code == 401

    def test_card_link_remove_and_resave(self, bakery):
        r = requests.post(f"{API}/merchants/mer-1/square/card-link", headers=bakery)
        assert r.status_code == 200, r.text
        t = tok(r.json()["url"])
        assert r.json()["url"].startswith("https://")
        # remove
        r = requests.delete(f"{API}/merchants/mer-1/square/card", headers=bakery)
        assert r.status_code == 200 and r.json()["ok"] is True
        assert requests.get(f"{API}/merchants/mer-1/square", headers=bakery).json()["card"] is None
        assert requests.delete(f"{API}/merchants/mer-1/square/card", headers=bakery).status_code == 404
        # resave with sandbox nonce
        r = complete(t)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "saved" and r.json()["kind"] == "card"
        d = requests.get(f"{API}/merchants/mer-1/square", headers=bakery).json()
        assert d["card"] and d["card"]["last4"]
        print("new card", d["card"])

    def test_bad_source_id(self, bakery):
        t = tok(requests.post(f"{API}/merchants/mer-1/square/card-link", headers=bakery).json()["url"])
        assert complete(t, nonce="x").status_code == 400

    def test_invalid_token(self):
        assert complete("garbage").status_code == 401


# ---------------- invoice pay by card
class TestInvoicePay:
    def test_declined_then_ok(self, bakery):
        inv = mk_invoice("mer-1", datetime.now().date().isoformat(), 15.0)
        r = requests.post(f"{API}/merchants/mer-1/invoices/square-pay", json={"ids": [inv["id"]]}, headers=bakery)
        assert r.status_code == 200, r.text
        assert r.json()["amount"] == inv["total"]
        t = tok(r.json()["url"])
        r = complete(t, "cnon:card-nonce-declined")
        assert r.status_code == 402, r.text
        print("declined msg:", r.json()["detail"])
        assert "declined" in r.json()["detail"].lower() or "didn't" in r.json()["detail"].lower()
        assert db.invoices.find_one({"id": inv["id"]})["status"] == "unpaid"
        assert db.square_locks.count_documents({"key": f"inv:{inv['id']}"}) == 0
        r = complete(t)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["status"] == "paid" and j["total"] == inv["total"] and j["count"] == 1
        d = db.invoices.find_one({"id": inv["id"]})
        assert d["status"] == "paid" and d["paid_by"] == "square" and d["square_payment_id"] == j["payment_id"]
        ch = db.square_charges.find_one({"payment_id": j["payment_id"]})
        print("receipt_emails", ch.get("receipt_emails"))
        assert ch["receipt_emails"] and ch["receipt_emails"][0]["to"]
        # paying again -> 409 (already paid)
        assert complete(t).status_code == 409

    def test_no_outstanding(self, bakery):
        r = requests.post(f"{API}/merchants/mer-1/invoices/square-pay", json={"ids": ["nope"]}, headers=bakery)
        assert r.status_code == 400


# ---------------- auto billing
class TestAutoBilling:
    def test_requires_admin(self, bakery):
        assert requests.post(f"{API}/admin/square/run-billing", headers=bakery).status_code in (401, 403)

    def test_weekly_charge_idempotent(self, admin):
        today = datetime.now().date()
        monday = today - timedelta(days=today.weekday())
        last_week = (monday - timedelta(days=3)).isoformat()
        this_week = monday.isoformat()
        old = mk_invoice("mer-1", last_week, 12.0)
        new = mk_invoice("mer-1", this_week, 9.0)
        r1 = requests.post(f"{API}/admin/square/run-billing", headers=admin, timeout=120)
        assert r1.status_code == 200, r1.text
        print("run1", r1.json())
        st = db.invoices.find_one({"id": old["id"]})["status"]
        if st != "paid":
            key = f"week:mer-1:{monday.isoformat()}"
            prev = db.square_charges.find_one({"key": key}, {"_id": 0, "status": 1})
            pytest.fail(f"last-week invoice not charged; weekly key {key} already {prev} -> late invoices wait a whole extra week")
        assert db.invoices.find_one({"id": new["id"]})["status"] == "unpaid"
        r2 = requests.post(f"{API}/admin/square/run-billing", headers=admin, timeout=120)
        assert r2.status_code == 200 and r2.json()["results"] == []

    def test_platform_fee_autocharge_idempotent(self, admin):
        """Unpaid monthly platform invoice for a store with a card on file is auto-charged once (fresh key plat:{id})."""
        inv = mk_invoice("mer-1", datetime.now().date().isoformat(), 25.0, kind="platform")
        db.invoices.update_one({"id": inv["id"]}, {"$set": {"delivery.customer_name": "TEST Monthly platform fee"}})
        r1 = requests.post(f"{API}/admin/square/run-billing", headers=admin, timeout=120)
        assert r1.status_code == 200, r1.text
        print("run1", r1.json())
        d = db.invoices.find_one({"id": inv["id"]})
        assert d["status"] == "paid" and d["paid_by"] == "square", d["status"]
        ch = db.square_charges.find_one({"key": f"plat:{inv['id']}"})
        assert ch["status"] == "paid" and ch["total"] == 28.25 and ch["by"] == "autopay"
        print("auto receipt_emails", ch.get("receipt_emails"))
        r2 = requests.post(f"{API}/admin/square/run-billing", headers=admin, timeout=120)
        assert r2.json()["results"] == []
        assert db.square_charges.count_documents({"key": f"plat:{inv['id']}"}) == 1
        db.square_charges.delete_one({"key": f"plat:{inv['id']}"})


# ---------------- amount due
class TestAmountDue:
    def test_validation(self, bakery):
        sid = db.stops.find_one({"tracking_code": "MD1001"})["id"]
        u = f"{API}/merchants/mer-1/stops/{sid}/amount-due"
        for bad in (0, 0.5, 10000.01, -3):
            assert requests.put(u, json={"amount": bad}, headers=bakery).status_code == 400, bad
        r = requests.put(u, json={"amount": 42.5}, headers=bakery)
        assert r.status_code == 200 and r.json()["payment"]["total"] == 48.03 and r.json()["payment"]["hst"] == 5.53
        assert requests.put(f"{API}/merchants/mer-1/stops/nope/amount-due", json={"amount": 5}, headers=bakery).status_code == 404


# ---------------- customer order payment (on a TEST copy of MD1001 so the demo order stays unpaid for UI)
@pytest.fixture(scope="module")
def test_stop():
    s = db.stops.find_one({"tracking_code": "MD1001"}, {"_id": 0})
    s.update(id=f"TEST_{uuid.uuid4()}", tracking_code="TSQ" + uuid.uuid4().hex[:5].upper(), amount_due=20.0)
    s.pop("customer_payment", None)
    s.pop("route_id", None)
    db.stops.insert_one(dict(s))
    return s


class TestCustomerPay:
    def test_flow(self, bakery, test_stop):
        code, sid = test_stop["tracking_code"], test_stop["id"]
        # pay-link without token
        assert requests.post(f"{API}/track/{code}/pay-link", headers=NOAUTH).status_code in (401, 403)
        # token of another order -> 403
        other = requests.post(f"{API}/track/MD1001/verify", json={"last4": "0100"}, headers=NOAUTH).json()["token"]
        assert requests.post(f"{API}/track/{code}/pay-link", headers={"X-Order-Token": other, **NOAUTH}).status_code == 403
        v = requests.post(f"{API}/track/{code}/verify", json={"last4": "0100"}, headers=NOAUTH)
        assert v.status_code == 200, v.text
        ot = {"X-Order-Token": v.json()["token"], **NOAUTH}
        p = requests.get(f"{API}/track/{code}/private", headers=ot).json()["payment"]
        assert p["amount"] == 20.0 and p["hst"] == 2.6 and p["total"] == 22.6 and p["paid"] is False
        r = requests.post(f"{API}/track/{code}/pay-link", headers=ot)
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 22.6
        t = tok(r.json()["url"])
        assert complete(t, email="bad-email").status_code == 400
        assert complete(t, email=None).status_code == 400
        r = complete(t, "cnon:card-nonce-declined", email="test_customer@example.com")
        assert r.status_code == 402, r.text
        assert db.square_locks.count_documents({"key": f"order:{sid}"}) == 0
        r = complete(t, email="test_customer@example.com")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["status"] == "paid" and j["total"] == 22.6 and j["tracking_code"] == code
        p = requests.get(f"{API}/track/{code}/private", headers=ot).json()["payment"]
        assert p["paid"] is True and p["total"] == 22.6
        assert complete(t, email="test_customer@example.com").status_code == 409
        assert requests.post(f"{API}/track/{code}/pay-link", headers=ot).status_code == 409
        # amount locked once paid
        assert requests.put(f"{API}/merchants/mer-1/stops/{sid}/amount-due", json={"amount": 5}, headers=bakery).status_code == 409
        time.sleep(4)
        print("customer receipt", db.stops.find_one({"id": sid})["customer_payment"].get("receipt_email"))
        db.square_locks.delete_many({"key": f"order:{sid}"})

    def test_public_track_no_payment_leak(self):
        r = requests.get(f"{API}/track/MD1001", headers=NOAUTH)
        assert r.status_code == 200
        assert "payment" not in r.json() or r.json().get("payment") is None


# ---------------- hosted page
class TestHostedPage:
    def test_page_and_csp(self):
        v = requests.post(f"{API}/track/MD1001/verify", json={"last4": "0100"}, headers=NOAUTH).json()["token"]
        r = requests.post(f"{API}/track/MD1001/pay-link", headers={"X-Order-Token": v, **NOAUTH})
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 48.03
        h = requests.get(r.json()["url"], headers=NOAUTH)
        assert h.status_code == 200 and "text/html" in h.headers["content-type"]
        csp = h.headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp and "script-src 'nonce-" in csp
        assert 'id=em' in h.text and 'id=go' in h.text and "CA$48.03" in h.text and "sandbox.web.squarecdn.com" in h.text

    def test_invalid_token(self):
        assert requests.get(f"{API}/square/pay?t=bad", headers=NOAUTH).status_code == 401


# ---------------- pricing monthly fee
class TestPricing:
    def test_monthly_fee_roundtrip(self, admin, bakery):
        cur = requests.get(f"{API}/pricing", headers=admin).json()
        body = {k: cur[k] for k in ("base_fee", "included_km", "per_km", "priority_fee", "charge_failed", "tax_pct")}
        r = requests.put(f"{API}/admin/pricing", json={**body, "monthly_fee": 30.0}, headers=admin)
        assert r.status_code == 200, r.text
        assert requests.get(f"{API}/pricing", headers=admin).json()["monthly_fee"] == 30.0
        assert requests.get(f"{API}/merchants/mer-1/square", headers=bakery).json()["monthly_fee"] == 30.0
        assert requests.put(f"{API}/admin/pricing", json={**body, "monthly_fee": 6000}, headers=admin).status_code == 400
        r = requests.put(f"{API}/admin/pricing", json={**body, "monthly_fee": cur.get("monthly_fee", 25.0)}, headers=admin)
        assert r.status_code == 200
        assert requests.get(f"{API}/pricing", headers=admin).json()["monthly_fee"] == cur.get("monthly_fee", 25.0)
