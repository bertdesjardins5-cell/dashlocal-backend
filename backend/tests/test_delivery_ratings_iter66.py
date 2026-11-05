# Delivery Ratings (iter66): comment on rating, store/admin ratings views, driver feedback comments, trends, screenshots zip.
# Needs `python tests/ui_seed_ratings_iter66.py seed` first (TEST_ stop TS9901 on mer-1 / drv-1 route).
import os
import json
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
NOAUTH = {"X-No-Auto-Auth": "1"}
STATE = json.load(open("/tmp/ratings_iter66.json"))["api"]
COMMENT_RAW = "   TEST_ Great driver, left it with the concierge. " + "x" * 400


def login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def admin():
    return login("admin@dashlocal.app", "DashLocalAdmin2026")


@pytest.fixture(scope="module")
def bakery():
    return login("bakery@dashlocal.app", "Bakery2026")


@pytest.fixture(scope="module")
def driver():
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": "(519) 555-0101", "pin": "482913"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


# ---- merchant ratings access
class TestMerchantRatings:
    def test_own_ratings_shape(self, bakery):
        r = requests.get(f"{BASE_URL}/api/merchants/mer-1/ratings", headers=bakery)
        assert r.status_code == 200, r.text
        d = r.json()
        assert set(d) >= {"count", "avg", "stars", "drivers", "recent"}
        assert list(d["stars"].keys()) == ["5", "4", "3", "2", "1"]

    def test_other_store_forbidden(self, bakery):
        r = requests.get(f"{BASE_URL}/api/merchants/mer-2/ratings", headers=bakery)
        assert r.status_code == 403, r.text

    def test_unauth_401(self):
        r = requests.get(f"{BASE_URL}/api/merchants/mer-1/ratings", headers=NOAUTH)
        assert r.status_code == 401


# ---- admin ratings access
class TestAdminRatings:
    def test_admin_all(self, admin):
        r = requests.get(f"{BASE_URL}/api/admin/ratings", headers=admin)
        assert r.status_code == 200
        d = r.json()
        assert d["count"] >= 1 and isinstance(d["avg"], float)
        assert sum(d["stars"].values()) == d["count"]
        assert all({"driver_id", "name", "count", "avg"} <= set(x) for x in d["drivers"])
        assert len(d["recent"]) <= 30 and "comment" in d["recent"][0] and "shop" in d["recent"][0]

    def test_merchant_forbidden(self, bakery):
        r = requests.get(f"{BASE_URL}/api/admin/ratings", headers=bakery)
        assert r.status_code == 403

    def test_driver_forbidden(self, driver):
        assert requests.get(f"{BASE_URL}/api/admin/ratings", headers=driver).status_code == 403

    def test_unauth(self):
        assert requests.get(f"{BASE_URL}/api/admin/ratings", headers=NOAUTH).status_code == 401


# ---- rating with comment → visible in all views
class TestRateWithComment:
    token = None

    def test_unlock(self):
        r = requests.post(f"{BASE_URL}/api/track/{STATE['code']}/verify", json={"last4": STATE["last4"]})
        assert r.status_code == 200, r.text
        TestRateWithComment.token = r.json()["token"]

    def test_invalid_stars(self):
        r = requests.post(f"{BASE_URL}/api/stops/{STATE['id']}/rating", json={"stars": 6, "comment": "x"},
                          headers={"X-Order-Token": self.token, **NOAUTH})
        assert r.status_code == 400

    def test_rate(self):
        r = requests.post(f"{BASE_URL}/api/stops/{STATE['id']}/rating", json={"stars": 4, "comment": COMMENT_RAW, "tags": ["on time"]},
                          headers={"X-Order-Token": self.token, **NOAUTH})
        assert r.status_code == 200, r.text
        assert r.json()["rating"] == 4

    def test_double_rate_rejected(self):
        r = requests.post(f"{BASE_URL}/api/stops/{STATE['id']}/rating", json={"stars": 1},
                          headers={"X-Order-Token": self.token, **NOAUTH})
        assert r.status_code == 400

    def test_in_merchant_view(self, bakery):
        d = requests.get(f"{BASE_URL}/api/merchants/mer-1/ratings", headers=bakery).json()
        row = next(x for x in d["recent"] if x["stop_id"] == STATE["id"])
        exp = COMMENT_RAW.strip()[:300]
        assert row["comment"] == exp and len(row["comment"]) == 300
        assert row["stars"] == 4 and row["tags"] == ["ON TIME"] and row["driver"] == "Marcus Reid"
        assert any(x["driver_id"] == "drv-1" for x in d["drivers"])
        assert d["count"] >= 1 and d["stars"]["4"] >= 1

    def test_not_in_other_store(self, admin):
        d = requests.get(f"{BASE_URL}/api/merchants/mer-2/ratings", headers=admin).json()
        assert all(x["stop_id"] != STATE["id"] for x in d["recent"])

    def test_in_admin_view(self, admin):
        d = requests.get(f"{BASE_URL}/api/admin/ratings", headers=admin).json()
        row = next(x for x in d["recent"] if x["stop_id"] == STATE["id"])
        assert row["comment"].startswith("TEST_ Great driver") and row["shop"] == "Mission Bakery Co."

    def test_in_driver_feedback(self, driver):
        r = requests.get(f"{BASE_URL}/api/drivers/drv-1/feedback", headers=driver)
        assert r.status_code == 200
        assert any((x.get("comment") or "").startswith("TEST_ Great driver") for x in r.json()["recent"])

    def test_driver_feedback_other_driver_forbidden(self, driver):
        assert requests.get(f"{BASE_URL}/api/drivers/drv-2/feedback", headers=driver).status_code == 403


# ---- trends + screenshots
class TestMisc:
    def test_trends(self, admin):
        r = requests.get(f"{BASE_URL}/api/alerts/trends?days=7", headers=admin)
        assert r.status_code == 200, r.text
        d = r.json()
        assert {"by_driver", "by_zone", "daily", "total_ratings"} <= set(d) and len(d["daily"]) == 7

    def test_screenshots_zip(self):
        r = requests.get(f"{BASE_URL}/api/store-screenshots.zip", headers=NOAUTH)
        assert r.status_code == 200 and r.content[:2] == b"PK"
