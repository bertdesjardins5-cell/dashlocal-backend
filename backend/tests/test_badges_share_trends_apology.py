"""Iteration 8: badge celebrations (new/seen), proof share links, alert trends, apology texts on low ratings."""
import os
import re
from datetime import datetime, timedelta, timezone
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    assert s.post(f"{API}/admin/reset").status_code == 200
    yield s
    s.post(f"{API}/admin/reset")


@pytest.fixture(scope="module")
def pending(api):
    d = api.get(f"{API}/drivers/drv-1/route").json()
    rid = d.get("route", d)["id"]
    return [s for s in api.get(f"{API}/routes/{rid}").json()["stops"] if s["status"] == "pending"]


def deliver(api, stop):
    r = api.post(f"{API}/stops/{stop['id']}/deliver", json={"signature": "M10 10 L100 100"})
    assert r.status_code == 200, r.text
    return r.json()


# --- Badge celebrations
class TestBadges:
    def test_unknown_driver_404(self, api):
        assert api.get(f"{API}/drivers/nope/badges/new").status_code == 404
        assert api.post(f"{API}/drivers/nope/badges/seen", json={"keys": ["x"]}).status_code == 404

    @pytest.mark.parametrize("drv", ["drv-2", "drv-3"])
    def test_new_then_seen_flow(self, api, drv):
        r = api.get(f"{API}/drivers/{drv}/badges/new")
        assert r.status_code == 200
        badges = r.json()["badges"]
        assert len(badges) >= 1, f"{drv} should hold badges after reset"
        for b in badges:
            assert {"key", "label", "value"} <= set(b)
        # badges/new must match held badges in scorecards
        board = api.get(f"{API}/scorecards").json()
        cards = board if isinstance(board, list) else board.get("scorecards", board.get("drivers", []))
        held = next(c["badges"] for c in cards if c["driver"]["id"] == drv)
        assert {b["key"] for b in badges} == {b["key"] for b in held}
        # repeat call before seen returns same set
        assert api.get(f"{API}/drivers/{drv}/badges/new").json()["badges"] == badges
        keys = [b["key"] for b in badges]
        s = api.post(f"{API}/drivers/{drv}/badges/seen", json={"keys": keys})
        assert s.status_code == 200 and s.json()["ok"] is True
        assert api.get(f"{API}/drivers/{drv}/badges/new").json()["badges"] == []
        # idempotent repeat seen
        assert api.post(f"{API}/drivers/{drv}/badges/seen", json={"keys": keys}).status_code == 200
        assert api.get(f"{API}/drivers/{drv}/badges/new").json()["badges"] == []

    def test_lost_badge_pulled_from_seen(self, api):
        # mark a non-held key as seen; badges/new should pull it (no crash, held unaffected)
        api.post(f"{API}/drivers/drv-1/badges/seen", json={"keys": ["volume_fake"]})
        r = api.get(f"{API}/drivers/drv-1/badges/new")
        assert r.status_code == 200 and isinstance(r.json()["badges"], list)

    def test_seen_validation(self, api):
        assert api.post(f"{API}/drivers/drv-2/badges/seen", json={}).status_code == 422

    def test_reset_clears_seen(self, api):
        api.post(f"{API}/admin/reset")
        assert len(api.get(f"{API}/drivers/drv-2/badges/new").json()["badges"]) >= 1


# --- Proof share link
class TestShare:
    def test_share_undelivered_400(self, api, pending):
        assert api.post(f"{API}/stops/{pending[-1]['id']}/share").status_code == 400

    def test_share_unknown_404(self, api):
        assert api.post(f"{API}/stops/nope/share").status_code == 404

    def test_share_create_reuse_and_public_page(self, api):
        d = api.get(f"{API}/drivers/drv-1/route").json()
        rid = d.get("route", d)["id"]
        st = next(s for s in api.get(f"{API}/routes/{rid}").json()["stops"] if s["status"] == "pending")
        deliver(api, st)
        r1 = api.post(f"{API}/stops/{st['id']}/share")
        assert r1.status_code == 200, r1.text
        j = r1.json()
        assert j["path"] == f"/api/proof/{j['token']}" and len(j["token"]) >= 16
        assert api.post(f"{API}/stops/{st['id']}/share").json()["token"] == j["token"]
        page = requests.get(f"{BASE_URL}{j['path']}")
        assert page.status_code == 200 and "text/html" in page.headers["content-type"]
        assert st["tracking_code"] in page.text and "SIGNATURE" in page.text and "<svg" in page.text
        assert requests.get(f"{API}/proof/badtoken123").status_code == 404


# --- Apology texts + trends
class TestApologyAndTrends:
    def test_low_rating_apology_and_trends(self, api):
        d = api.get(f"{API}/drivers/drv-1/route").json()
        rid = d.get("route", d)["id"]
        pend = [s for s in api.get(f"{API}/routes/{rid}").json()["stops"] if s["status"] == "pending"]
        before = api.get(f"{API}/alerts/trends?days=7").json()
        a, b = pend[0], pend[1]
        deliver(api, a)
        deliver(api, b)
        ra = api.post(f"{API}/stops/{a['id']}/rating", json={"stars": 1, "comment": "LATE, DAMAGED"})
        assert ra.status_code == 200, ra.text
        ja = ra.json()
        assert re.fullmatch(r"SORRY-[A-Z0-9]{5}", ja["apology_code"])
        assert ja["apology_sms_status"] == "skipped_fictional"
        tr = api.get(f"{API}/track/{a['tracking_code']}").json()
        assert tr.get("stop", tr)["apology_code"] == ja["apology_code"]
        rb = api.post(f"{API}/stops/{b['id']}/rating", json={"stars": 5})
        assert rb.status_code == 200 and rb.json().get("apology_code") is None and rb.json().get("apology_sms_status") is None
        # low-rating alerts contain apology
        al = api.get(f"{API}/alerts/low-ratings").json()
        row = next(x for x in al if x["stop"]["id"] == a["id"])
        assert row["stop"]["apology_code"] == ja["apology_code"]
        # trends updated
        t = api.get(f"{API}/alerts/trends?days=7").json()
        assert t["total_ratings"] == before["total_ratings"] + 2
        assert t["total_low"] == before["total_low"] + 1
        drv = next(x for x in t["by_driver"] if x["key"] == "drv-1")
        zone = next(x for x in t["by_zone"] if x["key"] == a["zone"])
        assert drv["low"] >= 1 and zone["low"] >= 1
        assert any(r["reason"] == "LATE" for r in drv["top_reasons"])

    def test_trends_shape_and_consistency(self, api):
        t = api.get(f"{API}/alerts/trends?days=7").json()
        assert {"total_ratings", "total_low", "by_driver", "by_zone", "daily"} <= set(t)
        assert len(t["daily"]) == 7
        assert sum(x["low"] for x in t["daily"]) == t["total_low"]
        assert sum(x["low"] for x in t["by_zone"]) == t["total_low"]
        assert sum(x["ratings"] for x in t["by_zone"]) == t["total_ratings"]
        for x in t["by_driver"] + t["by_zone"]:
            assert {"key", "label", "ratings", "low", "low_pct", "top_reasons"} <= set(x)
            assert x["low_pct"] == round(x["low"] / x["ratings"] * 100)
            assert len(x["top_reasons"]) <= 3
        lows = [x["low"] for x in t["by_driver"]]
        assert lows == sorted(lows, reverse=True)
        names = {d["id"]: d["name"] for d in api.get(f"{API}/drivers").json()}
        for x in t["by_driver"]:
            assert x["label"] == names.get(x["key"], x["key"])

    def test_trends_days_clamped(self, api):
        assert api.get(f"{API}/alerts/trends?days=100").json()["days"] == 31
        assert len(api.get(f"{API}/alerts/trends?days=0").json()["daily"]) == 1
