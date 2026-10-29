# Sign-up "Type of business": business_type validation, pharmacy -> PIPEDA/PHIPA consent, others -> Terms of Service consent
import os
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
NOAUTH = {"X-No-Auto-Auth": "1"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _clean():
    db.signup_requests.delete_many({"store_name": {"$regex": "^TEST_"}})


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    _clean()
    yield
    _clean()


@pytest.fixture(scope="module")
def admin():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"admin login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


def _signup(**kw):
    body = {"store_name": "TEST_BT " + uuid.uuid4().hex[:5], "contact_name": "TEST Contact",
            "email": f"test+bt{uuid.uuid4().hex[:8]}@resend.dev", "consent_timestamp": "2026-01-10T10:00:00Z", **kw}
    return body, requests.post(f"{BASE_URL}/api/signup-requests", json=body, headers=NOAUTH)


class TestValidation:
    def test_invalid_type_400(self):
        _, r = _signup(business_type="bakery", terms_consent=True, privacy_consent=True)
        assert r.status_code == 400 and "type of business" in r.json()["detail"].lower(), r.text

    def test_empty_type_400(self):
        _, r = _signup(business_type="", terms_consent=True)
        assert r.status_code == 400, r.text

    def test_pharmacy_without_privacy_400(self):
        _, r = _signup(business_type="pharmacy", terms_consent=True)
        assert r.status_code == 400 and "PIPEDA" in r.json()["detail"], r.text

    def test_law_office_without_terms_400(self):
        _, r = _signup(business_type="law_office")
        assert r.status_code == 400 and "Terms of Service" in r.json()["detail"], r.text

    def test_store_privacy_only_not_enough_400(self):
        _, r = _signup(business_type="store", privacy_consent=True)
        assert r.status_code == 400 and "Terms of Service" in r.json()["detail"], r.text

    def test_nothing_was_stored(self):
        assert db.signup_requests.count_documents({"store_name": {"$regex": "^TEST_BT"}}) == 0


class TestStoredDocs:
    @pytest.mark.parametrize("bt", ["store", "law_office", "flower_shop", "other"])
    def test_non_pharmacy_terms(self, bt, admin):
        body, r = _signup(business_type=bt, terms_consent=True)
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text
        d = db.signup_requests.find_one({"email": body["email"]})
        assert d["business_type"] == bt and d["terms_consent"] is True and d["terms_version"] == "2026-10"
        assert d.get("terms_consent_at") and d["consent_timestamp"] == "2026-01-10T10:00:00Z"
        assert "phipa_consent_given" not in d and "privacy_consent" not in d
        lst = requests.get(f"{BASE_URL}/api/admin/signup-requests", headers=admin).json()
        mine = [x for x in lst if x["email"] == body["email"]]
        assert len(mine) == 1 and mine[0]["business_type"] == bt and mine[0]["terms_version"] == "2026-10" and "_id" not in mine[0]

    def test_pharmacy_privacy(self, admin):
        _clean()  # stay under 5/IP/hour
        body, r = _signup(business_type="pharmacy", privacy_consent=True)
        assert r.status_code == 200, r.text
        d = db.signup_requests.find_one({"email": body["email"]})
        assert d["business_type"] == "pharmacy" and d["phipa_consent_given"] is True and d["privacy_consent"] is True
        assert d.get("consent_version") and "terms_consent" not in d
        lst = requests.get(f"{BASE_URL}/api/admin/signup-requests", headers=admin).json()
        mine = [x for x in lst if x["email"] == body["email"]]
        assert mine and mine[0]["business_type"] == "pharmacy" and mine[0]["phipa_consent_given"] is True
