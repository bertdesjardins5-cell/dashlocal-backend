# Monthly statement PDF + Stripe card checkout for invoices (iteration 28). Run with -n 0.
import io
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
MONTH = datetime.now(timezone.utc).strftime("%Y-%m")
NOAUTO = {"X-No-Auto-Auth": "1"}


def login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    if r.status_code != 200:
        pytest.skip(f"login failed {email}: {r.status_code}")
    return {"Authorization": "Bearer " + r.json()["token"], **NOAUTO}


@pytest.fixture(scope="module")
def bakery():
    return login("bakery@dashlocal.app", "Bakery2026")


@pytest.fixture(scope="module")
def books():
    return login("books@dashlocal.app", "Books2026")


def _fake_invoice(merchant_id, status, number):
    inv = {"id": f"TEST_{uuid.uuid4().hex[:10]}", "number": number, "merchant_id": merchant_id, "stop_id": f"TEST_stop_{uuid.uuid4().hex[:8]}",
           "date": f"{MONTH}-01", "issued_at": datetime.now(timezone.utc).isoformat(), "currency": "CAD",
           "business": {"name": "DashLocal", "address": "x", "hst_number": "x", "hst_pct": 13.0},
           "merchant": {"name": "x", "address": None},
           "delivery": {"tracking_code": "TESTX", "customer_name": "TEST_fake", "address": "x", "delivered_at": None, "delivered_local": "", "km": 1, "priority": False},
           "lines": [{"description": "Same-day delivery fee", "amount": 10.0}], "subtotal": 10.0, "hst_pct": 13.0, "hst": 1.3, "total": 11.3,
           "status": status, "paid_at": None, "paid_note": None}
    db.invoices.insert_one(dict(inv))
    return inv


@pytest.fixture(scope="module", autouse=True)
def seeded():
    # needs >=3 outstanding mer-1 invoices this month; reuse the iteration-27 UI seed if missing
    q = {"merchant_id": "mer-1", "status": {"$in": ["unpaid", "reported"]}, "date": {"$regex": f"^{MONTH}-"}}
    if db.invoices.count_documents(q) < 3:
        import subprocess, sys
        subprocess.run([sys.executable, str(Path(__file__).parent / "seed_ui_iter27.py")], check=True, timeout=110,
                       env={**os.environ, "EXPO_PUBLIC_BACKEND_URL": BASE})


@pytest.fixture(scope="module")
def fakes():
    paid = _fake_invoice("mer-1", "paid", "TEST-PAID-01")
    foreign = _fake_invoice("mer-2", "unpaid", "TEST-MER2-01")
    yield {"paid": paid, "foreign": foreign}
    db.invoices.delete_many({"id": {"$in": [paid["id"], foreign["id"]]}})


def outstanding(h):
    r = requests.get(f"{API}/merchants/mer-1/invoices", headers=h)
    assert r.status_code == 200
    return [i for i in r.json()["invoices"] if i["status"] in ("unpaid", "reported") and i["date"].startswith(MONTH)]


# ---- Statement PDF
class TestStatement:
    def test_statement_pdf_content(self, bakery, fakes):
        from pypdf import PdfReader
        r = requests.post(f"{API}/merchants/mer-1/statements/link", json={"month": MONTH}, headers=bakery)
        assert r.status_code == 200, r.text
        path = r.json()["path"]
        assert path.startswith(f"/api/merchants/mer-1/statements/{MONTH}.pdf?token=")
        g = requests.get(BASE + path)
        assert g.status_code == 200
        assert g.headers["content-type"].startswith("application/pdf")
        assert "attachment" in g.headers.get("content-disposition", "")
        text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(g.content)).pages)
        assert "MONTHLY STATEMENT" in text
        assert datetime.strptime(MONTH, "%Y-%m").strftime("%B %Y") in text
        invs = list(db.invoices.find({"merchant_id": "mer-1", "date": {"$regex": f"^{MONTH}-"}}, {"_id": 0}))
        assert len(invs) >= 3
        for i in invs:
            assert i["number"] in text, i["number"]
        total = sum(i["total"] for i in invs)
        assert f"CA${total:,.2f}" in text
        assert f"{len(invs)} invoices" in text
        assert "TEST-MER2-01" not in text  # mer-2 invoice must not leak

    def test_bad_month_400(self, bakery):
        for m in ["2026-13", "202610", "abc", "2026-1"]:
            r = requests.post(f"{API}/merchants/mer-1/statements/link", json={"month": m}, headers=bakery)
            assert r.status_code == 400, (m, r.status_code)

    def test_other_merchant_403(self, books):
        r = requests.post(f"{API}/merchants/mer-1/statements/link", json={"month": MONTH}, headers=books)
        assert r.status_code == 403

    def test_no_auth_401(self):
        r = requests.post(f"{API}/merchants/mer-1/statements/link", json={"month": MONTH}, headers=NOAUTO)
        assert r.status_code == 401

    def test_token_other_month_or_merchant_403(self, bakery, books):
        path = requests.post(f"{API}/merchants/mer-1/statements/link", json={"month": MONTH}, headers=bakery).json()["path"]
        tok = path.split("token=")[1]
        assert requests.get(f"{API}/merchants/mer-1/statements/2025-01.pdf?token={tok}").status_code == 403
        assert requests.get(f"{API}/merchants/mer-2/statements/{MONTH}.pdf?token={tok}").status_code == 403
        tok2 = requests.post(f"{API}/merchants/mer-2/statements/link", json={"month": MONTH}, headers=books).json()["path"].split("token=")[1]
        assert requests.get(f"{API}/merchants/mer-1/statements/{MONTH}.pdf?token={tok2}").status_code == 403

    def test_garbage_token_400(self):
        r = requests.get(f"{API}/merchants/mer-1/statements/{MONTH}.pdf?token=garbage")
        assert r.status_code == 400
        assert requests.get(f"{API}/merchants/mer-1/statements/{MONTH}.pdf").status_code == 422

    def test_session_token_rejected(self, bakery):
        # a normal sign-in JWT must not open the statement (purpose check)
        tok = bakery["Authorization"].split()[1]
        assert requests.get(f"{API}/merchants/mer-1/statements/{MONTH}.pdf?token={tok}").status_code == 400

    def test_empty_month_pdf(self, bakery):
        path = requests.post(f"{API}/merchants/mer-1/statements/link", json={"month": "2020-01"}, headers=bakery).json()["path"]
        g = requests.get(BASE + path)
        assert g.status_code == 200 and g.content[:4] == b"%PDF"


# ---- Stripe checkout
class TestCheckout:
    state = {}

    def test_checkout_creates_session(self, bakery, fakes):
        out = outstanding(bakery)
        assert len(out) >= 2
        pick = out[:2]
        ids = [i["id"] for i in pick] + [fakes["paid"]["id"], fakes["foreign"]["id"], "nope"]
        r = requests.post(f"{API}/merchants/mer-1/invoices/checkout", json={"ids": ids}, headers=bakery)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "checkout.stripe.com" in d["url"]
        assert d["session_id"].startswith("cs_")
        assert d["amount"] == round(sum(i["total"] for i in pick), 2)
        ps = db.payment_sessions.find_one({"session_id": d["session_id"]}, {"_id": 0})
        assert sorted(ps["invoice_ids"]) == sorted(i["id"] for i in pick)
        assert ps["amount_cents"] == int(round(d["amount"] * 100))
        TestCheckout.state.update(sid=d["session_id"], url=d["url"], ids=[i["id"] for i in pick], amount=d["amount"])

    def test_only_non_outstanding_400(self, bakery, fakes):
        r = requests.post(f"{API}/merchants/mer-1/invoices/checkout", json={"ids": [fakes["paid"]["id"], fakes["foreign"]["id"]]}, headers=bakery)
        assert r.status_code == 400
        assert requests.post(f"{API}/merchants/mer-1/invoices/checkout", json={"ids": []}, headers=bakery).status_code == 400

    def test_other_merchant_403(self, books, bakery):
        ids = [i["id"] for i in outstanding(bakery)[:1]]
        r = requests.post(f"{API}/merchants/mer-1/invoices/checkout", json={"ids": ids}, headers=books)
        assert r.status_code == 403

    def test_status_open(self, bakery):
        sid = TestCheckout.state.get("sid") or pytest.skip("no session")
        r = requests.get(f"{API}/merchants/mer-1/payments/{sid}", headers=bakery)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "open"
        assert d["amount"] == TestCheckout.state["amount"] and d["count"] == 2

    def test_status_other_merchant(self, books):
        sid = TestCheckout.state.get("sid") or pytest.skip("no session")
        assert requests.get(f"{API}/merchants/mer-1/payments/{sid}", headers=books).status_code == 403
        assert requests.get(f"{API}/merchants/mer-2/payments/{sid}", headers=books).status_code == 404

    def test_status_unknown_404(self, bakery):
        assert requests.get(f"{API}/merchants/mer-1/payments/cs_test_nope", headers=bakery).status_code == 404
