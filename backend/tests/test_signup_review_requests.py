# Merchant sign-up requests (public + admin approve/decline), set-password links, and shop review requests
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
NOAUTH = {"X-No-Auto-Auth": "1"}
SECRET = os.environ["JWT_SECRET"]
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
STATE = {}


def mint(sub, purpose, **extra):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": sub, "role": "merchant", "purpose": purpose, "iat": now, "exp": now + timedelta(hours=1), **extra}, SECRET, algorithm="HS256")


def _login(body):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=body, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text}")
    return {"Authorization": f"Bearer {r.json()['token']}", **NOAUTH}


@pytest.fixture(scope="module")
def admin():
    return _login({"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"})


@pytest.fixture(scope="module")
def merchant():
    return _login({"email": "bakery@dashlocal.app", "password": "Bakery2026"})


def _email():
    return f"test+{uuid.uuid4().hex[:10]}@resend.dev"


def _signup(email=None, **kw):
    body = {"store_name": "TEST_Shop " + uuid.uuid4().hex[:5], "contact_name": "TEST Contact", "email": email or _email(), "privacy_consent": True, "business_type": "pharmacy", "consent_timestamp": "2026-01-01T00:00:00Z", **kw}
    return body, requests.post(f"{BASE_URL}/api/signup-requests", json=body, headers=NOAUTH)


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    db.signup_requests.delete_many({"store_name": {"$regex": "^TEST_"}})
    yield
    for sr in db.signup_requests.find({"store_name": {"$regex": "^TEST_"}}):
        if sr.get("merchant_id"):
            db.merchants.delete_one({"id": sr["merchant_id"]})
            db.users.delete_many({"merchant_id": sr["merchant_id"]})
    db.signup_requests.delete_many({"store_name": {"$regex": "^TEST_"}})
    db.review_requests.delete_many({})
    db.testimonials.delete_many({})
    db.login_attempts.delete_many({"key": {"$regex": "^(drv|trk)"}})


class TestSignupPublic:
    def test_missing_store_400(self):
        _, r = _signup(store_name="  ")
        assert r.status_code == 400, r.text

    def test_missing_contact_400(self):
        _, r = _signup(contact_name="")
        assert r.status_code == 400, r.text

    def test_bad_email_400(self):
        _, r = _signup(email="not-an-email")
        assert r.status_code == 400, r.text

    def test_create_and_list(self, admin):
        body, r = _signup(phone="(519) 555-0199", address="200 King St W Kitchener", message="  hello   there ")
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text
        lst = requests.get(f"{BASE_URL}/api/admin/signup-requests", headers=admin).json()
        mine = [x for x in lst if x["email"] == body["email"]]
        assert len(mine) == 1 and mine[0]["status"] == "pending" and mine[0]["message"] == "hello there"
        assert "ip" not in mine[0] and "_id" not in mine[0]
        STATE["pending"] = mine[0]

    def test_duplicate_pending_no_new_doc(self):
        email = STATE["pending"]["email"]
        _, r = _signup(email=email.upper())
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert db.signup_requests.count_documents({"email": email}) == 1

    def test_existing_account_no_doc(self):
        _, r = _signup(email="bakery@dashlocal.app")
        assert r.status_code == 200
        assert db.signup_requests.count_documents({"email": "bakery@dashlocal.app"}) == 0

    def test_rate_limit_429(self):
        codes = []
        for _ in range(6):
            codes.append(_signup()[1].status_code)
        assert 429 in codes, codes
        assert codes[-1] == 429


class TestSignupAdmin:
    def test_list_no_auth_401(self):
        assert requests.get(f"{BASE_URL}/api/admin/signup-requests", headers=NOAUTH).status_code == 401

    def test_list_merchant_403(self, merchant):
        assert requests.get(f"{BASE_URL}/api/admin/signup-requests", headers=merchant).status_code == 403

    def test_approve_merchant_403(self, merchant):
        assert requests.post(f"{BASE_URL}/api/admin/signup-requests/{STATE['pending']['id']}/approve", headers=merchant).status_code == 403

    def test_approve_unknown_404(self, admin):
        assert requests.post(f"{BASE_URL}/api/admin/signup-requests/nope/approve", headers=admin).status_code == 404

    def test_approve_creates_merchant_user(self, admin):
        sr = STATE["pending"]
        r = requests.post(f"{BASE_URL}/api/admin/signup-requests/{sr['id']}/approve", headers=admin)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] and d["merchant_id"].startswith("mer-")
        print("email_status:", d["email_status"])
        assert d["email_status"] == "sent", d
        m = db.merchants.find_one({"id": d["merchant_id"]})
        assert m and m["name"] == sr["store_name"]
        u = db.users.find_one({"email": sr["email"]})
        assert u["role"] == "merchant" and u["merchant_id"] == d["merchant_id"] and u.get("pw_nonce")
        assert db.signup_requests.find_one({"id": sr["id"]})["status"] == "approved"
        STATE["user"] = u
        STATE["merchant_id"] = d["merchant_id"]

    def test_approve_again_400(self, admin):
        r = requests.post(f"{BASE_URL}/api/admin/signup-requests/{STATE['pending']['id']}/approve", headers=admin)
        assert r.status_code == 400, r.text

    def test_approve_existing_email_409(self, admin):
        sid = str(uuid.uuid4())
        db.signup_requests.insert_one({"id": sid, "store_name": "TEST_Dup", "contact_name": "TEST", "email": "bakery@dashlocal.app",
                                       "status": "pending", "ip": "x", "created_at": "2000-01-01T00:00:00+00:00"})
        r = requests.post(f"{BASE_URL}/api/admin/signup-requests/{sid}/approve", headers=admin)
        assert r.status_code == 409, r.text

    def test_decline(self, admin):
        sid = str(uuid.uuid4())
        db.signup_requests.insert_one({"id": sid, "store_name": "TEST_Decline", "contact_name": "TEST", "email": _email(),
                                       "status": "pending", "ip": "x", "created_at": "2000-01-01T00:00:00+00:00"})
        r = requests.post(f"{BASE_URL}/api/admin/signup-requests/{sid}/decline", headers=admin)
        assert r.status_code == 200
        assert db.signup_requests.find_one({"id": sid})["status"] == "declined"
        assert requests.post(f"{BASE_URL}/api/admin/signup-requests/{sid}/decline", headers=admin).status_code == 404
        assert requests.post(f"{BASE_URL}/api/admin/signup-requests/{sid}/approve", headers=admin).status_code == 400


class TestSetPassword:
    def test_bad_token(self):
        r = requests.get(f"{BASE_URL}/api/auth/set-password/garbage", headers=NOAUTH)
        assert r.status_code in (400, 401)

    def test_wrong_nonce_400(self):
        tok = mint(STATE["user"]["id"], "setpw", n="wrong")
        assert requests.get(f"{BASE_URL}/api/auth/set-password/{tok}", headers=NOAUTH).status_code == 400

    def test_review_purpose_token_rejected(self):
        tok = mint(STATE["user"]["id"], "review", n=STATE["user"]["pw_nonce"])
        assert requests.get(f"{BASE_URL}/api/auth/set-password/{tok}", headers=NOAUTH).status_code == 400  # link_payload: friendly 400 since iter 25

    def test_info(self):
        u = STATE["user"]
        STATE["tok"] = mint(u["id"], "setpw", n=u["pw_nonce"])
        r = requests.get(f"{BASE_URL}/api/auth/set-password/{STATE['tok']}", headers=NOAUTH)
        assert r.status_code == 200, r.text
        assert r.json() == {"email": u["email"], "store_name": STATE["pending"]["store_name"]}

    def test_short_password_400(self):
        r = requests.post(f"{BASE_URL}/api/auth/set-password", json={"token": STATE["tok"], "password": "short"}, headers=NOAUTH)
        assert r.status_code == 400

    def test_set_then_login(self):
        r = requests.post(f"{BASE_URL}/api/auth/set-password", json={"token": STATE["tok"], "password": "TestPass2026"}, headers=NOAUTH)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["token"] and d["user"]["merchant_id"] == STATE["merchant_id"]
        assert "pw_nonce" not in d["user"] and "password_hash" not in d["user"]
        me = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {d['token']}", **NOAUTH})
        assert me.status_code == 200
        lg = requests.post(f"{BASE_URL}/api/auth/login", json={"email": STATE["user"]["email"], "password": "TestPass2026"}, headers=NOAUTH)
        assert lg.status_code == 200, lg.text
        assert "pw_nonce" not in lg.json()["user"]

    def test_reuse_token_400(self):
        r = requests.post(f"{BASE_URL}/api/auth/set-password", json={"token": STATE["tok"], "password": "Another2026"}, headers=NOAUTH)
        assert r.status_code == 400
        assert requests.get(f"{BASE_URL}/api/auth/set-password/{STATE['tok']}", headers=NOAUTH).status_code == 400


class TestReviewRequests:
    def test_no_auth_401(self):
        assert requests.post(f"{BASE_URL}/api/admin/review-requests", json={"merchant_id": "mer-1"}, headers=NOAUTH).status_code == 401

    def test_merchant_403(self, merchant):
        assert requests.get(f"{BASE_URL}/api/admin/review-requests", headers=merchant).status_code == 403

    def test_unknown_merchant_404(self, admin):
        assert requests.post(f"{BASE_URL}/api/admin/review-requests", json={"merchant_id": "mer-nope"}, headers=admin).status_code == 404

    def test_send_and_list(self, admin):
        r = requests.post(f"{BASE_URL}/api/admin/review-requests", json={"merchant_id": "mer-1"}, headers=admin)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "sent" and d["merchant_id"] == "mer-1" and d["emails"] == ["bakery@dashlocal.app"] and "_id" not in d
        lst = requests.get(f"{BASE_URL}/api/admin/review-requests", headers=admin).json()
        assert any(x["id"] == d["id"] for x in lst)
        STATE["rr"] = d

    def test_get_public(self):
        STATE["rtok"] = mint(STATE["rr"]["id"], "review")
        r = requests.get(f"{BASE_URL}/api/review-requests/{STATE['rtok']}", headers=NOAUTH)
        assert r.status_code == 200 and r.json() == {"shop": STATE["rr"]["shop"], "status": "sent"}

    def test_bad_tokens(self):
        assert requests.get(f"{BASE_URL}/api/review-requests/garbage", headers=NOAUTH).status_code == 400
        assert requests.get(f"{BASE_URL}/api/review-requests/{mint('nope', 'review')}", headers=NOAUTH).status_code == 404
        assert requests.get(f"{BASE_URL}/api/review-requests/{mint(STATE['rr']['id'], 'setpw')}", headers=NOAUTH).status_code == 400

    def test_review_token_not_session(self):
        r = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {STATE['rtok']}", **NOAUTH})
        assert r.status_code == 401

    def test_empty_quote_400(self):
        r = requests.post(f"{BASE_URL}/api/review-requests/{STATE['rtok']}", json={"quote": "   "}, headers=NOAUTH)
        assert r.status_code == 400
        assert db.review_requests.find_one({"id": STATE["rr"]["id"]})["status"] == "sent"

    def test_submit_hidden_then_publish(self, admin):
        r = requests.post(f"{BASE_URL}/api/review-requests/{STATE['rtok']}", json={"quote": "TEST great  service", "person": "Ana, owner"}, headers=NOAUTH)
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text
        t = db.testimonials.find_one({"merchant_id": "mer-1", "from_shop": True})
        assert t and t["published"] is False and t["quote"] == "TEST great service" and t["shop"] == STATE["rr"]["shop"]
        assert all(x["id"] != t["id"] for x in requests.get(f"{BASE_URL}/api/testimonials").json())
        g = requests.get(f"{BASE_URL}/api/review-requests/{STATE['rtok']}", headers=NOAUTH).json()
        assert g["status"] == "submitted"
        again = requests.post(f"{BASE_URL}/api/review-requests/{STATE['rtok']}", json={"quote": "again"}, headers=NOAUTH)
        assert again.status_code == 400
        assert db.testimonials.count_documents({"from_shop": True}) == 1
        pu = requests.put(f"{BASE_URL}/api/admin/testimonials/{t['id']}", json={"quote": t["quote"], "shop": t["shop"], "person": t["person"], "published": True}, headers=admin)
        assert pu.status_code == 200 and pu.json()["published"] is True and pu.json().get("from_shop") is True
        assert any(x["id"] == t["id"] for x in requests.get(f"{BASE_URL}/api/testimonials").json())
