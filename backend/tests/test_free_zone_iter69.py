# Merchant free distance zone (GET /api/merchants/{id} -> free_zone)
import os
import requests
import pytest

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"})
    if r.status_code != 200:
        pytest.skip(f"admin login failed {r.status_code}")
    s.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return s


def test_mer1_free_zone_depot(admin):
    r = admin.get(f"{BASE_URL}/api/merchants/mer-1")
    assert r.status_code == 200
    z = r.json()["free_zone"]
    assert z["from"] == "depot"
    assert abs(z["lat"] - 43.4516) < 1e-3 and abs(z["lng"] - (-80.4925)) < 1e-3
    assert z["km"] == 3.0 and z["per_km"] == 1.25


def test_mer2_free_zone_pharmacy(admin):
    r = admin.get(f"{BASE_URL}/api/merchants/mer-2")
    assert r.status_code == 200
    d = r.json()
    z = d["free_zone"]
    assert z["from"] == "pharmacy"
    assert z["km"] == 3.0 and z["per_km"] == 1.25
    assert isinstance(z["lat"], float) and isinstance(z["lng"], float)
    assert not (abs(z["lat"] - 43.4516) < 1e-4 and abs(z["lng"] + 80.4925) < 1e-4), "pharmacy origin should not equal depot"
    pick = (d.get("runs") or [{}])[0].get("pickup") or {}
    if pick.get("lat") is not None:
        assert abs(pick["lat"] - z["lat"]) < 1e-3 and abs(pick["lng"] - z["lng"]) < 1e-3


def test_free_zone_requires_auth():
    r = requests.get(f"{BASE_URL}/api/merchants/mer-1", headers={"X-No-Auto-Auth": "1"})
    assert r.status_code in (401, 403)


def test_unknown_merchant_404(admin):
    assert admin.get(f"{BASE_URL}/api/merchants/mer-nope").status_code == 404
