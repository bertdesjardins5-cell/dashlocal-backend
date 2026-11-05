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
    r = requests.post(f"{API}/auth/login", json={"email": "books@localdash.app", "password": "Books2026"}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip("merchant login failed")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


def order_token(code, last4):
    r = requests.post(f"{API}/track/{code}/verify", json={"last4": last4}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def seeded(mdb):
    """A delivered stop with a past order payment (partly refunded) and an older driver tip."""
    sid, pid, tid = f"TEST_{uuid.uuid4().hex[:8]}", f"TESTPAY_{uuid.uuid4().hex[:6]}", f"TESTTIP_{uuid.uuid4().hex[:6]}"
    stop = {"id": sid, "tracking_code": f"TS{uuid.uuid4().hex[:6].upper()}", "status": "delivered", "merchant_id": "mer-1", "customer_name": "Test Customer",
            "customer_payment": {"payment_id": pid, "amount": 12.65, "hst_pct": 13, "hst": 1.65, "total": 14.3, "paid_at": "2026-10-29T10:00:00+00:00",
                                 "card": "Visa ••1111", "email": "t@example.com", "refunds": [{"id": "RF1", "amount": 4.3, "status": "COMPLETED", "reason": "x", "at": "2026-10-29T11:00:00+00:00"}]},
            "tips": [{"payment_id": tid, "amount": 5.0, "total": 5.0, "paid_at": "2026-10-29T12:00:00+00:00", "card": "Visa ••1111", "email": "t@example.com"}]}
    mdb.stops.insert_one(dict(stop))
    yield {"stop": stop, "pid": pid, "tid": tid}
    mdb.stops.delete_one({"id": sid})



class TestTipsRemoved:
    def test_tip_link_gone(self):
        assert requests.post(f"{API}/track/MD1002/tip-link", headers=NOAUTH).status_code in (404, 405)


class TestAdminPaymentsRefunds:
    def test_customer_payments_requires_admin(self, merchant_h):
        assert requests.get(f"{API}/admin/customer-payments", headers=NOAUTH).status_code in (401, 403)
        assert requests.get(f"{API}/admin/customer-payments", headers=merchant_h).status_code in (401, 403)

    def test_customer_payments_shape(self, admin_h, seeded):
        r = requests.get(f"{API}/admin/customer-payments", headers=admin_h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert set(j) >= {"payments", "sandbox"} and "tips_by_driver" not in j
        rows = {p["payment_id"]: p for p in j["payments"]}
        o, t = rows[seeded["pid"]], rows[seeded["tid"]]
        assert o["kind"] == "order" and o["refunded"] == 4.3 and o["refundable"] == 10.0
        assert t["kind"] == "tip" and t["refundable"] == 5.0  # older tips stay refundable

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
        assert k["exists"] and k["store_email"] == "review-store@localdash.app" and k["driver_phone"] == "+15195550199"
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
