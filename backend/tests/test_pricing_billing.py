"""Iteration 14: admin pricing (CAD), per-order fee snapshot, merchant priority toggle, priority-first routing,
weekly merchant bills + statement CSV, admin billing, and admin reset clearing merchant_alerts/report_emails.
Run with -n 0. Restores pricing defaults at the end."""
import csv
import io
import math
import os
import time

import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
DEFAULTS = {"base_fee": 8.99, "included_km": 5, "per_km": 1.25, "priority_fee": 16, "charge_failed": True, "tax_pct": 0}
CUSTOM = {"base_fee": 10, "included_km": 1, "per_km": 2, "priority_fee": 5, "charge_failed": True, "tax_pct": 13}
DEPOT = {"lat": 43.4516, "lng": -80.4925}
NOAUTH = {"X-No-Auto-Auth": "1"}


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _dlogin(phone, pin):
    r = requests.post(f"{API}/auth/driver-login", json={"phone": phone, "pin": pin})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hav(a, b):
    R = 6371000.0
    la1, la2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b["lng"] - a["lng"]) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


@pytest.fixture(scope="module")
def toks():
    t = {"admin": _login("admin@localdash.app", "DashLocalAdmin2026"), "m1": _login("bakery@localdash.app", "Bakery2026"),
         "m2": _login("books@localdash.app", "Books2026"), "d2": _dlogin("+15195550102", "730516")}
    assert requests.post(f"{API}/admin/reset", headers=H(t["admin"])).status_code == 200
    yield t
    requests.put(f"{API}/admin/pricing", json=DEFAULTS, headers=H(t["admin"]))
    requests.put(f"{API}/merchants/mer-1/alerts", json={"enabled": True}, headers=H(t["m1"]))


def _put_pricing(toks, body):
    return requests.put(f"{API}/admin/pricing", json=body, headers=H(toks["admin"]))


# ---------------- Pricing API
class TestPricingAPI:
    def test_get_access(self, toks):
        r = requests.get(f"{API}/pricing", headers=H(toks["admin"]))
        assert r.status_code == 200
        assert set(r.json()) >= {"currency", "base_fee", "included_km", "per_km", "priority_fee", "charge_failed", "tax_pct"}
        assert r.json()["currency"] == "CAD"
        assert requests.get(f"{API}/pricing", headers=H(toks["m1"])).status_code == 200
        assert requests.get(f"{API}/pricing", headers=H(toks["d2"])).status_code == 403
        assert requests.get(f"{API}/pricing", headers=NOAUTH).status_code == 401

    def test_put_access_and_validation(self, toks):
        assert requests.put(f"{API}/admin/pricing", json=DEFAULTS, headers=H(toks["m1"])).status_code == 403
        assert requests.put(f"{API}/admin/pricing", json=DEFAULTS, headers=NOAUTH).status_code == 401
        caps = {"base_fee": 1000, "included_km": 500, "per_km": 100, "priority_fee": 1000, "tax_pct": 30}
        for k, cap in caps.items():
            assert _put_pricing(toks, {**DEFAULTS, k: -1}).status_code == 400, k
            r = _put_pricing(toks, {**DEFAULTS, k: cap + 0.01})
            assert r.status_code == 400 and "between 0 and" in r.json()["detail"], (k, r.text)
            assert _put_pricing(toks, {**DEFAULTS, k: cap}).status_code == 200, k

    def test_put_saves(self, toks):
        r = _put_pricing(toks, CUSTOM)
        assert r.status_code == 200
        for k, v in CUSTOM.items():
            assert r.json()[k] == v
        g = requests.get(f"{API}/pricing", headers=H(toks["m1"])).json()
        assert g["base_fee"] == 10 and g["tax_pct"] == 13 and g["currency"] == "CAD"


# ---------------- Import with priority column -> fee snapshot, toggle, routing, bill
@pytest.fixture(scope="module")
def orders(toks):
    assert _put_pricing(toks, CUSTOM).status_code == 200
    csvb = (b"customer_name,phone,address,priority\n"
            b"TEST_PriA,5195550191,200 King St W Kitchener,yes\n"
            b"TEST_PriB,5195550192,10200 King St W Kitchener,\n")
    h = H(toks["m1"])
    r = requests.post(f"{API}/merchants/mer-1/import?consent=true", headers=h, files={"file": ("p.csv", csvb, "text/csv")})
    assert r.status_code == 200, r.text
    jid, end = r.json()["id"], time.time() + 60
    while time.time() < end:
        j = requests.get(f"{API}/imports/{jid}", headers=h).json()
        if j["status"] == "done":
            break
        time.sleep(1.5)
    assert j["created"] == 2, j
    os_ = requests.get(f"{API}/merchants/mer-1", headers=h).json()["orders"]
    return {o["customer_name"]: o for o in os_ if o["customer_name"] in ("TEST_PriA", "TEST_PriB")}


class TestFeeSnapshot:
    def test_fee_shape_and_math(self, orders):
        for name, o in orders.items():
            f = o["fee"]
            assert set(f) >= {"base", "km", "distance_fee", "priority_fee", "total", "currency"}
            assert f["currency"] == "CAD" and f["base"] == 10
            km = round(_hav(DEPOT, o) / 1000 * 1.3, 1)
            assert abs(f["km"] - km) <= 0.1, (name, f, km)
            assert f["distance_fee"] == round(max(0, f["km"] - 1) * 2, 2), f
            assert f["priority_fee"] == (5 if name == "TEST_PriA" else 0), f
            assert f["total"] == round(f["base"] + f["distance_fee"] + f["priority_fee"], 2)
        assert orders["TEST_PriA"]["priority"] is True and orders["TEST_PriB"]["priority"] is False

    def test_pricing_change_keeps_existing_fee(self, toks, orders):
        assert _put_pricing(toks, {**CUSTOM, "base_fee": 99}).status_code == 200
        cur = {o["customer_name"]: o for o in requests.get(f"{API}/merchants/mer-1", headers=H(toks["m1"])).json()["orders"]}
        assert cur["TEST_PriB"]["fee"]["total"] == orders["TEST_PriB"]["fee"]["total"]
        assert _put_pricing(toks, CUSTOM).status_code == 200


class TestPriorityToggle:
    def test_toggle_reprices(self, toks, orders):
        b = orders["TEST_PriB"]
        r = requests.put(f"{API}/merchants/mer-1/orders/{b['id']}/priority", json={"priority": True}, headers=H(toks["m1"]))
        assert r.status_code == 200, r.text
        assert r.json()["priority"] is True and r.json()["fee"]["priority_fee"] == 5
        assert r.json()["fee"]["total"] == round(b["fee"]["total"] + 5, 2)
        r = requests.put(f"{API}/merchants/mer-1/orders/{b['id']}/priority", json={"priority": False}, headers=H(toks["admin"]))
        assert r.status_code == 200 and r.json()["priority"] is False and r.json()["fee"]["priority_fee"] == 0

    def test_toggle_access(self, toks, orders):
        b = orders["TEST_PriB"]
        assert requests.put(f"{API}/merchants/mer-1/orders/{b['id']}/priority", json={"priority": True},
                            headers=H(toks["m2"])).status_code == 403
        assert requests.put(f"{API}/merchants/mer-2/orders/{b['id']}/priority", json={"priority": True},
                            headers=H(toks["m2"])).status_code == 404


@pytest.fixture(scope="module")
def route(toks, orders):
    today = requests.get(f"{API}/digest").json()["date"]
    zone = orders["TEST_PriA"]["zone"]
    r = requests.post(f"{API}/routes/batch", json={"zone": zone, "driver_id": "drv-2", "service_date": today})
    assert r.status_code == 200, r.text
    return r.json()


class TestPriorityRouting:
    def _check(self, stops):
        flags = [bool(s.get("priority")) for s in sorted(stops, key=lambda s: s["seq"]) if s["status"] not in ("delivered", "failed")]
        assert True in flags and False in flags, flags
        assert flags == sorted(flags, reverse=True), f"priority stops not first: {flags}"

    def test_batch_priority_first(self, route):
        self._check(route["stops"])

    def test_optimize_priority_first(self, toks, route):
        r = requests.post(f"{API}/routes/{route['id']}/optimize", headers=H(toks["admin"]))
        assert r.status_code == 200, r.text
        self._check(r.json()["stops"])

    def test_toggle_after_dispatch_400(self, toks, orders, route):
        r = requests.put(f"{API}/merchants/mer-1/orders/{orders['TEST_PriB']['id']}/priority", json={"priority": True}, headers=H(toks["m1"]))
        assert r.status_code == 400


# ---------------- Bills
class TestBills:
    @pytest.fixture(scope="class")
    def done(self, toks, orders, route):
        dh = H(toks["d2"])
        assert requests.put(f"{API}/merchants/mer-1/alerts", json={"enabled": False}, headers=H(toks["m1"])).status_code == 200
        assert requests.post(f"{API}/routes/{route['id']}/start", headers=dh).status_code == 200
        a, b = orders["TEST_PriA"], orders["TEST_PriB"]
        r = requests.post(f"{API}/stops/{a['id']}/deliver", json={"signature": "M10 10 L20 20 L30 15"}, headers=dh)
        assert r.status_code == 200, r.text
        r = requests.post(f"{API}/stops/{b['id']}/fail", json={"reason": "No one home"}, headers=dh)
        assert r.status_code == 200, r.text
        return a, b

    def test_bill_current_week(self, toks, done):
        a, b = done
        r = requests.get(f"{API}/merchants/mer-1/bill", headers=H(toks["m1"]))
        assert r.status_code == 200, r.text
        bill = r.json()
        assert bill["currency"] == "CAD" and bill["current_week"] is True and len(bill["weeks"]) == 8
        codes = {x["tracking_code"]: x for x in bill["lines"]}
        assert a["tracking_code"] in codes and b["tracking_code"] in codes
        assert codes[a["tracking_code"]]["total"] == a["fee"]["total"]
        assert bill["subtotal"] == round(sum(x["total"] for x in bill["lines"]), 2)
        assert bill["tax"] == round(bill["subtotal"] * 0.13, 2) and bill["total"] == round(bill["subtotal"] + bill["tax"], 2)
        assert bill["priority_total"] == round(sum(x["priority_fee"] for x in bill["lines"]), 2)
        assert {"count", "estimate"} <= set(bill["open_orders"])
        from datetime import date
        assert all(date.fromisoformat(w).weekday() == 0 for w in bill["weeks"])
        assert date.fromisoformat(bill["week_start"]).weekday() == 0 and date.fromisoformat(bill["week_end"]).weekday() == 6

    def test_charge_failed_false_hides_failed(self, toks, done):
        a, b = done
        assert _put_pricing(toks, {**CUSTOM, "charge_failed": False}).status_code == 200
        codes = [x["tracking_code"] for x in requests.get(f"{API}/merchants/mer-1/bill", headers=H(toks["m1"])).json()["lines"]]
        assert a["tracking_code"] in codes and b["tracking_code"] not in codes
        assert _put_pricing(toks, CUSTOM).status_code == 200

    def test_bill_access_and_bad_week(self, toks):
        assert requests.get(f"{API}/merchants/mer-1/bill", headers=H(toks["m2"])).status_code == 403
        assert requests.get(f"{API}/merchants/mer-1/bill", headers=NOAUTH).status_code == 401
        assert requests.get(f"{API}/merchants/mer-1/bill?week=bad", headers=H(toks["m1"])).status_code == 400
        old = requests.get(f"{API}/merchants/mer-1/bill?week=2025-01-08", headers=H(toks["m1"])).json()
        assert old["week_start"] == "2025-01-06" and old["current_week"] is False

    def test_statement_csv(self, toks, done):
        a, _ = done
        r = requests.post(f"{API}/merchants/mer-1/statement-link", json={}, headers=H(toks["m1"]))
        assert r.status_code == 200 and r.json()["path"].startswith("/api/merchants/mer-1/statement.csv?token=")
        c = requests.get(f"{BASE_URL}{r.json()['path']}")
        assert c.status_code == 200 and "text/csv" in c.headers["content-type"], c.text
        rows = list(csv.reader(io.StringIO(c.text.lstrip("\ufeff"))))
        flat = [x for row in rows for x in row]
        assert a["tracking_code"] in flat and "Subtotal" in flat and "Total" in flat and any(x.startswith("Tax") for x in flat)
        assert requests.post(f"{API}/merchants/mer-1/statement-link", json={}, headers=H(toks["m2"])).status_code == 403
        p2 = requests.post(f"{API}/merchants/mer-2/statement-link", json={}, headers=H(toks["m2"])).json()["path"]
        assert requests.get(f"{BASE_URL}{p2.replace('mer-2', 'mer-1', 1)}").status_code == 403
        assert requests.get(f"{API}/merchants/mer-1/statement.csv?token=garbage").status_code in (401, 403)

    def test_admin_billing(self, toks, done):
        r = requests.get(f"{API}/admin/billing", headers=H(toks["admin"]))
        assert r.status_code == 200
        j = r.json()
        st = {s["id"]: s for s in j["stores"]}
        m1 = requests.get(f"{API}/merchants/mer-1/bill", headers=H(toks["m1"])).json()
        assert st["mer-1"]["total"] == m1["total"] and j["total"] == round(sum(s["total"] for s in j["stores"]), 2)
        assert j["currency"] == "CAD" and len(j["weeks"]) == 8
        assert requests.get(f"{API}/admin/billing", headers=H(toks["m1"])).status_code == 403

    def test_reset_clears_alerts_and_report_emails(self, toks, done):
        assert len(requests.get(f"{API}/merchants/mer-1/alerts", headers=H(toks["m1"])).json()["recent"]) >= 1
        assert requests.post(f"{API}/admin/reset", headers=H(toks["admin"])).status_code == 200
        assert requests.get(f"{API}/merchants/mer-1/alerts", headers=H(toks["m1"])).json()["recent"] == []
        assert requests.get(f"{API}/merchants/mer-1/weekly-email", headers=H(toks["m1"])).json()["last"] is None
