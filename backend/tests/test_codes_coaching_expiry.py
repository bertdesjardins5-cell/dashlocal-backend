"""Iteration 9: apology code redemption tracking, driver coaching notes, proof link 30-day expiry."""
import os
from datetime import datetime, timedelta, timezone
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


def _env(key):
    for line in open("/app/backend/.env"):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"')


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    assert s.post(f"{API}/admin/reset").status_code == 200
    yield s
    s.post(f"{API}/admin/reset")


@pytest.fixture(scope="module")
def mdb():
    c = MongoClient(_env("MONGO_URL"))
    yield c[_env("DB_NAME")]
    c.close()


def check_totals(d):
    used = [c["redemption"] for c in d["codes"] if c["redemption"]]
    rev = round(sum(r["order_value"] for r in used), 2)
    disc = round(sum(r["discount"] for r in used), 2)
    assert d["issued"] == len(d["codes"])
    assert d["redeemed"] == len(used)
    assert abs(d["revenue"] - rev) < 0.01
    assert abs(d["discount_given"] - disc) < 0.01
    assert abs(d["net_revenue"] - round(rev - disc, 2)) < 0.01
    assert d["redemption_rate"] == (round(len(used) / len(d["codes"]) * 100) if d["codes"] else 0)
    assert abs(d["avg_order"] - (round(rev / len(used), 2) if used else 0)) < 0.01
    for r in used:
        assert abs(r["discount"] - round(r["order_value"] * 0.15, 2)) < 0.011


# --- Apology codes
class TestApologyCodes:
    def test_seed_summary(self, api):
        d = api.get(f"{API}/codes/apology").json()
        assert d["discount_pct"] == 15
        assert d["issued"] > 0 and 0 < d["redeemed"] < d["issued"]
        print(f"seed issued={d['issued']} redeemed={d['redeemed']} rate={d['redemption_rate']}")
        check_totals(d)
        for c in d["codes"]:
            assert c["code"].startswith("SORRY-") and "_id" not in c

    def test_redeem_errors_and_success(self, api):
        d = api.get(f"{API}/codes/apology").json()
        unused = next(c for c in d["codes"] if not c["redemption"])
        used = next(c for c in d["codes"] if c["redemption"])
        assert api.post(f"{API}/codes/redeem", json={"code": "SORRY-ZZZZZ9", "order_value": 40}).status_code == 404
        assert api.post(f"{API}/codes/redeem", json={"code": used["code"], "order_value": 40}).status_code == 400
        for bad in (0, -5, 10000.01):
            r = api.post(f"{API}/codes/redeem", json={"code": unused["code"], "order_value": bad})
            assert r.status_code == 400, (bad, r.text)
        # lower-case + whitespace normalized
        r = api.post(f"{API}/codes/redeem", json={"code": f"  {unused['code'].lower()} ", "order_value": 40, "order_ref": "TEST_ORD1"})
        assert r.status_code == 200, r.text
        rec = r.json()
        assert rec["code"] == unused["code"] and rec["discount"] == 6.0 and rec["order_ref"] == "TEST_ORD1"
        assert "_id" not in rec
        # reuse now 400
        assert api.post(f"{API}/codes/redeem", json={"code": unused["code"], "order_value": 10}).status_code == 400
        d2 = api.get(f"{API}/codes/apology").json()
        assert d2["redeemed"] == d["redeemed"] + 1
        assert abs(d2["revenue"] - (d["revenue"] + 40)) < 0.01
        assert abs(d2["discount_given"] - (d["discount_given"] + 6)) < 0.01
        row = next(c for c in d2["codes"] if c["code"] == unused["code"])
        assert row["redemption"]["order_value"] == 40
        check_totals(d2)

    def test_boundary_10000_ok(self, api):
        d = api.get(f"{API}/codes/apology").json()
        unused = next(c for c in d["codes"] if not c["redemption"])
        r = api.post(f"{API}/codes/redeem", json={"code": unused["code"], "order_value": 10000})
        assert r.status_code == 200 and r.json()["discount"] == 1500.0


# --- Coaching notes
class TestCoaching:
    def test_errors(self, api):
        assert api.post(f"{API}/drivers/drv-1/coaching", json={"note": "   "}).status_code == 400
        assert api.post(f"{API}/drivers/nope/coaching", json={"note": "hi"}).status_code == 404
        assert api.get(f"{API}/drivers/nope/coaching").status_code == 404
        assert api.post(f"{API}/coaching/nope/read").status_code == 404

    def test_send_list_read(self, api):
        assert api.get(f"{API}/drivers/drv-2/coaching?unread=true").json() == []
        r = api.post(f"{API}/drivers/drv-2/coaching", json={"note": "TEST_ ring the bell", "reason": "Bell"})
        assert r.status_code == 200, r.text
        n = r.json()
        assert n["driver_id"] == "drv-2" and n["note"] == "TEST_ ring the bell" and n["reason"] == "Bell" and n["read_at"] is None
        unread = api.get(f"{API}/drivers/drv-2/coaching?unread=true").json()
        assert [x["id"] for x in unread] == [n["id"]]
        assert api.post(f"{API}/coaching/{n['id']}/read").json()["ok"] is True
        assert api.post(f"{API}/coaching/{n['id']}/read").status_code == 200  # idempotent
        assert api.get(f"{API}/drivers/drv-2/coaching?unread=true").json() == []
        allnotes = api.get(f"{API}/drivers/drv-2/coaching").json()
        assert allnotes[0]["id"] == n["id"] and allnotes[0]["read_at"]
        # other driver isolated
        assert api.get(f"{API}/drivers/drv-3/coaching").json() == []


# --- Proof link expiry
class TestShareExpiry:
    def test_expiry_and_reshare(self, api, mdb):
        st = mdb.stops.find_one({"status": "delivered"}, {"_id": 0, "id": 1})
        r = api.post(f"{API}/stops/{st['id']}/share")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["valid_days"] == 30
        exp = datetime.fromisoformat(d["expires_at"])
        assert abs((exp - datetime.now(timezone.utc)) - timedelta(days=30)) < timedelta(minutes=5)
        tok = d["token"]
        assert api.get(f"{API}/proof/{tok}").status_code == 200
        # same token while valid
        assert api.post(f"{API}/stops/{st['id']}/share").json()["token"] == tok
        # age 31 days
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        mdb.stops.update_one({"id": st["id"]}, {"$set": {"share_created_at": old}})
        g = api.get(f"{API}/proof/{tok}")
        assert g.status_code == 410 and "LINK EXPIRED" in g.text
        r2 = api.post(f"{API}/stops/{st['id']}/share").json()
        assert r2["token"] != tok and r2["valid_days"] == 30
        assert api.get(f"{API}/proof/{r2['token']}").status_code == 200
        assert api.get(f"{API}/proof/{tok}").status_code == 404

    def test_day_29_still_valid(self, api, mdb):
        st = mdb.stops.find_one({"status": "delivered", "share_token": {"$ne": None}}, {"_id": 0, "id": 1, "share_token": 1})
        mdb.stops.update_one({"id": st["id"]}, {"$set": {"share_created_at": (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()}})
        assert api.get(f"{API}/proof/{st['share_token']}").status_code == 200
        r = api.post(f"{API}/stops/{st['id']}/share").json()
        assert r["token"] == st["share_token"]
        assert abs(datetime.fromisoformat(r["expires_at"]) - (datetime.now(timezone.utc) + timedelta(days=1))) < timedelta(minutes=5)

    def test_pending_stop_400(self, api, mdb):
        st = mdb.stops.find_one({"status": "pending"}, {"_id": 0, "id": 1})
        assert api.post(f"{API}/stops/{st['id']}/share").status_code == 400
