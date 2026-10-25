# Shop testimonials (landing page reviews): public list + admin CRUD, auth gating, validation
import os
import time
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
NOAUTH = {"X-No-Auto-Auth": "1"}


def _login(path, body):
    r = requests.post(f"{BASE_URL}/api/auth/{path}", json=body, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"login {path} failed {r.status_code} {r.text}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def admin():
    return _login("login", {"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"})


@pytest.fixture(scope="module")
def merchant():
    return _login("login", {"email": "bakery@dashlocal.app", "password": "Bakery2026"})


@pytest.fixture(scope="module")
def driver():
    return _login("driver-login", {"phone": "(415) 555-0101", "pin": "482913"})


@pytest.fixture(scope="module", autouse=True)
def cleanup(admin):
    yield
    for t in requests.get(f"{BASE_URL}/api/admin/testimonials", headers=admin).json():
        if t["shop"].startswith("TEST_"):
            requests.delete(f"{BASE_URL}/api/admin/testimonials/{t['id']}", headers=admin)


def _create(admin, **kw):
    body = {"quote": "TEST quote", "shop": "TEST_Shop", "person": None, "published": True, **kw}
    r = requests.post(f"{BASE_URL}/api/admin/testimonials", json=body, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


class TestAuthGating:
    @pytest.mark.parametrize("method,path", [("GET", "/api/admin/testimonials"), ("POST", "/api/admin/testimonials"),
                                             ("PUT", "/api/admin/testimonials/x"), ("DELETE", "/api/admin/testimonials/x")])
    def test_no_auth_401(self, method, path):
        r = requests.request(method, f"{BASE_URL}{path}", json={"quote": "a", "shop": "TEST_b"}, headers=NOAUTH)
        assert r.status_code == 401, r.text

    @pytest.mark.parametrize("who", ["merchant", "driver"])
    @pytest.mark.parametrize("method,path", [("GET", "/api/admin/testimonials"), ("POST", "/api/admin/testimonials"),
                                             ("PUT", "/api/admin/testimonials/x"), ("DELETE", "/api/admin/testimonials/x")])
    def test_non_admin_403(self, who, method, path, request):
        h = request.getfixturevalue(who)
        r = requests.request(method, f"{BASE_URL}{path}", json={"quote": "a", "shop": "TEST_b"}, headers=h)
        assert r.status_code == 403, r.text

    def test_public_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/testimonials", headers=NOAUTH)
        assert r.status_code == 200 and isinstance(r.json(), list)


class TestValidation:
    @pytest.mark.parametrize("body", [{"quote": "", "shop": "TEST_x"}, {"quote": "   ", "shop": "TEST_x"},
                                      {"quote": "hi", "shop": ""}, {"quote": "hi", "shop": " \n "}])
    def test_empty_400(self, admin, body):
        r = requests.post(f"{BASE_URL}/api/admin/testimonials", json=body, headers=admin)
        assert r.status_code == 400, r.text

    def test_missing_field_422(self, admin):
        r = requests.post(f"{BASE_URL}/api/admin/testimonials", json={"quote": "hi"}, headers=admin)
        assert r.status_code == 422

    def test_whitespace_and_limits(self, admin):
        d = _create(admin, quote="  Great   \n service  " + "q" * 500, shop="TEST_  Mission   Bakery " + "s" * 100,
                    person="  Ana \t owner " + "p" * 100)
        assert d["quote"].startswith("Great service q") and len(d["quote"]) == 400
        assert d["shop"].startswith("TEST_ Mission Bakery s") and len(d["shop"]) == 80
        assert d["person"].startswith("Ana owner p") and len(d["person"]) == 60
        assert set(d) >= {"id", "quote", "shop", "person", "published", "created_at", "updated_at"} and "_id" not in d

    def test_blank_person_null(self, admin):
        assert _create(admin, person="   ")["person"] is None

    def test_unknown_id_404(self, admin):
        r = requests.put(f"{BASE_URL}/api/admin/testimonials/nope", json={"quote": "a", "shop": "TEST_b"}, headers=admin)
        assert r.status_code == 404
        assert requests.delete(f"{BASE_URL}/api/admin/testimonials/nope", headers=admin).status_code == 404


class TestCrudAndPublic:
    def test_crud_toggle_public(self, admin):
        d = _create(admin, quote="TEST love it", shop="TEST_Crud", person="Ana, owner")
        lst = requests.get(f"{BASE_URL}/api/admin/testimonials", headers=admin).json()
        assert any(t["id"] == d["id"] for t in lst)
        pub = requests.get(f"{BASE_URL}/api/testimonials").json()
        item = next(t for t in pub if t["id"] == d["id"])
        assert set(item) == {"id", "quote", "person", "shop"} and item["person"] == "Ana, owner"
        # edit
        r = requests.put(f"{BASE_URL}/api/admin/testimonials/{d['id']}", json={"quote": "TEST edited", "shop": "TEST_Crud", "person": None, "published": True}, headers=admin)
        assert r.status_code == 200 and r.json()["quote"] == "TEST edited" and r.json()["person"] is None
        # hide
        r = requests.put(f"{BASE_URL}/api/admin/testimonials/{d['id']}", json={"quote": "TEST edited", "shop": "TEST_Crud", "published": False}, headers=admin)
        assert r.json()["published"] is False
        assert all(t["id"] != d["id"] for t in requests.get(f"{BASE_URL}/api/testimonials").json())
        # still listed for admin
        assert any(t["id"] == d["id"] for t in requests.get(f"{BASE_URL}/api/admin/testimonials", headers=admin).json())
        # show again
        requests.put(f"{BASE_URL}/api/admin/testimonials/{d['id']}", json={"quote": "TEST edited", "shop": "TEST_Crud", "published": True}, headers=admin)
        assert any(t["id"] == d["id"] for t in requests.get(f"{BASE_URL}/api/testimonials").json())
        # delete
        assert requests.delete(f"{BASE_URL}/api/admin/testimonials/{d['id']}", headers=admin).json() == {"ok": True}
        assert all(t["id"] != d["id"] for t in requests.get(f"{BASE_URL}/api/admin/testimonials", headers=admin).json())
        assert requests.delete(f"{BASE_URL}/api/admin/testimonials/{d['id']}", headers=admin).status_code == 404

    def test_public_newest_first_max6(self, admin):
        ids = []
        for i in range(8):
            ids.append(_create(admin, quote=f"TEST order {i}", shop=f"TEST_Order{i}", published=(i != 7))["id"])
            time.sleep(0.01)
        pub = requests.get(f"{BASE_URL}/api/testimonials").json()
        assert len(pub) == 6
        # published are 0..6; newest 6 = 6..1
        assert [t["id"] for t in pub] == [ids[i] for i in range(6, 0, -1)]
        assert ids[7] not in [t["id"] for t in pub]
