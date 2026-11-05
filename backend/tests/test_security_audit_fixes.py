# Security audit fixes: SEC-001 demo secrets blocked on live host, SEC-002 public_base host poisoning,
# SEC-003 file download ownership, coaching read auth, order token 30-day expiry.
import os
import io
import time
import jwt as pyjwt
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
LOCAL = "http://localhost:8001"
LIVE = {"Host": "dashlocal.app", "X-No-Auto-Auth": "1"}
NA = {"X-No-Auto-Auth": "1"}
PREVIEW_BASE = "https://dashlocal.preview.emergentagent.com"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
ADMIN = (os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])


def public_base():
    return (db.settings.find_one({"key": "public_base"}) or {}).get("value")


def restore_base():
    db.settings.update_one({"key": "public_base"}, {"$set": {"value": PREVIEW_BASE}}, upsert=True)


def clear_locks():
    db.login_attempts.delete_many({})


def login(email, pw, base=BASE_URL, headers=None):
    return requests.post(f"{base}/api/auth/login", json={"email": email, "password": pw}, headers=headers or NA)


def dlogin(phone, pin, base=BASE_URL, headers=None):
    return requests.post(f"{base}/api/auth/driver-login", json={"phone": phone, "pin": pin}, headers=headers or NA)


def tok(r):
    assert r.status_code == 200, r.text
    return r.json()["token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "X-No-Auto-Auth": "1"}


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    clear_locks()
    yield
    restore_base()
    clear_locks()


# ---------- SEC-001 demo secrets
class TestDemoSecrets:
    def test_merchant_demo_works_on_preview(self):
        r = login("bakery@dashlocal.app", "Bakery2026")
        assert r.status_code == 200 and r.json()["user"]["merchant_id"] == "mer-1"

    def test_merchant_demo_blocked_on_live(self):
        r = login("bakery@dashlocal.app", "Bakery2026", LOCAL, LIVE)
        assert r.status_code == 401 and r.json()["detail"] == "Wrong email or password"
        r2 = login("books@dashlocal.app", "Books2026", LOCAL, LIVE)
        assert r2.status_code == 401

    def test_admin_works_on_live(self):
        r = login(*ADMIN, base=LOCAL, headers=LIVE)
        assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
        restore_base()

    def test_driver_demo_preview_and_live(self):
        assert dlogin("+15195550101", "482913").status_code == 200
        r = dlogin("+15195550101", "482913", LOCAL, LIVE)
        assert r.status_code == 401 and r.json()["detail"] == "Wrong phone number or PIN"
        # plain localhost is NOT treated as preview (live health checks/proxies may use it)
        assert dlogin("+15195550101", "482913", LOCAL).status_code == 401

    def test_changed_pin_works_on_live_then_restore(self):
        at = tok(login(*ADMIN))
        r = requests.put(f"{BASE_URL}/api/admin/drivers/drv-3/pin", json={"pin": "583027"}, headers=H(at))
        assert r.status_code == 200, r.text
        try:
            r = dlogin("+15195550103", "583027", LOCAL, LIVE)
            assert r.status_code == 200 and r.json()["user"]["driver_id"] == "drv-3"
            assert dlogin("+15195550103", "264871").status_code == 401  # old PIN dead
        finally:
            r = requests.put(f"{BASE_URL}/api/admin/drivers/drv-3/pin", json={"pin": "264871"}, headers=H(at))
            assert r.status_code == 200
            restore_base()
            clear_locks()
        assert dlogin("+15195550103", "264871").status_code == 200


# ---------- SEC-002 public_base
class TestPublicBase:
    def test_forged_xfh_on_preview_ignored(self):
        restore_base()
        r = login(*ADMIN, headers={**NA, "X-Forwarded-Host": "evil.com"})
        assert r.status_code == 200
        assert public_base() == PREVIEW_BASE

    def test_forged_host_local_ignored(self):
        restore_base()
        r = login(*ADMIN, base=LOCAL, headers={**NA, "Host": "evil.com"})
        assert r.status_code == 200
        assert public_base() == PREVIEW_BASE
        r = dlogin("+15195550101", "482913", LOCAL, {**NA, "X-Forwarded-Host": "evil.com:443"})
        assert public_base() == PREVIEW_BASE

    def test_http_scheme_and_lookalike_rejected(self):
        restore_base()
        login(*ADMIN, base=LOCAL, headers={**NA, "Host": "dashlocal.preview.emergentagent.com.evil.com"})
        assert public_base() == PREVIEW_BASE
        login(*ADMIN, base=LOCAL, headers={**NA, "X-Forwarded-Host": "evil.com, dashlocal.preview.emergentagent.com"})
        assert public_base() == PREVIEW_BASE

    def test_normal_preview_login_keeps_preview(self):
        db.settings.update_one({"key": "public_base"}, {"$set": {"value": "https://dashlocal.app"}})
        login(*ADMIN)
        assert public_base() == PREVIEW_BASE


# ---------- SEC-003 file ownership + delivery
JPEG = b"\xff\xd8\xff\xe0" + b"TESTPHOTO" * 50 + b"\xff\xd9"


@pytest.fixture(scope="class")
def toks():
    clear_locks()
    return {"admin": tok(login(*ADMIN)), "mer1": tok(login("bakery@dashlocal.app", "Bakery2026")),
            "mer2": tok(login("books@dashlocal.app", "Books2026")),
            "drv1": tok(dlogin("+15195550101", "482913")), "drv2": tok(dlogin("+15195550102", "730516")),
            "drv3": tok(dlogin("+15195550103", "264871"))}


def fget(path, t=None, bearer=None):
    h = dict(NA)
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    return requests.get(f"{BASE_URL}/api/files/{path}", params={"t": t} if t else None, headers=h)


def upload(t):
    r = requests.post(f"{BASE_URL}/api/upload", files={"file": ("pod.jpg", io.BytesIO(JPEG), "image/jpeg")}, headers=H(t))
    assert r.status_code == 200, r.text
    return r.json()["path"]


class TestFileAccess:
    state = {}

    def _merchant_stop(self):
        # pending mer-1 stop on drv-2's active route
        # pending mer-1 stop on drv-2's active route; else tag a pending stop on drv-2/drv-1's route as mer-1 (test data)
        for did in ("drv-2", "drv-1"):
            r = db.routes.find_one({"driver_id": did, "status": "active"})
            s = db.stops.find_one({"route_id": r["id"], "merchant_id": "mer-1", "status": {"$in": ["pending", "arrived"]}}) if r else None
            if s:
                return s, did
        for did in ("drv-2", "drv-1"):
            r = db.routes.find_one({"driver_id": did, "status": "active"})
            s = db.stops.find_one({"route_id": r["id"], "status": "pending"}, sort=[("sequence", -1)]) if r else None
            if s:
                db.stops.update_one({"id": s["id"]}, {"$set": {"merchant_id": "mer-1"}})
                return {**s, "merchant_id": "mer-1"}, did
        pytest.skip("no pending stop on an active route")

    def test_upload_records_uploader_and_preview_view(self, toks):
        s, did = self._merchant_stop()
        me, other = ("drv2", "drv1") if did == "drv-2" else ("drv1", "drv2")
        TestFileAccess.state.update(stop=s, me=me, other=other)
        p = upload(toks[me])
        rec = db.files.find_one({"storage_path": p})
        u = db.users.find_one({"role": "driver", "driver_id": did})
        assert rec["uploaded_by"] == u["id"]
        assert fget(p, t=toks[me]).status_code == 200
        assert fget(p, t=toks[me]).content == JPEG
        assert fget(p, bearer=toks[me]).status_code == 200
        assert fget(p, t=toks[other]).status_code == 401
        assert fget(p, t=toks["mer1"]).status_code == 401  # not attached to a stop yet
        assert fget(p).status_code == 401
        assert fget(p, t="garbage").status_code == 401
        TestFileAccess.state["p"] = p

    def test_deliver_with_photo_and_access_matrix(self, toks):
        st = TestFileAccess.state
        s, p, me, other = st["stop"], st["p"], st["me"], st["other"]
        r = requests.post(f"{BASE_URL}/api/stops/{s['id']}/deliver", json={"photo_path": p}, headers=H(toks[me]))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "delivered"
        assert fget(p, t=toks["admin"]).status_code == 200
        assert fget(p, t=toks["mer1"]).status_code == 200
        assert fget(p, t=toks["mer2"]).status_code == 401
        assert fget(p, t=toks[other]).status_code == 401
        assert fget(p, t=toks["drv3"]).status_code == 401
        assert fget(p, t=toks[me]).status_code == 200

    def test_route_driver_sees_admin_uploaded_photo(self, toks):
        r = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
        s = db.stops.find_one({"route_id": r["id"], "status": "pending", "merchant_id": None}) if r else None
        if not s:
            pytest.skip("no pending drv-1 stop")
        p = upload(toks["admin"])
        assert fget(p, t=toks["drv1"]).status_code == 401
        rr = requests.post(f"{BASE_URL}/api/stops/{s['id']}/deliver", json={"photo_path": p}, headers=H(toks["admin"]))
        assert rr.status_code == 200, rr.text
        assert fget(p, t=toks["drv1"]).status_code == 200  # own route
        assert fget(p, t=toks["drv2"]).status_code == 401
        assert fget(p, t=toks["mer1"]).status_code == 401  # stop has no merchant

    def test_signed_file_link_and_order_token_exp(self, toks):
        s = TestFileAccess.state.get("stop")
        if not s:
            pytest.skip("no delivered stop")
        last4 = "".join(c for c in s["customer_phone"] if c.isdigit())[-4:]
        r = requests.post(f"{BASE_URL}/api/track/{s['tracking_code']}/verify", json={"last4": last4}, headers=NA)
        if r.status_code == 422:
            r = requests.post(f"{BASE_URL}/api/track/{s['tracking_code']}/verify", json={"digits": last4}, headers=NA)
        assert r.status_code == 200, r.text
        d = r.json()
        claims = pyjwt.decode(d["token"], options={"verify_signature": False})
        days = (claims["exp"] - claims["iat"]) / 86400
        assert claims["purpose"] == "order" and abs(days - 30) < 0.01, days
        url = d.get("pod_photo_url")
        assert url, d.keys()
        g = requests.get(f"{BASE_URL}{url}", headers=NA)
        assert g.status_code == 200 and g.content == JPEG
        # file token for another path doesn't open this file
        other = upload(toks["admin"])
        t_other = url.split("t=", 1)[1]
        assert fget(other, t=t_other).status_code == 401
        # order token is not a session token
        assert fget(TestFileAccess.state["p"], t=d["token"]).status_code == 401


# ---------- coaching read auth
class TestCoachingRead:
    def test_coaching_read_auth(self, toks):
        r = requests.post(f"{BASE_URL}/api/drivers/drv-1/coaching", json={"note": "TEST_ note from audit", "reason": "test"}, headers=H(toks["admin"]))
        assert r.status_code == 200, r.text
        nid = r.json()["id"]
        try:
            assert requests.post(f"{BASE_URL}/api/coaching/{nid}/read", headers=NA).status_code == 401
            assert requests.post(f"{BASE_URL}/api/coaching/{nid}/read", headers=H(toks["drv2"])).status_code == 403
            assert requests.post(f"{BASE_URL}/api/coaching/{nid}/read", headers=H(toks["mer1"])).status_code == 403
            assert db.coaching_notes.find_one({"id": nid})["read_at"] is None
            r = requests.post(f"{BASE_URL}/api/coaching/{nid}/read", headers=H(toks["drv1"]))
            assert r.status_code == 200 and r.json()["ok"] is True
            assert db.coaching_notes.find_one({"id": nid})["read_at"]
            assert requests.post(f"{BASE_URL}/api/coaching/{nid}/read", headers=H(toks["admin"])).status_code == 200
            assert requests.post(f"{BASE_URL}/api/coaching/nope-{time.time()}/read", headers=H(toks["admin"])).status_code == 404
        finally:
            db.coaching_notes.delete_one({"id": nid})
