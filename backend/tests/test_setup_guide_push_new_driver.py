# First-store setup guide, admin "add driver", push registration and non-blocking dispatch push (iteration 43)
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
DB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
SETUP_KEYS = ["demo_removed", "setup_done", "setup_pricing"]
NA = {"X-No-Auto-Auth": "1"}


def login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_h():
    return {"Authorization": f"Bearer {login(os.environ['ADMIN_EMAIL'], os.environ['ADMIN_PASSWORD'])}"}


@pytest.fixture(scope="module")
def merchant_h():
    return {"Authorization": f"Bearer {login('bakery@arrivewise.app', 'Bakery2026')}"}


@pytest.fixture(scope="module")
def driver_auth():
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"})
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code} {r.text}")
    return r.json()


def _log_tail():
    try:
        with open("/var/log/supervisor/backend.err.log", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200000))
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return ""


# ---- dispatch must not be blocked by push (runs first: demo_removed would block /admin/reset)
class TestDispatchNonBlockingPush:
    def test_batch_dispatch_succeeds(self, admin_h):
        DB.settings.delete_many({"key": {"$in": SETUP_KEYS}})
        r = requests.post(f"{BASE_URL}/api/admin/reset", headers=admin_h)
        assert r.status_code == 200, r.text
        z = requests.get(f"{BASE_URL}/api/zones", headers=admin_h)
        assert z.status_code == 200 and z.json(), z.text
        zone = z.json()[0]["zone"]
        before = _log_tail().count("Push failed (non-blocking)")
        t = time.time()
        r = requests.post(f"{BASE_URL}/api/routes/batch", headers=admin_h, json={"zone": zone, "driver_id": "drv-2"})
        elapsed = time.time() - t
        assert r.status_code == 200, r.text
        assert r.json().get("stops"), r.json()
        assert elapsed < 8, f"batch took {elapsed:.1f}s"
        time.sleep(4)
        after = _log_tail().count("Push failed (non-blocking)")
        print(f"batch elapsed={elapsed:.2f}s non-blocking push logs +{after - before}")
        assert after > before, "expected 'Push failed (non-blocking)' log for placeholder push key"

    def test_auto_dispatch_succeeds(self, admin_h):
        r = requests.post(f"{BASE_URL}/api/admin/reset", headers=admin_h)
        assert r.status_code == 200, r.text
        t = time.time()
        r = requests.post(f"{BASE_URL}/api/routes/auto-dispatch", headers=admin_h, json={})
        elapsed = time.time() - t
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["dispatched"] > 0 and d["routes"], d
        assert elapsed < 10, f"auto-dispatch took {elapsed:.1f}s"
        print(f"auto-dispatch elapsed={elapsed:.2f}s routes={len(d['routes'])}")
        # restore demo state
        assert requests.post(f"{BASE_URL}/api/admin/reset", headers=admin_h).status_code == 200


# ---- GET /api/admin/setup auth + state machine
class TestSetupGuide:
    def test_setup_requires_admin(self, merchant_h, driver_auth):
        assert requests.get(f"{BASE_URL}/api/admin/setup", headers=NA).status_code == 401
        assert requests.get(f"{BASE_URL}/api/admin/setup", headers={**NA, **merchant_h}).status_code == 403
        dh = {**NA, "Authorization": f"Bearer {driver_auth['token']}"}
        assert requests.get(f"{BASE_URL}/api/admin/setup", headers=dh).status_code == 403
        assert requests.post(f"{BASE_URL}/api/admin/setup/finish", headers={**NA, **merchant_h}).status_code == 403
        assert requests.post(f"{BASE_URL}/api/admin/setup/pricing-reviewed", headers=NA).status_code == 401

    def test_setup_state_flow(self, admin_h):
        try:
            DB.settings.delete_many({"key": {"$in": SETUP_KEYS}})
            s = requests.get(f"{BASE_URL}/api/admin/setup", headers=admin_h).json()
            assert s == {"show": False, "done": False, "steps": {"pricing": False, "store": True, "driver": True}}, s
            DB.settings.insert_one({"key": "demo_removed", "value": "2026-06-01T00:00:00Z"})
            s = requests.get(f"{BASE_URL}/api/admin/setup", headers=admin_h).json()
            assert s["show"] is True and s["done"] is False, s
            # pricing-reviewed ticks pricing -> all steps done -> hidden
            assert requests.post(f"{BASE_URL}/api/admin/setup/pricing-reviewed", headers=admin_h).json() == {"ok": True}
            s = requests.get(f"{BASE_URL}/api/admin/setup", headers=admin_h).json()
            assert s["steps"]["pricing"] is True and s["done"] is True and s["show"] is False, s
            # saving pricing also ticks the step
            DB.settings.delete_one({"key": "setup_pricing"})
            p = requests.get(f"{BASE_URL}/api/pricing", headers=admin_h).json()
            body = {k: p[k] for k in ("base_fee", "included_km", "per_km", "priority_fee", "charge_failed", "tax_pct")}
            r = requests.put(f"{BASE_URL}/api/admin/pricing", headers=admin_h, json=body)
            assert r.status_code == 200, r.text
            assert requests.get(f"{BASE_URL}/api/admin/setup", headers=admin_h).json()["steps"]["pricing"] is True
            # finish hides even with a step left
            DB.settings.delete_one({"key": "setup_pricing"})
            assert requests.get(f"{BASE_URL}/api/admin/setup", headers=admin_h).json()["show"] is True
            assert requests.post(f"{BASE_URL}/api/admin/setup/finish", headers=admin_h).json() == {"ok": True}
            s = requests.get(f"{BASE_URL}/api/admin/setup", headers=admin_h).json()
            assert s["done"] is True and s["show"] is False and s["steps"]["pricing"] is False, s
        finally:
            DB.settings.delete_many({"key": {"$in": SETUP_KEYS}})


# ---- POST /api/admin/drivers + push registration
class TestAddDriverAndPush:
    created = []

    @classmethod
    def teardown_class(cls):
        ids = [d["id"] for d in DB.drivers.find({"name": {"$regex": "^TEST_"}}, {"id": 1})]
        DB.users.delete_many({"role": "driver", "driver_id": {"$in": ids}})
        DB.drivers.delete_many({"id": {"$in": ids}})
        DB.login_attempts.delete_many({"key": {"$regex": "5550999"}})

    def _post(self, h, **kw):
        body = {"name": "TEST_Nova", "phone": "519 555 0999", "vehicle": "Civic", "pin": "583014", **kw}
        return requests.post(f"{BASE_URL}/api/admin/drivers", headers={**NA, **h} if h else NA, json=body)

    def test_auth_required(self, merchant_h):
        assert self._post(None).status_code == 401
        assert self._post(merchant_h).status_code == 403

    @pytest.mark.parametrize("kw,code", [({"name": "  "}, 400), ({"vehicle": ""}, 400), ({"phone": "12"}, 400),
                                         ({"pin": "12345"}, 400), ({"pin": "abcdef"}, 400), ({"pin": "123456"}, 400),
                                         ({"pin": "777777"}, 400), ({"compass_zone": "Middle"}, 400),
                                         ({"phone": "+15195550101"}, 409)])
    def test_validation(self, admin_h, kw, code):
        r = self._post(admin_h, **kw)
        assert r.status_code == code, f"{kw} -> {r.status_code} {r.text}"

    def test_create_login_and_duplicate(self, admin_h):
        r = self._post(admin_h, compass_zone="North")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["phone"] == "+15195550999" and d["has_pin"] is True and d["name"] == "TEST_Nova", d
        lst = requests.get(f"{BASE_URL}/api/admin/driver-accounts", headers=admin_h).json()
        assert any(x["id"] == d["id"] and x["has_pin"] for x in lst)
        assert DB.drivers.find_one({"id": d["id"]})["compass_zone"] == "North"
        assert self._post(admin_h, phone="(519) 555-0999").status_code == 409
        lg = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "5195550999", "pin": "583014"})
        assert lg.status_code == 200, lg.text
        TestAddDriverAndPush.created.append(lg.json())
        assert lg.json()["user"]["driver_id"] == d["id"]

    def test_register_push(self):
        if not self.created:
            pytest.skip("no driver")
        tok, uid = self.created[0]["token"], self.created[0]["user"]["id"]
        h = {**NA, "Authorization": f"Bearer {tok}"}
        url = f"{BASE_URL}/api/register-push"
        assert requests.post(url, headers=NA, json={"user_id": uid, "platform": "android", "device_token": "x"}).status_code == 401
        assert requests.post(url, headers=h, json={"user_id": "someone-else", "platform": "android", "device_token": "x"}).status_code == 403
        assert requests.post(url, headers=h, json={"user_id": uid, "platform": "web", "device_token": "x"}).status_code == 400
        assert requests.post(url, headers=h, json={"user_id": uid, "platform": "ios", "device_token": "  "}).status_code == 400
        assert requests.post(url, headers=h, json={"user_id": uid}).status_code == 422
        r = requests.post(url, headers=h, json={"user_id": uid, "platform": "android", "device_token": "ExponentPushToken[test]"})
        print("register-push:", r.status_code, r.text)
        assert r.status_code == 500 and "EMERGENT_PUSH_KEY" in r.text
