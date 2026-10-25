# Security audit fixes: public tracking privacy, last-4 unlock, order-token gating, file links
import base64
import os
import re

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().strip('"')
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"
NOAUTH = {"X-No-Auto-Auth": "1"}
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

FORBIDDEN = {"customer_name", "customer_phone", "address", "notes", "lat", "lng", "pod_photo_path",
             "pod_signature", "apology_code", "share_token", "postal_code"}
# 1x1 JPEG
JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")


def last4(code):
    return re.sub(r"\D", "", mdb.stops.find_one({"tracking_code": code})["customer_phone"])[-4:]


def stop_id(code):
    return mdb.stops.find_one({"tracking_code": code})["id"]


def clean_trk():
    mdb.login_attempts.delete_many({"key": {"$regex": "^trk"}})


@pytest.fixture(scope="module")
def admin():
    r = requests.post(f"{API}/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}, headers=NOAUTH)
    assert r.status_code == 200
    tok = r.json()["token"]
    assert requests.post(f"{API}/admin/reset", headers={"Authorization": f"Bearer {tok}", **NOAUTH}).status_code == 200
    clean_trk()
    yield tok
    clean_trk()


@pytest.fixture(scope="module")
def drv1(admin):
    r = requests.post(f"{API}/auth/driver-login", json={"phone": "+14155550101", "pin": "482913"}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def drv2(admin):
    r = requests.post(f"{API}/auth/driver-login", json={"phone": "+14155550102", "pin": "730516"}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def verify(code, digits):
    return requests.post(f"{API}/track/{code}/verify", json={"last4": digits}, headers=NOAUTH)


# ---- public tracking payload
class TestPublicTracking:
    def test_track_payload_has_no_pii(self, admin):
        r = requests.get(f"{API}/track/MD1001", headers=NOAUTH)
        assert r.status_code == 200
        d = r.json()
        assert not (FORBIDDEN & set(d["stop"].keys())), d["stop"].keys()
        for k in ("id", "tracking_code", "status", "has_photo", "has_signature", "window_start", "rating"):
            assert k in d["stop"]
        assert d["path"] == []
        assert d["driver"]["name"] == "Marcus"

    def test_track_codes_no_names(self, admin):
        r = requests.get(f"{API}/track-codes", headers=NOAUTH)
        assert r.status_code == 200 and len(r.json()) > 0
        assert set(r.json()[0].keys()) == {"tracking_code", "status", "seq"}

    def test_ws_track_no_pii(self, admin):
        import asyncio, json, websockets

        async def go():
            async with websockets.connect(f"{BASE_URL.replace('https', 'wss')}/api/ws/track/MD1001") as ws:
                return json.loads(await asyncio.wait_for(ws.recv(), 10))
        d = asyncio.run(go())
        assert not (FORBIDDEN & set(d["stop"].keys()))
        assert d["path"] == []


# ---- last-4 verify + private
class TestVerify:
    def test_wrong_and_right_digits(self, admin):
        clean_trk()
        bad = "0000" if last4("MD1001") != "0000" else "1111"
        assert verify("MD1001", bad).status_code == 401
        r = verify("MD1001", last4("MD1001"))
        assert r.status_code == 200
        d = r.json()
        for k in ("token", "address", "lat", "lng", "pod_photo_url", "pod_signature", "apology_code"):
            assert k in d
        assert last4("MD1001") == "0100"

    def test_bad_format_rejected(self, admin):
        clean_trk()
        assert verify("MD1001", "01").status_code in (401, 422)
        assert verify("NOPE99", "0100").status_code == 401

    def test_lockout_429(self, admin):
        clean_trk()
        bad = "9999" if last4("MD1003") != "9999" else "8888"
        codes = [verify("MD1003", bad).status_code for _ in range(5)]
        assert codes == [401] * 5
        assert verify("MD1003", bad).status_code == 429
        assert verify("MD1003", last4("MD1003")).status_code == 429  # locked even with right digits
        clean_trk()

    def test_private_requires_token(self, admin):
        clean_trk()
        t1 = verify("MD1001", last4("MD1001")).json()["token"]
        t2 = verify("MD1002", last4("MD1002")).json()["token"]
        assert requests.get(f"{API}/track/MD1001/private", headers=NOAUTH).status_code == 401
        r = requests.get(f"{API}/track/MD1001/private", headers={"X-Order-Token": t1, **NOAUTH})
        assert r.status_code == 200 and "address" in r.json()
        assert requests.get(f"{API}/track/MD1001/private", headers={"X-Order-Token": t2, **NOAUTH}).status_code == 403
        # staff session JWT is not an order token
        assert requests.get(f"{API}/track/MD1001/private", headers={"X-Order-Token": admin, **NOAUTH}).status_code == 401


# ---- window / rating / share gating
class TestOrderAccess:
    def test_window_gating(self, admin, drv2):
        clean_trk()
        sid = stop_id("MD1001")
        t1 = verify("MD1001", last4("MD1001")).json()["token"]
        t2 = verify("MD1002", last4("MD1002")).json()["token"]
        body = {"date": None, "start": None, "end": None}
        assert requests.put(f"{API}/stops/{sid}/window", json=body, headers=NOAUTH).status_code == 401
        assert requests.put(f"{API}/stops/{sid}/window", json=body, headers={"X-Order-Token": t2, **NOAUTH}).status_code == 403
        assert requests.put(f"{API}/stops/{sid}/window", json=body, headers={"Authorization": f"Bearer {drv2}", **NOAUTH}).status_code == 403
        assert requests.put(f"{API}/stops/{sid}/window", json=body, headers={"Authorization": f"Bearer {admin}", **NOAUTH}).status_code == 200
        r = requests.put(f"{API}/stops/{sid}/window", json=body, headers={"X-Order-Token": t1, **NOAUTH})
        assert r.status_code == 200
        assert requests.put(f"{API}/stops/{sid}/window", json=body, headers={"X-Order-Token": "garbage", **NOAUTH}).status_code == 401

    def test_rating_share_no_auth_401(self, admin):
        sid = stop_id("MD1001")
        assert requests.post(f"{API}/stops/{sid}/rating", json={"stars": 5}, headers=NOAUTH).status_code == 401
        assert requests.post(f"{API}/stops/{sid}/share", headers=NOAUTH).status_code == 401


# ---- deliver with photo -> files gating, rating, share, proof page
class TestProofFiles:
    @pytest.fixture(scope="class")
    def delivered(self, admin, drv1):
        up = requests.post(f"{API}/upload", files={"file": ("p.jpg", JPEG, "image/jpeg")}, headers={"Authorization": f"Bearer {drv1}", **NOAUTH})
        if up.status_code != 200:
            pytest.skip(f"upload failed {up.status_code} {up.text}")
        path = up.json()["path"]
        sid = stop_id("MD1002")
        r = requests.post(f"{API}/stops/{sid}/deliver", json={"photo_path": path, "signature": "M10 10 L100 100"},
                          headers={"Authorization": f"Bearer {drv1}", **NOAUTH})
        assert r.status_code == 200, r.text
        clean_trk()
        v = verify("MD1002", last4("MD1002")).json()
        return {"path": path, "sid": sid, "order": v}

    def test_public_has_photo_flags(self, delivered):
        s = requests.get(f"{API}/track/MD1002", headers=NOAUTH).json()["stop"]
        assert s["has_photo"] is True and s["has_signature"] is True
        assert "pod_photo_path" not in s

    def test_files_gating(self, delivered, admin, drv1):
        p = delivered["path"]
        assert requests.get(f"{API}/files/{p}", headers=NOAUTH).status_code == 401
        assert requests.get(f"{API}/files/{p}?t=garbage", headers=NOAUTH).status_code == 401
        signed = delivered["order"]["pod_photo_url"]
        assert signed and "?t=" in signed
        assert requests.get(f"{BASE_URL}{signed}", headers=NOAUTH).status_code == 200
        assert requests.get(f"{API}/files/{p}?t={drv1}", headers=NOAUTH).status_code == 200
        assert requests.get(f"{API}/files/{p}", headers={"Authorization": f"Bearer {admin}", **NOAUTH}).status_code == 200
        # order token must not open files
        assert requests.get(f"{API}/files/{p}?t={delivered['order']['token']}", headers=NOAUTH).status_code == 401

    def test_signed_link_is_file_specific(self, delivered):
        other = mdb.files.find_one({"storage_path": {"$ne": delivered["path"]}})
        if not other:
            pytest.skip("no other file")
        tok = delivered["order"]["pod_photo_url"].split("?t=")[1]
        assert requests.get(f"{API}/files/{other['storage_path']}?t={tok}", headers=NOAUTH).status_code == 401

    def test_share_and_proof_page(self, delivered):
        h = {"X-Order-Token": delivered["order"]["token"], **NOAUTH}
        r = requests.post(f"{API}/stops/{delivered['sid']}/share", headers=h)
        assert r.status_code == 200
        page = requests.get(f"{BASE_URL}{r.json()['path']}", headers=NOAUTH)
        assert page.status_code == 200
        m = re.search(r'<img src="([^"]+)"', page.text)
        assert m, "no photo in proof page"
        src = m.group(1).replace("&amp;", "&")
        assert requests.get(f"{BASE_URL}{src}", headers=NOAUTH).status_code == 200

    def test_rating_other_token_403_then_low_rating_apology(self, delivered):
        clean_trk()
        t1 = verify("MD1001", last4("MD1001")).json()["token"]
        sid = delivered["sid"]
        assert requests.post(f"{API}/stops/{sid}/rating", json={"stars": 1}, headers={"X-Order-Token": t1, **NOAUTH}).status_code == 403
        r = requests.post(f"{API}/stops/{sid}/rating", json={"stars": 1, "comment": "TEST_ late"},
                          headers={"X-Order-Token": delivered["order"]["token"], **NOAUTH})
        assert r.status_code == 200, r.text
        assert r.json()["rating"] == 1
        p = requests.get(f"{API}/track/MD1002/private", headers={"X-Order-Token": delivered["order"]["token"], **NOAUTH}).json()
        assert p["apology_code"]
        assert "apology_code" not in requests.get(f"{API}/track/MD1002", headers=NOAUTH).json()["stop"]
