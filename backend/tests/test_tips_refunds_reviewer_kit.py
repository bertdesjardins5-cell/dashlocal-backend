# Iter 61: driver tips (tip-link, hosted pay page tip UI), admin customer payments/refunds validation, Play Store reviewer kit
import os
import re
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
API = f"{BASE_URL}/api"
NOAUTH = {"X-No-Auto-Auth": "1"}


@pytest.fixture(scope="module")
def mdb():
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def admin_h():
    r = requests.post(f"{API}/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def merchant_h():
    r = requests.post(f"{API}/auth/login", json={"email": "books@arrivewise.app", "password": "Books2026"}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip("merchant login failed")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


def order_token(code, last4):
    r = requests.post(f"{API}/track/{code}/verify", json={"last4": last4}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---- tip-link
class TestTipLink:
    def test_tip_link_requires_token(self):
        r = requests.post(f"{API}/track/MD1002/tip-link", headers=NOAUTH)
        assert r.status_code in (401, 403), r.text

    def test_tip_link_wrong_order_token(self):
        tok = order_token("MD1001", "0100")  # token for a different order
        r = requests.post(f"{API}/track/MD1002/tip-link", headers={"X-Order-Token": tok, **NOAUTH})
        assert r.status_code in (401, 403), r.text

    def test_tip_link_not_delivered_409(self):
        tok = order_token("MD1001", "0100")
        r = requests.post(f"{API}/track/MD1001/tip-link", headers={"X-Order-Token": tok, **NOAUTH})
        assert r.status_code == 409, r.text

    def test_tip_link_unknown_code_404(self):
        r = requests.post(f"{API}/track/ZZ9999/tip-link", headers={"X-Order-Token": "x", **NOAUTH})
        assert r.status_code == 404

    def test_tip_link_delivered_ok_and_page_renders(self):
        tok = order_token("MD1002", "0101")
        r = requests.post(f"{API}/track/MD1002/tip-link", headers={"X-Order-Token": tok, **NOAUTH})
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert "/api/square/pay?t=" in url
        page = requests.get(url)
        assert page.status_code == 200
        h = page.text
        assert 'id=tips' in h and 'data-t=2' in h and 'data-t=3' in h and 'data-t=5' in h and 'data-t=c' in h
        assert 'data-t=0' not in h  # tip page has no "No tip"
        assert 'id=totv' in h and 'Tip your driver' in h
        assert "Content-Security-Policy" in page.headers

    def test_private_has_tips_total(self):
        tok = order_token("MD1002", "0101")
        r = requests.get(f"{API}/track/MD1002/private", headers={"X-Order-Token": tok, **NOAUTH})
        assert r.status_code == 200
        assert "tips_total" in r.json(), list(r.json().keys())


# ---- order pay page with tip
class TestOrderPayPage:
    def test_order_pay_page_has_tips(self):
        tok = order_token("MD1001", "0100")
        r = requests.post(f"{API}/track/MD1001/pay-link", headers={"X-Order-Token": tok, **NOAUTH})
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 22.6
        h = requests.get(r.json()["url"]).text
        assert 'data-t=0 class=on' in h and 'data-t=5' in h and 'id=totv' in h
        assert 'CA$22.60' in h
        assert 'id=tiprow style=display:none' in h

    def test_pay_page_bad_token(self):
        r = requests.get(f"{API}/square/pay?t=garbage")
        assert r.status_code in (400, 401, 403)

    def test_complete_invalid_tip_rejected(self):
        tok = order_token("MD1002", "0101")
        url = requests.post(f"{API}/track/MD1002/tip-link", headers={"X-Order-Token": tok, **NOAUTH}).json()["url"]
        t = url.split("t=", 1)[1]
        for tip in (0, 0.5, 150):
            r = requests.post(f"{API}/square/complete", json={"t": t, "source_id": "cnon:card-nonce-ok", "email": "a@b.co", "tip": tip})
            assert r.status_code == 400, (tip, r.text)


# ---- admin customer payments + refund validation (seeded fake payment; never reaches Square)
@pytest.fixture(scope="class")
def seeded(mdb):
    s = mdb.stops.find_one({"status": "delivered", "tracking_code": {"$ne": "MD1002"}, "customer_payment.payment_id": {"$exists": False}}, {"_id": 0})
    assert s
    pid, tid = f"TEST_pay_{uuid.uuid4().hex[:8]}", f"TEST_tip_{uuid.uuid4().hex[:8]}"
    drv = (mdb.routes.find_one({"id": s.get("route_id")}) or {}).get("driver_id") or "drv-1"
    old = {k: s.get(k) for k in ("customer_payment", "tips")}
    mdb.stops.update_one({"id": s["id"]}, {"$set": {
        "customer_payment": {"payment_id": pid, "amount": 10.0, "hst_pct": 13.0, "hst": 1.3, "tip": 3.0, "total": 14.3, "paid_at": "2026-01-01T00:00:00Z",
                             "card": "Visa ••1111", "email": "delivered@resend.dev", "tip_driver_id": drv,
                             "refunds": [{"id": "TEST_rf", "amount": 4.3, "status": "COMPLETED"}]},
        "tips": [{"payment_id": tid, "amount": 5.0, "total": 5.0, "paid_at": "2026-01-02T00:00:00Z", "card": "Visa ••1111", "email": "delivered@resend.dev", "driver_id": drv}]}})
    yield {"stop": s, "pid": pid, "tid": tid, "drv": drv}
    mdb.stops.update_one({"id": s["id"]}, {"$set": old})


class TestAdminPaymentsRefunds:
    def test_customer_payments_requires_admin(self, merchant_h):
        assert requests.get(f"{API}/admin/customer-payments", headers=NOAUTH).status_code in (401, 403)
        assert requests.get(f"{API}/admin/customer-payments", headers=merchant_h).status_code in (401, 403)

    def test_customer_payments_shape(self, admin_h, seeded):
        r = requests.get(f"{API}/admin/customer-payments", headers=admin_h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert set(j) >= {"payments", "tips_by_driver", "sandbox"}
        rows = {p["payment_id"]: p for p in j["payments"]}
        o, t = rows[seeded["pid"]], rows[seeded["tid"]]
        assert o["kind"] == "order" and o["refunded"] == 4.3 and o["refundable"] == 10.0 and o["tip"] == 3.0
        assert t["kind"] == "tip" and t["refundable"] == 5.0
        drv = next(d for d in j["tips_by_driver"] if d["count"] >= 2)
        assert drv["total"] >= 8.0

    def test_refund_unknown_payment_404(self, admin_h, seeded):
        r = requests.post(f"{API}/admin/stops/{seeded['stop']['id']}/refund", json={"payment_id": "nope", "amount": 1}, headers=admin_h)
        assert r.status_code == 404

    def test_refund_unknown_stop_404(self, admin_h):
        r = requests.post(f"{API}/admin/stops/nope/refund", json={"payment_id": "nope", "amount": 1}, headers=admin_h)
        assert r.status_code == 404

    @pytest.mark.parametrize("amt", [0, -5, 0.001])
    def test_refund_amount_le_zero_400(self, admin_h, seeded, amt):
        r = requests.post(f"{API}/admin/stops/{seeded['stop']['id']}/refund", json={"payment_id": seeded["pid"], "amount": amt}, headers=admin_h)
        assert r.status_code == 400, r.text

    def test_refund_over_refundable_400(self, admin_h, seeded):
        r = requests.post(f"{API}/admin/stops/{seeded['stop']['id']}/refund", json={"payment_id": seeded["pid"], "amount": 10.01}, headers=admin_h)
        assert r.status_code == 400 and "10.00" in r.text
        r = requests.post(f"{API}/admin/stops/{seeded['stop']['id']}/refund", json={"payment_id": seeded["tid"], "amount": 5.5}, headers=admin_h)
        assert r.status_code == 400

    def test_refund_non_admin(self, merchant_h, seeded):
        url = f"{API}/admin/stops/{seeded['stop']['id']}/refund"
        assert requests.post(url, json={"payment_id": seeded["pid"], "amount": 1}, headers=NOAUTH).status_code in (401, 403)
        assert requests.post(url, json={"payment_id": seeded["pid"], "amount": 1}, headers=merchant_h).status_code in (401, 403)


# ---- reviewer kit
class TestReviewerKit:
    def test_requires_admin(self, merchant_h):
        assert requests.get(f"{API}/admin/reviewer-kit", headers=NOAUTH).status_code in (401, 403)
        assert requests.post(f"{API}/admin/reviewer-kit", headers=merchant_h).status_code in (401, 403)

    def test_create_and_logins(self, admin_h, mdb):
        r = requests.post(f"{API}/admin/reviewer-kit", headers=admin_h)
        assert r.status_code == 200, r.text
        k = r.json()
        assert k["exists"] and k["store_email"] == "review-store@arrivewise.app" and k["driver_phone"] == "+15195550199"
        assert re.fullmatch(r"\d{6}", k["driver_pin"]) and len(k["store_password"]) >= 10
        assert [o["tracking_code"] for o in k["orders"]] == ["RV1001", "RV1002", "RV1003"]
        assert mdb.stops.count_documents({"route_id": "route-review"}) == 3
        # GET never returns secrets
        g = requests.get(f"{API}/admin/reviewer-kit", headers=admin_h).json()
        assert "store_password" not in g and "driver_pin" not in g and g["route"]
        # store login
        sl = requests.post(f"{API}/auth/login", json={"email": k["store_email"], "password": k["store_password"]}, headers=NOAUTH)
        assert sl.status_code == 200, sl.text
        assert sl.json()["user"]["merchant_id"] == "mer-review"
        # driver login
        dl = requests.post(f"{API}/auth/driver-login", json={"phone": k["driver_phone"], "pin": k["driver_pin"]}, headers=NOAUTH)
        assert dl.status_code == 200, dl.text
        dh = {"Authorization": f"Bearer {dl.json()['token']}", **NOAUTH}
        rt = requests.get(f"{API}/drivers/drv-review/route", headers=dh)
        assert rt.status_code == 200, rt.text
        assert len((rt.json().get("route") or rt.json()).get("stops", rt.json().get("stops", []))) == 3
        # customer unlock of a review order
        assert requests.post(f"{API}/track/RV1001/verify", json={"last4": "0190"}, headers=NOAUTH).status_code == 200

    def test_reset_rotates_password(self, admin_h):
        a = requests.post(f"{API}/admin/reviewer-kit", headers=admin_h).json()
        b = requests.post(f"{API}/admin/reviewer-kit", headers=admin_h).json()
        assert a["store_password"] != b["store_password"]
        old = requests.post(f"{API}/auth/login", json={"email": a["store_email"], "password": a["store_password"]}, headers=NOAUTH)
        assert old.status_code == 401
        assert requests.post(f"{API}/auth/login", json={"email": b["store_email"], "password": b["store_password"]}, headers=NOAUTH).status_code == 200
