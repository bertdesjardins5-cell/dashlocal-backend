# Invoicing + HST (per-delivery invoices, PDF, shop reports paid / admin confirms, business details, rate card) — iteration 27
import io
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
NOAUTH = {"X-No-Auto-Auth": "1"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
S = {}
SIG = "M10 10 L20 20 L30 15"
API = f"{BASE_URL}/api"


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"login failed {email} {r.status_code} {r.text}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def admin():
    return _login("admin@localdash.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def bakery():
    return _login("bakery@localdash.app", "Bakery2026")


@pytest.fixture(scope="module")
def books():
    return _login("books@localdash.app", "Books2026")


def _cleanup():
    ids = [s["id"] for s in db.stops.find({"customer_name": {"$regex": "^TEST_INV"}}, {"id": 1})]
    db.invoices.delete_many({"stop_id": {"$in": ids}})
    db.stops.delete_many({"id": {"$in": ids}})
    db.routes.delete_many({"name": {"$regex": "^TEST_INV"}})
    db.login_attempts.delete_many({"key": {"$regex": "^(drv|trk)"}})


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    _cleanup()
    biz = db.settings.find_one({"key": "business"}, {"_id": 0})
    addr = (db.merchants.find_one({"id": "mer-1"}, {"_id": 0, "address": 1}) or {}).get("address")
    yield
    _cleanup()
    if biz:
        db.settings.replace_one({"key": "business"}, biz, upsert=True)
    else:
        db.settings.delete_one({"key": "business"})
    db.merchants.update_one({"id": "mer-1"}, {"$set": {"address": addr}})


@pytest.fixture(scope="module")
def route():
    r101 = db.routes.find_one({"status": "active", "driver_id": "drv-1"}, {"_id": 0})
    assert r101, "seeded active route missing"
    rid = "TEST_INV_route_" + uuid.uuid4().hex[:6]
    db.routes.insert_one({"id": rid, "name": "TEST_INV Route", "zone": "Downtown", "driver_id": "drv-2", "status": "active", "planned_distance_m": 0,
                          "actual_distance_m": 0, "started_at": r101["started_at"], "completed_at": None, "resequenced_at": None,
                          "resequence_seen": True, "service_date": r101["service_date"], "created_at": r101["created_at"]})
    return rid


def _invs(stop_id):
    return list(db.invoices.find({"stop_id": stop_id}, {"_id": 0}))


# ---- rate card
class TestRateCard:
    def test_public_rate_card(self):
        r = requests.get(f"{API}/rate-card", headers=NOAUTH)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("base_fee", "included_km", "per_km", "priority_fee"):
            assert isinstance(d[k], (int, float))
        assert d["currency"] == "CAD" and d["hst_pct"] == 13


# ---- business details defaults/validation
class TestBusiness:
    def test_defaults_and_auth(self, admin, bakery):
        db.settings.delete_one({"key": "business"})
        d = requests.get(f"{API}/admin/business", headers=admin).json()
        assert d["name"] == "LocalDash" and d["hst_pct"] == 13 and "set in Admin" in d["address"] and "set in Admin" in d["hst_number"]
        assert requests.get(f"{API}/admin/business", headers=bakery).status_code == 403
        assert requests.put(f"{API}/admin/business", json={**d}, headers=bakery).status_code == 403

    @pytest.mark.parametrize("patch", [{"name": "  "}, {"address": ""}, {"hst_number": " "}, {"hst_pct": -1}, {"hst_pct": 31}])
    def test_validation(self, admin, patch):
        base = {"name": "LocalDash", "address": "1 King St W, Toronto ON", "hst_number": "123456789RT0001", "hst_pct": 13}
        r = requests.put(f"{API}/admin/business", json={**base, **patch}, headers=admin)
        assert r.status_code == 400, (patch, r.text)


# ---- invoice creation on delivery
class TestInvoiceCreation:
    def test_import_and_route(self, bakery, route):
        before = {s["id"] for s in db.stops.find({"merchant_id": "mer-1"}, {"id": 1})}
        csv = ("customer_name,phone,address\n"
               "TEST_INV A,+15195550191,\"200 King St W, Kitchener\"\n"
               "TEST_INV B,+15195550192,\"75 King St S, Waterloo\"\n"
               "TEST_INV C,+15195550193,\"385 Fairway Rd S, Kitchener\"\n"
               "TEST_INV D,+15195550194,\"100 Regina St S, Waterloo\"\n")
        r = requests.post(f"{API}/merchants/mer-1/import?consent=true", files={"file": ("o.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=bakery)
        assert r.status_code == 200, r.text
        job = r.json()["id"]
        for _ in range(40):
            j = requests.get(f"{API}/imports/{job}", headers=bakery).json()
            if (j.get("job") or j).get("status") != "running":
                break
            time.sleep(1)
        new = sorted(db.stops.find({"merchant_id": "mer-1", "id": {"$nin": list(before)}}, {"_id": 0}), key=lambda s: s["customer_name"])
        if len(new) < 4:
            pytest.skip(f"CSV import created {len(new)} stops: {j}")
        # make C priority via the merchant endpoint (before dispatch)
        c = new[2]["id"]
        rp = requests.put(f"{API}/merchants/mer-1/orders/{c}/priority", json={"priority": True}, headers=bakery)
        assert rp.status_code == 200, rp.text
        for i, s in enumerate(new, 1):
            db.stops.update_one({"id": s["id"]}, {"$set": {"route_id": route, "seq": i}})
        S["a"], S["b"], S["c"], S["d"] = [s["id"] for s in new]
        assert all(s["status"] == "pending" for s in new)

    def test_deliver_creates_one_invoice(self, admin):
        if "a" not in S:
            pytest.skip("no stops")
        r = requests.post(f"{API}/stops/{S['a']}/deliver", json={"signature": SIG}, headers=admin)
        assert r.status_code == 200, r.text
        invs = _invs(S["a"])
        assert len(invs) == 1
        inv = invs[0]
        st = db.stops.find_one({"id": S["a"]}, {"_id": 0})
        fee = st["fee"]
        import re
        assert re.fullmatch(r"DL-\d{6}", inv["number"])
        exp = [fee["base"]] + ([fee["distance_fee"]] if fee.get("distance_fee") else []) + ([fee["priority_fee"]] if fee.get("priority_fee") else [])
        assert [x["amount"] for x in inv["lines"]] == exp
        assert inv["subtotal"] == round(sum(exp), 2)
        assert inv["hst"] == round(inv["subtotal"] * 0.13, 2)
        assert inv["total"] == round(inv["subtotal"] + inv["hst"], 2)
        assert inv["status"] == "unpaid" and inv["currency"] == "CAD"
        assert inv["merchant"]["name"] == "Mission Bakery Co."
        assert inv["business"]["name"] == "LocalDash" and inv["business"]["hst_pct"] == 13
        assert inv["delivery"]["tracking_code"] == st["tracking_code"]
        S["inv_a"] = inv

    def test_redeliver_no_duplicate(self, admin):
        if "a" not in S:
            pytest.skip("no stops")
        r = requests.post(f"{API}/stops/{S['a']}/deliver", json={"signature": SIG}, headers=admin)
        assert r.status_code == 400
        assert len(_invs(S["a"])) == 1

    def test_failed_no_invoice(self, admin):
        if "b" not in S:
            pytest.skip("no stops")
        r = requests.post(f"{API}/stops/{S['b']}/fail", json={"reason": "no_access"}, headers=admin)
        assert r.status_code == 200, r.text
        assert db.stops.find_one({"id": S["b"]})["status"] == "failed"
        assert _invs(S["b"]) == []

    def test_priority_invoice_lines(self, admin):
        if "c" not in S:
            pytest.skip("no stops")
        r = requests.post(f"{API}/stops/{S['c']}/deliver", json={"signature": SIG}, headers=admin)
        assert r.status_code == 200, r.text
        inv = _invs(S["c"])[0]
        assert any(x["description"] == "Priority surcharge" and x["amount"] > 0 for x in inv["lines"])
        assert inv["delivery"]["priority"] is True
        assert inv["total"] == round(inv["subtotal"] + round(inv["subtotal"] * 0.13, 2), 2)
        assert inv["number"] > S["inv_a"]["number"]
        S["inv_c"] = inv

    def test_restart_no_duplicate(self):
        if "inv_a" not in S:
            pytest.skip("no invoice")
        n = db.invoices.count_documents({})
        subprocess.run("sudo supervisorctl restart backend", shell=True, capture_output=True)
        for _ in range(40):
            time.sleep(1.5)
            try:
                if requests.get(f"{API}/rate-card", headers=NOAUTH, timeout=5).status_code == 200:
                    break
            except Exception:
                pass
        time.sleep(4)  # backfill runs in the background
        assert db.invoices.count_documents({}) == n
        assert len(_invs(S["a"])) == 1 and len(_invs(S["c"])) == 1


# ---- merchant listing / access / billing address
class TestMerchantInvoices:
    def test_list_summary_filter(self, bakery):
        if "inv_a" not in S:
            pytest.skip("no invoice")
        d = requests.get(f"{API}/merchants/mer-1/invoices", headers=bakery).json()
        ids = [i["id"] for i in d["invoices"]]
        assert S["inv_a"]["id"] in ids and S["inv_c"]["id"] in ids
        all_ = list(db.invoices.find({"merchant_id": "mer-1"}, {"_id": 0}))
        assert d["outstanding"] == round(sum(i["total"] for i in all_ if i["status"] != "paid"), 2)
        assert d["unpaid_count"] == sum(i["status"] != "paid" for i in all_)
        assert d["currency"] == "CAD" and "paid" in d
        assert all("_id" not in i for i in d["invoices"])
        p = requests.get(f"{API}/merchants/mer-1/invoices?status=paid", headers=bakery).json()
        assert all(i["status"] == "paid" for i in p["invoices"])
        assert S["inv_a"]["id"] not in [i["id"] for i in p["invoices"]]

    def test_other_merchant_forbidden(self, books):
        assert requests.get(f"{API}/merchants/mer-1/invoices", headers=books).status_code == 403
        assert requests.post(f"{API}/merchants/mer-1/invoices/report-paid", json={"ids": [S.get("inv_a", {}).get("id", "x")], "note": "hi"}, headers=books).status_code == 403
        assert requests.get(f"{API}/merchants/mer-1/invoices", headers=NOAUTH).status_code == 401

    def test_billing_address(self, bakery):
        if "inv_a" not in S:
            pytest.skip("no invoice")
        db.invoices.update_one({"id": S["inv_a"]["id"]}, {"$set": {"merchant.address": None}})
        assert requests.put(f"{API}/merchants/mer-1/billing-address", json={"address": "   "}, headers=bakery).status_code == 400
        r = requests.put(f"{API}/merchants/mer-1/billing-address", json={"address": "  123  Queen St W,  Toronto ON M5H 2M9 "}, headers=bakery)
        assert r.status_code == 200, r.text
        assert r.json()["billing_address"] == "123 Queen St W, Toronto ON M5H 2M9"
        d = requests.get(f"{API}/merchants/mer-1/invoices", headers=bakery).json()
        assert d["billing_address"] == "123 Queen St W, Toronto ON M5H 2M9"
        assert db.invoices.find_one({"id": S["inv_a"]["id"]})["merchant"]["address"] == "123 Queen St W, Toronto ON M5H 2M9"


# ---- PDF
class TestPdf:
    def test_pdf_download(self, bakery, books):
        if "inv_a" not in S:
            pytest.skip("no invoice")
        inv = S["inv_a"]
        r = requests.post(f"{API}/merchants/mer-1/invoices/{inv['id']}/link", headers=bakery)
        assert r.status_code == 200, r.text
        path = r.json()["path"]
        assert path.startswith(f"/api/invoices/{inv['id']}.pdf?token=")
        assert requests.post(f"{API}/merchants/mer-1/invoices/{inv['id']}/link", headers=books).status_code == 403
        assert requests.post(f"{API}/merchants/mer-1/invoices/nope/link", headers=bakery).status_code == 404
        p = requests.get(BASE_URL + path, headers=NOAUTH)
        assert p.status_code == 200 and p.headers["content-type"].startswith("application/pdf")
        assert p.content[:4] == b"%PDF"
        from pypdf import PdfReader
        text = " ".join(pg.extract_text() for pg in PdfReader(io.BytesIO(p.content)).pages)
        for needle in ("LocalDash", inv["number"], "Mission Bakery Co.", "Subtotal", "HST (13%, Ontario)", f"{inv['total']:,.2f}"):
            assert needle in text, (needle, text[:600])
        S["pdf_tok"] = path.split("token=")[1]

    def test_bad_tokens(self):
        if "inv_c" not in S:
            pytest.skip("no invoice")
        r = requests.get(f"{API}/invoices/{S['inv_a']['id']}.pdf?token=garbage", headers=NOAUTH)
        assert r.status_code in (400, 403)
        r = requests.get(f"{API}/invoices/{S['inv_c']['id']}.pdf?token={S['pdf_tok']}", headers=NOAUTH)
        assert r.status_code == 403


# ---- shop reports paid -> admin confirms
class TestPaymentFlow:
    def test_report_paid(self, bakery):
        if "inv_a" not in S:
            pytest.skip("no invoice")
        a = S["inv_a"]["id"]
        assert requests.post(f"{API}/merchants/mer-1/invoices/report-paid", json={"ids": [a], "note": "   "}, headers=bakery).status_code == 400
        before = requests.get(f"{API}/merchants/mer-1/invoices", headers=bakery).json()
        # include a mer-2 invoice id (if any) to make sure it's untouched
        other = db.invoices.find_one({"merchant_id": {"$ne": "mer-1"}, "status": "unpaid"}, {"_id": 0, "id": 1})
        ids = [a] + ([other["id"]] if other else [])
        r = requests.post(f"{API}/merchants/mer-1/invoices/report-paid", json={"ids": ids, "note": "e-Transfer ref TEST123"}, headers=bakery)
        assert r.status_code == 200 and r.json()["updated"] == 1, r.text
        if other:
            assert db.invoices.find_one({"id": other["id"]})["status"] == "unpaid"
        after = requests.get(f"{API}/merchants/mer-1/invoices", headers=bakery).json()
        assert after["reported_count"] == before["reported_count"] + 1
        assert after["outstanding"] == before["outstanding"]
        inv = next(i for i in after["invoices"] if i["id"] == a)
        assert inv["status"] == "reported" and inv["report"]["note"] == "e-Transfer ref TEST123"
        # reporting again doesn't change anything
        assert requests.post(f"{API}/merchants/mer-1/invoices/report-paid", json={"ids": [a], "note": "again"}, headers=bakery).json()["updated"] == 0

    def test_admin_list_and_confirm(self, admin, bakery):
        if "inv_a" not in S:
            pytest.skip("no invoice")
        a, c = S["inv_a"]["id"], S["inv_c"]["id"]
        d = requests.get(f"{API}/admin/invoices?status=reported", headers=admin).json()
        assert a in [i["id"] for i in d["invoices"]] and all(i["status"] == "reported" for i in d["invoices"])
        assert requests.get(f"{API}/admin/invoices", headers=bakery).status_code == 403
        assert requests.post(f"{API}/admin/invoices/mark-paid", json={"ids": [a]}, headers=bakery).status_code == 403
        # unpaid w/o note -> 400
        r = requests.post(f"{API}/admin/invoices/mark-paid", json={"ids": [c], "paid": True}, headers=admin)
        assert r.status_code == 400
        assert db.invoices.find_one({"id": c})["status"] == "unpaid"
        r = requests.post(f"{API}/admin/invoices/mark-paid", json={"ids": [a], "paid": True}, headers=admin)
        assert r.status_code == 200 and r.json()["updated"] == 1, r.text
        inv = db.invoices.find_one({"id": a})
        assert inv["status"] == "paid" and inv["paid_note"] == "e-Transfer ref TEST123" and inv["paid_at"]
        m = requests.get(f"{API}/merchants/mer-1/invoices?status=paid", headers=bakery).json()
        assert a in [i["id"] for i in m["invoices"]] and m["paid"] >= S["inv_a"]["total"]

    def test_undo_and_note_paid(self, admin):
        if "inv_a" not in S:
            pytest.skip("no invoice")
        a, c = S["inv_a"]["id"], S["inv_c"]["id"]
        r = requests.post(f"{API}/admin/invoices/mark-paid", json={"ids": [a], "paid": False}, headers=admin)
        assert r.status_code == 200
        inv = db.invoices.find_one({"id": a})
        assert inv["status"] == "unpaid" and inv["paid_note"] is None and inv.get("report") is None and inv["paid_at"] is None
        r = requests.post(f"{API}/admin/invoices/mark-paid", json={"ids": [c], "paid": True, "note": "Cheque 42"}, headers=admin)
        assert r.status_code == 200
        assert db.invoices.find_one({"id": c})["paid_note"] == "Cheque 42"
        assert requests.post(f"{API}/admin/invoices/mark-paid", json={"ids": []}, headers=admin).status_code == 400


# ---- business snapshot only on NEW invoices
class TestBusinessSnapshot:
    def test_update_affects_new_only(self, admin):
        if "d" not in S:
            pytest.skip("no stops")
        v = {"name": "LocalDash Inc.", "address": "1 King St W, Toronto ON", "hst_number": "123456789RT0001", "hst_pct": 13}
        r = requests.put(f"{API}/admin/business", json=v, headers=admin)
        assert r.status_code == 200 and r.json()["name"] == "LocalDash Inc."
        assert requests.get(f"{API}/admin/business", headers=admin).json()["hst_number"] == "123456789RT0001"
        r = requests.post(f"{API}/stops/{S['d']}/deliver", json={"signature": SIG}, headers=admin)
        assert r.status_code == 200, r.text
        new = _invs(S["d"])[0]
        assert new["business"]["name"] == "LocalDash Inc." and new["business"]["hst_number"] == "123456789RT0001"
        assert db.invoices.find_one({"id": S["inv_a"]["id"]})["business"]["name"] == "LocalDash"


# ---- regression: weekly report email
class TestWeeklyEmail:
    def test_send_now(self, bakery):
        db.report_emails.delete_many({"merchant_id": "mer-1", "trigger": "manual"})
        r = requests.post(f"{API}/merchants/mer-1/weekly-email/send-now", headers=bakery)
        assert r.status_code in (200, 429), r.text
        if r.status_code == 200:
            sent = r.json()["sent"]
            assert sent["status"] in ("sent", "failed"), sent
            print("weekly email:", {k: sent.get(k) for k in ("status", "subject", "error")})
