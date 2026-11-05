"""Iteration 15: per-store prices (custom rates), mark weekly bills paid/unpaid + outstanding, weekly email with statement.
Run with -n 0. Resets DB at start; clears custom rates, restores shared pricing defaults and removes bill payments at end."""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import jwt as pyjwt
import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
DEFAULTS = {"base_fee": 8.99, "included_km": 5, "per_km": 1.25, "priority_fee": 16, "charge_failed": True, "tax_pct": 0}
NOAUTH = {"X-No-Auto-Auth": "1"}
TZ = ZoneInfo("America/Toronto")
TODAY = datetime.now(TZ).date()
THIS_MON = TODAY - timedelta(days=TODAY.weekday())
LAST_MON = (THIS_MON - timedelta(weeks=1)).isoformat()


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def db():
    c = MongoClient(os.environ["MONGO_URL"])
    yield c[os.environ["DB_NAME"]]
    c.close()


@pytest.fixture(scope="module")
def toks(db):
    t = {"admin": _login("admin@localdash.app", "DashLocalAdmin2026"), "m1": _login("bakery@localdash.app", "Bakery2026"),
         "m2": _login("books@localdash.app", "Books2026")}
    assert requests.post(f"{API}/admin/reset", headers=H(t["admin"])).status_code == 200
    assert requests.put(f"{API}/admin/pricing", json=DEFAULTS, headers=H(t["admin"])).status_code == 200
    yield t
    for m in ("mer-1", "mer-2"):
        requests.put(f"{API}/admin/merchants/{m}/pricing", json={}, headers=H(t["admin"]))
    requests.put(f"{API}/admin/pricing", json=DEFAULTS, headers=H(t["admin"]))
    db.bill_payments.delete_many({})


def _rates(toks, mid, body):
    return requests.put(f"{API}/admin/merchants/{mid}/pricing", json=body, headers=H(toks["admin"]))


# ---------------- Store rates API
class TestStoreRates:
    def test_access(self, toks):
        r = requests.get(f"{API}/admin/merchants/mer-1/pricing", headers=H(toks["admin"]))
        assert r.status_code == 200 and set(r.json()) >= {"shared", "override", "effective"}
        assert requests.get(f"{API}/admin/merchants/mer-1/pricing", headers=H(toks["m1"])).status_code == 403
        assert requests.get(f"{API}/admin/merchants/mer-1/pricing", headers=NOAUTH).status_code == 401
        assert requests.put(f"{API}/admin/merchants/mer-1/pricing", json={"base_fee": 1}, headers=H(toks["m1"])).status_code == 403
        assert requests.put(f"{API}/admin/merchants/mer-1/pricing", json={"base_fee": 1}, headers=NOAUTH).status_code == 401
        assert requests.get(f"{API}/admin/merchants/mer-x/pricing", headers=H(toks["admin"])).status_code == 404

    def test_subset_override(self, toks):
        r = _rates(toks, "mer-1", {"base_fee": 6.5})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["override"] == {"base_fee": 6.5}
        assert d["effective"]["base_fee"] == 6.5
        for k in ("included_km", "per_km", "priority_fee", "charge_failed", "tax_pct", "currency"):
            assert d["effective"][k] == d["shared"][k], k
        assert requests.get(f"{API}/pricing", headers=H(toks["m1"])).json()["base_fee"] == 6.5
        assert requests.get(f"{API}/pricing", headers=H(toks["m2"])).json()["base_fee"] == 8.99
        assert requests.get(f"{API}/pricing", headers=H(toks["admin"])).json()["base_fee"] == 8.99

    def test_null_follows_shared(self, toks):
        d = _rates(toks, "mer-1", {"base_fee": None, "per_km": 2}).json()
        assert d["override"] == {"per_km": 2} and d["effective"]["base_fee"] == 8.99 and d["effective"]["per_km"] == 2

    def test_validation(self, toks):
        r = _rates(toks, "mer-1", {"base_fee": 1000.01})
        assert r.status_code == 400 and "Per-delivery fee" in r.json()["detail"], r.text
        r = _rates(toks, "mer-1", {"tax_pct": 31})
        assert r.status_code == 400 and "Sales tax %" in r.json()["detail"], r.text
        assert _rates(toks, "mer-1", {"per_km": -1}).status_code == 400
        # rejected update must not overwrite the saved override
        assert requests.get(f"{API}/admin/merchants/mer-1/pricing", headers=H(toks["admin"])).json()["override"] == {"per_km": 2}

    def test_empty_clears(self, toks):
        d = _rates(toks, "mer-1", {}).json()
        assert d["override"] == {} and d["effective"] == d["shared"]


# ---------------- New orders use store rates (CSV import 1 row + priority re-price)
@pytest.fixture(scope="module")
def order(toks):
    assert _rates(toks, "mer-1", {"base_fee": 6.5, "priority_fee": 2}).status_code == 200
    h = H(toks["m1"])
    r = requests.post(f"{API}/merchants/mer-1/import?consent=true", headers=h,
                      files={"file": ("p.csv", b"customer_name,phone,address\nTEST_Rates,5195550193,200 King St W Kitchener\n", "text/csv")})
    assert r.status_code == 200, r.text
    jid, end = r.json()["id"], time.time() + 60
    while time.time() < end:
        j = requests.get(f"{API}/imports/{jid}", headers=h).json()
        if j["status"] == "done":
            break
        time.sleep(1.5)
    assert j["created"] == 1, j
    o = [o for o in requests.get(f"{API}/merchants/mer-1", headers=h).json()["orders"] if o["customer_name"] == "TEST_Rates"]
    return o[0]


class TestStoreRatesOrders:
    def test_import_uses_store_rate(self, order):
        assert order["fee"]["base"] == 6.5 and order["fee"]["priority_fee"] == 0

    def test_priority_reprice_uses_store_rate(self, toks, order):
        r = requests.put(f"{API}/merchants/mer-1/orders/{order['id']}/priority", json={"priority": True}, headers=H(toks["m1"]))
        assert r.status_code == 200, r.text
        assert r.json()["fee"]["priority_fee"] == 2 and r.json()["fee"]["base"] == 6.5

    def test_existing_order_keeps_fee(self, toks, order):
        _rates(toks, "mer-1", {"base_fee": 9})
        o = [x for x in requests.get(f"{API}/merchants/mer-1", headers=H(toks["m1"])).json()["orders"] if x["id"] == order["id"]][0]
        assert o["fee"]["base"] == 6.5
        assert requests.get(f"{API}/pricing", headers=H(toks["m2"])).json()["base_fee"] == 8.99

    def test_admin_billing_custom_rates_flag(self, toks):
        s = {x["id"]: x for x in requests.get(f"{API}/admin/billing", headers=H(toks["admin"])).json()["stores"]}
        assert s["mer-1"]["custom_rates"] is True and s["mer-2"]["custom_rates"] is False


# ---------------- Bills paid + outstanding + weekly email
@pytest.fixture(scope="module")
def pastdrop(toks, db, order):
    st = db.stops.find_one({"id": order["id"]}, {"_id": 0})
    assert st, "no mer-1 stop"
    when = datetime.combine(date_from(LAST_MON) + timedelta(days=2), datetime.min.time(), TZ).replace(hour=12).astimezone(timezone.utc)
    fee = st.get("fee") or {"base": 8, "km": 1, "distance_fee": 0, "priority_fee": 0, "total": 8, "currency": "CAD", "priced_at": when.isoformat()}
    db.stops.update_one({"id": st["id"]}, {"$set": {"status": "delivered", "delivered_at": when.isoformat(), "fee": fee}})
    return {"id": st["id"], "total": fee["total"]}


def date_from(s):
    return datetime.fromisoformat(s).date()


class TestBillsPaid:
    def test_outstanding_before(self, toks, pastdrop):
        b = requests.get(f"{API}/merchants/mer-1/bill", params={"week": LAST_MON}, headers=H(toks["m1"])).json()
        assert b["can_mark_paid"] is True and b["payment"] is None and b["total"] > 0
        assert any(w["week_start"] == LAST_MON for w in b["outstanding"]["weeks"]) and b["outstanding"]["total"] >= b["total"]
        cur = requests.get(f"{API}/merchants/mer-1/bill", headers=H(toks["m1"])).json()
        assert cur["can_mark_paid"] is False and cur["current_week"] is True

    def test_weekly_email_send_now(self, toks, pastdrop):
        r = requests.post(f"{API}/merchants/mer-1/weekly-email/send-now", headers=H(toks["m1"]))
        assert r.status_code == 200, r.text
        log = r.json()["sent"]
        print("weekly email log:", {k: log.get(k) for k in ("status", "error", "bill_total", "total_due", "recipients", "week_start")})
        assert log["status"] == "sent", log
        assert log["week_start"] == LAST_MON and log["bill_total"] >= 0 and log["total_due"] >= 0  # invoice-based since per-delivery invoices
        assert all(x["to"] == "delivered@resend.dev" for x in log["recipients"])
        r2 = requests.post(f"{API}/merchants/mer-1/weekly-email/send-now", headers=H(toks["m1"]))
        assert r2.status_code == 429

    def test_statement_token_7d(self, toks, pastdrop):
        now = datetime.now(timezone.utc)
        tok = pyjwt.encode({"sub": "weekly:mer-1", "role": "merchant", "purpose": "statement", "iat": now,
                            "exp": now + timedelta(days=7), "mid": "mer-1", "wk": LAST_MON}, os.environ["JWT_SECRET"], algorithm="HS256")
        r = requests.get(f"{API}/merchants/mer-1/statement.csv", params={"token": tok}, headers=NOAUTH)
        assert r.status_code == 200 and LAST_MON in r.text and "Total" in r.text, r.text[:300]
        assert requests.get(f"{API}/merchants/mer-2/statement.csv", params={"token": tok}, headers=NOAUTH).status_code == 403
        rep = pyjwt.encode({"sub": "weekly:mer-1", "role": "merchant", "purpose": "report", "iat": now,
                            "exp": now + timedelta(days=7), "mid": "mer-1", "wk": LAST_MON}, os.environ["JWT_SECRET"], algorithm="HS256")
        assert requests.get(f"{API}/merchants/mer-1/statement.csv", params={"token": rep}, headers=NOAUTH).status_code == 401

    def test_email_html_has_statement(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import server  # noqa  (loads the app)
        from routes import emails  # noqa
        bill = {"count": 1, "subtotal": 8, "hst": 1.04, "total": 9.04, "outstanding": 16, "unpaid_count": 2}
        s = {"delivered": 1, "failed": 0, "on_time_rate": None, "avg_rating": None, "complaints": 0, "orders": 1, "open": 0}
        html = emails.weekly_email_html("Store", LAST_MON, LAST_MON, s, "https://x/r", bill, "https://x/sign-in/merchant")
        assert "Invoices this week" in html and "https://x/sign-in/merchant" in html and "Outstanding: CA$16.00 (2 invoices)" in html and "CA$9.04" in html
        assert emails.WEEKLY_LINK_DAYS == 7

    def test_mark_paid_access_and_current_week(self, toks, pastdrop):
        u = f"{API}/admin/merchants/mer-1/bills/{LAST_MON}/paid"
        assert requests.put(u, json={"paid": True}, headers=H(toks["m1"])).status_code == 403
        assert requests.put(u, json={"paid": True}, headers=NOAUTH).status_code == 401
        r = requests.put(f"{API}/admin/merchants/mer-1/bills/{THIS_MON.isoformat()}/paid", json={"paid": True}, headers=H(toks["admin"]))
        assert r.status_code == 400, r.text
        assert requests.put(f"{API}/admin/merchants/mer-1/bills/bad/paid", json={"paid": True}, headers=H(toks["admin"])).status_code == 400
        assert requests.put(f"{API}/admin/merchants/mer-x/bills/{LAST_MON}/paid", json={"paid": True}, headers=H(toks["admin"])).status_code == 404

    def test_mark_paid_then_unpaid(self, toks, pastdrop):
        u = f"{API}/admin/merchants/mer-1/bills/{LAST_MON}/paid"
        r = requests.put(u, json={"paid": True, "note": "TEST_ e-transfer"}, headers=H(toks["admin"]))
        assert r.status_code == 200, r.text
        p = r.json()["payment"]
        assert p and p["paid_at"] and p["amount"] == r.json()["total"] and p["by"] == "admin@localdash.app" and p["note"] == "TEST_ e-transfer"
        b = requests.get(f"{API}/merchants/mer-1/bill", params={"week": LAST_MON}, headers=H(toks["m1"])).json()
        assert b["payment"]["amount"] == p["amount"]
        assert all(w["week_start"] != LAST_MON for w in b["outstanding"]["weeks"])
        adm = requests.get(f"{API}/admin/billing", params={"week": LAST_MON}, headers=H(toks["admin"])).json()
        s = {x["id"]: x for x in adm["stores"]}["mer-1"]
        assert s["payment"] and s["can_mark_paid"] is True and set(s) >= {"outstanding", "unpaid_weeks", "custom_rates"}
        assert "outstanding" in adm
        r = requests.put(u, json={"paid": False}, headers=H(toks["admin"]))
        assert r.status_code == 200 and r.json()["payment"] is None
        b = requests.get(f"{API}/merchants/mer-1/bill", params={"week": LAST_MON}, headers=H(toks["m1"])).json()
        assert any(w["week_start"] == LAST_MON for w in b["outstanding"]["weeks"])
        adm = requests.get(f"{API}/admin/billing", headers=H(toks["admin"])).json()
        s = {x["id"]: x for x in adm["stores"]}["mer-1"]
        assert s["outstanding"] > 0 and s["unpaid_weeks"] >= 1 and adm["outstanding"] >= s["outstanding"]

    def test_reset_clears_payments(self, toks, db, pastdrop):
        requests.put(f"{API}/admin/merchants/mer-1/bills/{LAST_MON}/paid", json={"paid": True, "note": "TEST_ ref"}, headers=H(toks["admin"]))
        assert db.bill_payments.count_documents({}) >= 1
        assert requests.post(f"{API}/admin/reset", headers=H(toks["admin"])).status_code == 200
        assert db.bill_payments.count_documents({}) == 0
