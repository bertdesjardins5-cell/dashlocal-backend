# Launch cleanup (admin "remove all demo data before launch") — non-destructive checks via the preview URL
# and a live-host (Host: dashlocal.app) wrong-confirm check. The destructive wipe lives in
# test_launch_cleanup_live.py and only runs with LAUNCH_CLEANUP_DESTRUCTIVE=1.
import os
import requests
import pytest
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
LOCAL = "http://localhost:8001"
LIVE = {"Host": "dashlocal.app"}
ADMIN = {"email": os.environ.get("ADMIN_EMAIL", "admin@localdash.app"), "password": os.environ.get("ADMIN_PASSWORD")}
NO_AUTO = {"X-No-Auto-Auth": "1"}


def _login(base, body, headers=None, path="/api/auth/login"):
    r = requests.post(f"{base}{path}", json=body, headers={**NO_AUTO, **(headers or {})})
    return r


@pytest.fixture(scope="module")
def admin_tok():
    r = _login(BASE_URL, ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def merchant_tok():
    r = _login(BASE_URL, {"email": "bakery@localdash.app", "password": "Bakery2026"})
    if r.status_code != 200:
        pytest.skip(f"demo merchant login failed: {r.status_code} {r.text}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def driver_tok():
    r = _login(BASE_URL, {"phone": "+15195550101", "pin": "482913"}, path="/api/auth/driver-login")
    if r.status_code != 200:
        pytest.skip(f"demo driver login failed: {r.status_code} {r.text}")
    return r.json()["token"]


def _h(tok):
    return {**NO_AUTO, "Authorization": f"Bearer {tok}"}


# --- status endpoint
class TestStatus:
    def test_status_admin_preview(self, admin_tok):
        r = requests.get(f"{BASE_URL}/api/admin/launch-cleanup", headers=_h(admin_tok))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["preview"] is True
        assert d["available"] is False
        assert d["done_at"] is None
        assert set(d["counts"]) == {"merchants", "drivers", "stops", "routes", "invoices", "logins"}
        assert all(isinstance(v, int) for v in d["counts"].values())
        assert d["counts"]["drivers"] >= 3 and d["counts"]["merchants"] >= 2 and d["counts"]["logins"] >= 5

    def test_status_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/admin/launch-cleanup", headers=NO_AUTO)
        assert r.status_code == 401

    def test_status_merchant_forbidden(self, merchant_tok):
        r = requests.get(f"{BASE_URL}/api/admin/launch-cleanup", headers=_h(merchant_tok))
        assert r.status_code == 403

    def test_status_driver_forbidden(self, driver_tok):
        r = requests.get(f"{BASE_URL}/api/admin/launch-cleanup", headers=_h(driver_tok))
        assert r.status_code == 403

    def test_status_live_host_available(self):
        r = _login(LOCAL, ADMIN, LIVE)
        assert r.status_code == 200, r.text
        s = requests.get(f"{LOCAL}/api/admin/launch-cleanup", headers={**LIVE, **_h(r.json()["token"])})
        assert s.status_code == 200
        assert s.json()["preview"] is False and s.json()["available"] is True


# --- run endpoint guards (never wipes)
class TestRunGuards:
    def test_run_preview_blocked(self, admin_tok):
        r = requests.post(f"{BASE_URL}/api/admin/launch-cleanup", json={"confirm": "DELETE"}, headers=_h(admin_tok))
        assert r.status_code == 403
        assert "Launch cleanup only runs on the live app" in r.json()["detail"]

    def test_run_no_auth(self):
        r = requests.post(f"{BASE_URL}/api/admin/launch-cleanup", json={"confirm": "DELETE"}, headers=NO_AUTO)
        assert r.status_code == 401

    def test_run_merchant_forbidden(self, merchant_tok):
        r = requests.post(f"{BASE_URL}/api/admin/launch-cleanup", json={"confirm": "DELETE"}, headers=_h(merchant_tok))
        assert r.status_code == 403

    def test_run_live_wrong_confirm(self):
        tok = _login(LOCAL, ADMIN, LIVE).json()["token"]
        for bad in ("DEL", "remove", ""):
            r = requests.post(f"{LOCAL}/api/admin/launch-cleanup", json={"confirm": bad}, headers={**LIVE, **_h(tok)})
            assert r.status_code == 400, (bad, r.text)
            assert r.json()["detail"] == "Type DELETE to confirm"

    def test_run_live_missing_body(self):
        tok = _login(LOCAL, ADMIN, LIVE).json()["token"]
        r = requests.post(f"{LOCAL}/api/admin/launch-cleanup", json={}, headers={**LIVE, **_h(tok)})
        assert r.status_code == 422

    def test_data_untouched(self, admin_tok):
        d = requests.get(f"{BASE_URL}/api/admin/launch-cleanup", headers=_h(admin_tok)).json()
        assert d["counts"]["drivers"] >= 3 and d["done_at"] is None
