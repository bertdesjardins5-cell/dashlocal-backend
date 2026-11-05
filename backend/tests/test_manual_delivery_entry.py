# Merchant Portal: enter one delivery by hand (POST /api/merchants/{id}/orders) - light regression
import os
import pytest
import requests

BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
NO = {"X-No-Auto-Auth": "1"}


def login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, headers=NO, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"login failed {email}: {r.status_code} {r.text[:120]}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NO}


@pytest.fixture(scope="module")
def books():
    return login("books@dashlocal.app", "Books2026")


@pytest.fixture(scope="module")
def admin():
    return login("admin@dashlocal.app", "DashLocalAdmin2026")


BASE = {"customer_name": "TEST API Customer", "phone": "519 555 0179", "address": "105 Frederick St"}


def post(h, mid, body):
    return requests.post(f"{BASE_URL}/api/merchants/{mid}/orders", json=body, headers=h, timeout=40)


class TestManualEntry:
    def test_consent_required(self, books):
        r = post(books, "mer-2", {**BASE, "run": "afternoon"})
        assert r.status_code == 400 and "consent" in r.json()["detail"].lower()

    def test_pharmacy_run_required(self, books):
        r = post(books, "mer-2", {**BASE, "consent": True})
        assert r.status_code == 400 and "run" in r.json()["detail"].lower()

    def test_bad_phone(self, books):
        r = post(books, "mer-2", {**BASE, "phone": "12", "consent": True, "run": "afternoon"})
        assert r.status_code == 400 and "phone" in r.json()["detail"].lower()

    def test_cross_store_forbidden(self, books):
        r = post(books, "mer-1", {**BASE, "consent": True})
        assert r.status_code == 403

    def test_window_half_given(self, admin):
        r = post(admin, "mer-1", {**BASE, "consent": True, "window_start": "14:00"})
        assert r.status_code == 400 and "Window from" in r.json()["detail"]

    def test_unauth(self):
        r = post(NO, "mer-2", {**BASE, "consent": True, "run": "afternoon"})
        assert r.status_code == 401

    def test_create_and_persist(self, books):
        r = post(books, "mer-2", {**BASE, "consent": True, "run": "evening", "amount_due": "12.50"})
        assert r.status_code == 200, r.text
        st = r.json()
        assert st["tracking_code"].startswith("MC") and st["customer_name"] == "TEST API Customer"
        assert st.get("batch_run") == "evening"
        assert "_id" not in st
        d = requests.get(f"{BASE_URL}/api/merchants/mer-2", headers=books, timeout=20).json()
        assert any(o["tracking_code"] == st["tracking_code"] for o in d["orders"])
