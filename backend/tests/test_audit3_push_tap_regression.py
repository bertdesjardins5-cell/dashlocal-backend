# Security audit #3 regression (iteration 44): privacy officer, register-push auth, admin setup gating
import os
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_BACKEND_URL") or os.environ["EXPO_PUBLIC_BACKEND_URL"]).rstrip("/")
NA = {"X-No-Auto-Auth": "1"}


@pytest.fixture(scope="module")
def admin_h():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]})
    assert r.status_code == 200, r.text
    return {**NA, "Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def driver():
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"})
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    d = r.json()
    return {**NA, "Authorization": f"Bearer {d['token']}"}, d["user"]["id"]


@pytest.fixture(scope="module")
def merchant_h():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "bakery@localdash.app", "password": "Bakery2026"})
    assert r.status_code == 200, r.text
    return {**NA, "Authorization": f"Bearer {r.json()['token']}"}


# privacy officer (public)
def test_privacy_officer():
    r = requests.get(f"{BASE_URL}/api/privacy/officer", headers=NA)
    assert r.status_code == 200
    assert r.json() == {"officer_name": "Albert Desjardins", "officer_email": "bertdesjardins@hotmail.com"}


# register-push auth checks
def test_register_push_no_token():
    r = requests.post(f"{BASE_URL}/api/register-push", headers=NA, json={"user_id": "x", "platform": "ios", "device_token": "t"})
    assert r.status_code == 401


def test_register_push_other_user(driver):
    h, _ = driver
    r = requests.post(f"{BASE_URL}/api/register-push", headers=h, json={"user_id": "someone-else", "platform": "ios", "device_token": "t"})
    assert r.status_code == 403


def test_register_push_bad_platform(driver):
    h, uid = driver
    r = requests.post(f"{BASE_URL}/api/register-push", headers=h, json={"user_id": uid, "platform": "web", "device_token": "t"})
    assert r.status_code == 400


def test_register_push_own_placeholder_key(driver):
    # EMERGENT_PUSH_KEY is a placeholder in preview -> expected 500
    h, uid = driver
    r = requests.post(f"{BASE_URL}/api/register-push", headers=h, json={"user_id": uid, "platform": "android", "device_token": "TEST_tok"})
    assert r.status_code in (201, 500)
    if r.status_code == 500:
        assert r.json()["detail"] == "EMERGENT_PUSH_KEY missing or invalid"


# admin setup gating
def test_admin_setup_gating(admin_h, merchant_h, driver):
    assert requests.get(f"{BASE_URL}/api/admin/setup", headers=NA).status_code == 401
    assert requests.get(f"{BASE_URL}/api/admin/setup", headers=merchant_h).status_code == 403
    assert requests.get(f"{BASE_URL}/api/admin/setup", headers=driver[0]).status_code == 403
    r = requests.get(f"{BASE_URL}/api/admin/setup", headers=admin_h)
    assert r.status_code == 200
    body = r.json()
    assert "show" in body and isinstance(body.get("steps"), dict)
