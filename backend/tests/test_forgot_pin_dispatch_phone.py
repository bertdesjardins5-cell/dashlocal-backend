# Driver "Forgot PIN" request + dispatch phone setting + PIN reset clears request (iteration 59)
# NOTE: run serially (-n 0): the per-IP rate-limit test shares db.login_attempts with the module cleanup
import os
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
MSG = "If this number belongs to a ArriveWise driver, dispatch has been asked to reset your PIN. You'll get a text with a new one."
NOAUTH = {"X-No-Auto-Auth": "1"}

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def clear_pinreq():
    db.login_attempts.delete_many({"key": {"$regex": "^pinreq:"}})


@pytest.fixture(scope="module")
def admin_h():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}, headers=NOAUTH)
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def driver_h():
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "+15195550103", "pin": "264871"}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code} {r.text}")
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    clear_pinreq()
    db.drivers.update_one({"id": "drv-3"}, {"$set": {"pin_request_at": None}})
    orig = db.settings.find_one({"key": "dispatch_phone"}, {"_id": 0})
    yield
    clear_pinreq()
    db.drivers.update_one({"id": "drv-3"}, {"$set": {"pin_request_at": None}})
    if orig is None:
        db.settings.delete_one({"key": "dispatch_phone"})
    else:
        db.settings.update_one({"key": "dispatch_phone"}, {"$set": orig}, upsert=True)


# ---- forgot-pin: generic reply
class TestForgotPin:
    def test_unknown_number_generic(self):
        r = requests.post(f"{BASE_URL}/api/auth/driver-forgot-pin", json={"phone": "+15195559999"})
        assert r.status_code == 200 and r.json()["message"] == MSG

    def test_garbage_number_generic(self):
        r = requests.post(f"{BASE_URL}/api/auth/driver-forgot-pin", json={"phone": "abc"})
        assert r.status_code == 200 and r.json()["message"] == MSG

    def test_missing_phone_422(self):
        r = requests.post(f"{BASE_URL}/api/auth/driver-forgot-pin", json={})
        assert r.status_code == 422

    def test_known_number_sets_request_once(self, admin_h):
        r = requests.post(f"{BASE_URL}/api/auth/driver-forgot-pin", json={"phone": "(519) 555-0103"})
        assert r.status_code == 200 and r.json()["message"] == MSG
        first = db.drivers.find_one({"id": "drv-3"})["pin_request_at"]
        assert first
        time.sleep(1)
        r2 = requests.post(f"{BASE_URL}/api/auth/driver-forgot-pin", json={"phone": "+15195550103"})
        assert r2.status_code == 200 and r2.json()["message"] == MSG
        assert db.drivers.find_one({"id": "drv-3"})["pin_request_at"] == first  # 10-min cooldown
        acc = requests.get(f"{BASE_URL}/api/admin/driver-accounts", headers=admin_h).json()
        d3 = next(a for a in acc if a["id"] == "drv-3")
        assert d3["pin_request_at"] == first

    def test_set_pin_clears_request(self, admin_h):
        r = requests.put(f"{BASE_URL}/api/admin/drivers/drv-3/pin", json={"pin": "264871"}, headers=admin_h)
        assert r.status_code == 200
        assert db.drivers.find_one({"id": "drv-3"}).get("pin_request_at") is None
        acc = requests.get(f"{BASE_URL}/api/admin/driver-accounts", headers=admin_h).json()
        assert next(a for a in acc if a["id"] == "drv-3")["pin_request_at"] is None

    def test_per_ip_limit_10_then_429(self):
        clear_pinreq()
        codes = [requests.post(f"{BASE_URL}/api/auth/driver-forgot-pin", json={"phone": "+15195559998"}).status_code for _ in range(11)]
        clear_pinreq()
        assert codes[:10] == [200] * 10, codes
        assert codes[10] == 429, codes


# ---- dispatch phone setting (admin only)
class TestDispatchPhone:
    def test_get_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/admin/dispatch-phone", headers=NOAUTH)
        assert r.status_code == 401

    def test_put_requires_auth(self):
        r = requests.put(f"{BASE_URL}/api/admin/dispatch-phone", json={"phone": "5195550199"}, headers=NOAUTH)
        assert r.status_code == 401

    def test_driver_forbidden(self, driver_h):
        r = requests.get(f"{BASE_URL}/api/admin/dispatch-phone", headers={**driver_h, **NOAUTH})
        assert r.status_code == 403
        r = requests.put(f"{BASE_URL}/api/admin/dispatch-phone", json={"phone": "5195550199"}, headers={**driver_h, **NOAUTH})
        assert r.status_code == 403

    def test_invalid_400(self, admin_h):
        r = requests.put(f"{BASE_URL}/api/admin/dispatch-phone", json={"phone": "12ab"}, headers=admin_h)
        assert r.status_code == 400 and "valid" in r.json()["detail"].lower()

    def test_valid_saves_then_get(self, admin_h):
        r = requests.put(f"{BASE_URL}/api/admin/dispatch-phone", json={"phone": "519 555 0199"}, headers=admin_h)
        assert r.status_code == 200 and r.json()["phone"] == "+15195550199"
        assert requests.get(f"{BASE_URL}/api/admin/dispatch-phone", headers=admin_h).json()["phone"] == "+15195550199"

    def test_empty_clears(self, admin_h):
        r = requests.put(f"{BASE_URL}/api/admin/dispatch-phone", json={"phone": ""}, headers=admin_h)
        assert r.status_code == 200 and r.json()["phone"] is None
        assert requests.get(f"{BASE_URL}/api/admin/dispatch-phone", headers=admin_h).json()["phone"] is None


# ---- one-tap reset + text (admin only)
class TestTextPin:
    def test_text_pin_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/admin/drivers/drv-3/pin/text", headers=NOAUTH)
        assert r.status_code == 401

    def test_text_pin_404(self, admin_h):
        r = requests.post(f"{BASE_URL}/api/admin/drivers/drv-nope/pin/text", headers=admin_h)
        assert r.status_code == 404

    def test_text_pin_resets_and_clears(self, admin_h):
        db.drivers.update_one({"id": "drv-3"}, {"$set": {"pin_request_at": "2026-01-01T00:00:00+00:00", "pin_texted_at": None}})
        r = requests.post(f"{BASE_URL}/api/admin/drivers/drv-3/pin/text", headers=admin_h)
        try:
            assert r.status_code == 200, r.text
            j = r.json()
            assert j["invite"] in ("sent", "failed", "not_configured")
            if j["invite"] != "sent":
                assert len(j["pin"]) == 6 and j["pin"].isdigit()
                lr = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "+15195550103", "pin": j["pin"]}, headers=NOAUTH)
                assert lr.status_code == 200
            assert db.drivers.find_one({"id": "drv-3"}).get("pin_request_at") is None
            r2 = requests.post(f"{BASE_URL}/api/admin/drivers/drv-3/pin/text", headers=admin_h)
            assert r2.status_code == 429
        finally:
            requests.put(f"{BASE_URL}/api/admin/drivers/drv-3/pin", json={"pin": "264871"}, headers=admin_h)
            db.drivers.update_one({"id": "drv-3"}, {"$set": {"pin_texted_at": None}})
