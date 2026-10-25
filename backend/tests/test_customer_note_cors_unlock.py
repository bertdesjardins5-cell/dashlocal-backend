# Iteration 20: Customer delivery note, CORS allow-list, 7-day unlock expiry, /track-codes removed
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
LOCAL = "http://localhost:8001"
NOAUTH = {"X-No-Auto-Auth": "1"}


def mongo(js):
    return subprocess.run(["mongosh", "test_database", "--quiet", "--eval", js], capture_output=True, text=True).stdout.strip()


def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def driver_token(phone, pin):
    r = requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": phone, "pin": pin}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code} {r.text}")
    return r.json()["token"]


def stop_of(code):
    return mongo(f'print(db.stops.findOne({{tracking_code:"{code}"}}).id)')


def verify(code, last4):
    r = requests.post(f"{BASE_URL}/api/track/{code}/verify", json={"last4": last4}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def ctx():
    mongo('db.login_attempts.deleteMany({key:/^(drv|trk)/})')
    a = admin_token()
    requests.post(f"{BASE_URL}/api/admin/reset", headers={"Authorization": f"Bearer {a}"})
    s1, s2 = stop_of("MD1001"), stop_of("MD1002")
    t1 = verify("MD1001", "0100")["token"]
    t2 = verify("MD1002", "0101")["token"]
    yield {"admin": a, "s1": s1, "s2": s2, "t1": t1, "t2": t2}
    mongo('db.login_attempts.deleteMany({key:/^(drv|trk)/})')
    requests.post(f"{BASE_URL}/api/admin/reset", headers={"Authorization": f"Bearer {a}"})


# ---- PUT /stops/{id}/customer-note
class TestCustomerNote:
    def test_no_auth_401(self, ctx):
        r = requests.put(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note", json={"note": "x"}, headers=NOAUTH)
        assert r.status_code == 401

    def test_other_stop_token_403(self, ctx):
        r = requests.put(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note", json={"note": "x"},
                         headers={**NOAUTH, "X-Order-Token": ctx["t2"]})
        assert r.status_code == 403

    def test_save_trim_collapse(self, ctx):
        r = requests.put(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note", json={"note": "  Gate   code\n 4321  "},
                         headers={**NOAUTH, "X-Order-Token": ctx["t1"]})
        assert r.status_code == 200, r.text
        assert r.json()["customer_note"] == "Gate code 4321"
        assert mongo(f'print(db.stops.findOne({{id:"{ctx["s1"]}"}}).customer_note_seen)') == "false"

    def test_route_includes_note(self, ctx):
        r = requests.get(f"{BASE_URL}/api/drivers/drv-1/route", headers={"Authorization": f"Bearer {ctx['admin']}"})
        assert r.status_code == 200
        st = [s for s in r.json()["route"]["stops"] if s["id"] == ctx["s1"]][0]
        assert st["customer_note"] == "Gate code 4321" and st["customer_note_seen"] is False

    def test_private_and_verify_return_note(self, ctx):
        r = requests.get(f"{BASE_URL}/api/track/MD1001/private", headers={**NOAUTH, "X-Order-Token": ctx["t1"]})
        assert r.status_code == 200 and r.json()["customer_note"] == "Gate code 4321"
        assert verify("MD1001", "0100")["customer_note"] == "Gate code 4321"

    def test_public_track_hides_note(self, ctx):
        r = requests.get(f"{BASE_URL}/api/track/MD1001", headers=NOAUTH)
        assert r.status_code == 200
        assert "customer_note" not in r.json()

    def test_max_200(self, ctx):
        r = requests.put(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note", json={"note": "a" * 300},
                         headers={**NOAUTH, "X-Order-Token": ctx["t1"]})
        assert r.status_code == 200 and len(r.json()["customer_note"]) == 200

    def test_empty_clears(self, ctx):
        r = requests.put(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note", json={"note": "   "},
                         headers={**NOAUTH, "X-Order-Token": ctx["t1"]})
        assert r.status_code == 200 and r.json()["customer_note"] is None
        assert mongo(f'print(db.stops.findOne({{id:"{ctx["s1"]}"}}).customer_note_seen)') == "true"

    def test_admin_can_set(self, ctx):
        r = requests.put(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note", json={"note": "Admin note"},
                         headers={"Authorization": f"Bearer {ctx['admin']}"})
        assert r.status_code == 200 and r.json()["customer_note"] == "Admin note"

    # ---- POST /customer-note/seen
    def test_seen_other_driver_403(self, ctx):
        t = driver_token("+14155550102", "730516")
        r = requests.post(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note/seen", headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 403

    def test_seen_customer_token_rejected(self, ctx):
        r = requests.post(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note/seen", headers={**NOAUTH, "X-Order-Token": ctx["t1"]})
        assert r.status_code == 401

    def test_seen_route_driver(self, ctx):
        t = driver_token("+14155550101", "482913")
        r = requests.post(f"{BASE_URL}/api/stops/{ctx['s1']}/customer-note/seen", headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 200
        assert mongo(f'print(db.stops.findOne({{id:"{ctx["s1"]}"}}).customer_note_seen)') == "true"

    def test_locked_after_arrive(self, ctx):
        mongo(f'db.stops.updateOne({{id:"{ctx["s2"]}"}},{{$set:{{status:"arrived"}}}})')
        r = requests.put(f"{BASE_URL}/api/stops/{ctx['s2']}/customer-note", json={"note": "late"},
                         headers={**NOAUTH, "X-Order-Token": ctx["t2"]})
        mongo(f'db.stops.updateOne({{id:"{ctx["s2"]}"}},{{$set:{{status:"pending"}}}})')
        assert r.status_code == 400


# ---- 7-day unlock expiry
class TestUnlockExpiry:
    def test_expired_after_7_days(self, ctx):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        mongo(f'db.stops.updateOne({{id:"{ctx["s2"]}"}},{{$set:{{delivered_at:"{old}"}}}})')
        r = requests.get(f"{BASE_URL}/api/track/MD1002/private", headers={**NOAUTH, "X-Order-Token": ctx["t2"]})
        r2 = requests.put(f"{BASE_URL}/api/stops/{ctx['s2']}/window", json={"day": 1, "slot": "10-12"},
                          headers={**NOAUTH, "X-Order-Token": ctx["t2"]})
        recent = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
        mongo(f'db.stops.updateOne({{id:"{ctx["s2"]}"}},{{$set:{{delivered_at:"{recent}"}}}})')
        r3 = requests.get(f"{BASE_URL}/api/track/MD1002/private", headers={**NOAUTH, "X-Order-Token": ctx["t2"]})
        mongo(f'db.stops.updateOne({{id:"{ctx["s2"]}"}},{{$set:{{delivered_at:null}}}})')
        assert r.status_code == 401 and "expired" in r.text.lower()
        assert r2.status_code == 401 and "expired" in r2.text.lower()
        assert r3.status_code == 200


# ---- Launch settings
class TestLaunch:
    def test_track_codes_removed(self):
        assert requests.get(f"{BASE_URL}/api/track-codes", headers=NOAUTH).status_code == 404

    def test_cors_allowed_origin(self):
        r = requests.options(f"{LOCAL}/api/track/MD1001", headers={"Origin": "https://dashlocal.app", "Access-Control-Request-Method": "GET"})
        assert r.headers.get("access-control-allow-origin") == "https://dashlocal.app"
        r = requests.get(f"{LOCAL}/api/track/MD1001", headers={"Origin": "https://dashlocal.app"})
        assert r.headers.get("access-control-allow-origin") == "https://dashlocal.app"

    def test_cors_evil_origin(self):
        r = requests.options(f"{LOCAL}/api/track/MD1001", headers={"Origin": "https://evil.com", "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
        r = requests.get(f"{LOCAL}/api/track/MD1001", headers={"Origin": "https://evil.com"})
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
