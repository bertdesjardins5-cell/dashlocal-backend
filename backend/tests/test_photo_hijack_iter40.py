# Iter40: proof-photo hijack fix on POST /api/stops/{id}/deliver - drivers may only attach a photo they uploaded
# that is not already used on another stop; admin is exempt. Needs a fresh POST /api/admin/reset (drv-1 active route).
import io
import os
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
ADMIN = (os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
NA = {"X-No-Auto-Auth": "1"}
JPEG = b"\xff\xd8\xff\xe0" + b"HIJACKTEST" * 40 + b"\xff\xd9"
MSG = "Use a photo you just took for this delivery"
SIG = "M10 10 L20 20 L30 15"


def H(t):
    return {"Authorization": f"Bearer {t}", "X-No-Auto-Auth": "1"}


def _tok(r):
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def toks():
    db.login_attempts.delete_many({})
    lg = lambda e, p: requests.post(f"{BASE_URL}/api/auth/login", json={"email": e, "password": p}, headers=NA)
    dl = lambda ph, pin: requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": ph, "pin": pin}, headers=NA)
    return {"admin": _tok(lg(*ADMIN)), "drv1": _tok(dl("+15195550101", "482913")), "drv2": _tok(dl("+15195550102", "730516"))}


@pytest.fixture(scope="module")
def stops():
    r = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    ss = list(db.stops.find({"route_id": r["id"], "status": "pending", "merchant_id": None, "batch_run": {"$in": [None, False]}},
                            {"_id": 0, "id": 1}).sort("sequence", -1).limit(4)) if r else []
    if len(ss) < 4:
        pytest.skip("need 4 pending non-merchant drv-1 stops (run POST /api/admin/reset)")
    return [s["id"] for s in ss]


def upload(t):
    r = requests.post(f"{BASE_URL}/api/upload", files={"file": ("pod.jpg", io.BytesIO(JPEG), "image/jpeg")}, headers=H(t))
    assert r.status_code == 200, r.text
    return r.json()["path"]


def deliver(sid, t, **body):
    return requests.post(f"{BASE_URL}/api/stops/{sid}/deliver", json=body, headers=H(t))


def status(sid):
    return db.stops.find_one({"id": sid})["status"]


class TestPhotoHijack:
    st = {}

    def test_other_driver_photo_rejected(self, toks, stops):
        p = upload(toks["drv2"])
        r = deliver(stops[0], toks["drv1"], photo_path=p)
        assert r.status_code == 400 and r.json()["detail"] == MSG
        assert status(stops[0]) == "pending"

    def test_admin_uploaded_photo_rejected_for_driver(self, toks, stops):
        p = upload(toks["admin"])
        r = deliver(stops[0], toks["drv1"], photo_path=p, signature=SIG)
        assert r.status_code == 400 and r.json()["detail"] == MSG
        assert status(stops[0]) == "pending"

    def test_unknown_path_rejected(self, toks, stops):
        r = deliver(stops[0], toks["drv1"], photo_path="dashlocal/uploads/pod/does-not-exist.jpg")
        assert r.status_code == 400 and r.json()["detail"] == MSG

    def test_own_photo_delivers(self, toks, stops):
        p = upload(toks["drv1"])
        r = deliver(stops[0], toks["drv1"], photo_path=p)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "delivered"
        assert db.stops.find_one({"id": stops[0]})["pod_photo_path"] == p
        TestPhotoHijack.st["p"] = p

    def test_reuse_used_path_rejected(self, toks, stops):
        r = deliver(stops[1], toks["drv1"], photo_path=TestPhotoHijack.st["p"])
        assert r.status_code == 400 and r.json()["detail"] == MSG
        assert status(stops[1]) == "pending"

    def test_signature_only_still_ok(self, toks, stops):
        r = deliver(stops[1], toks["drv1"], signature=SIG)
        assert r.status_code == 200, r.text
        assert db.stops.find_one({"id": stops[1]})["pod_photo_path"] is None

    def test_admin_exempt_foreign_and_reused(self, toks, stops):
        r = deliver(stops[2], toks["admin"], photo_path=TestPhotoHijack.st["p"])
        assert r.status_code == 200, r.text
        p2 = upload(toks["drv2"])
        r = deliver(stops[3], toks["admin"], photo_path=p2)
        assert r.status_code == 200, r.text
