"""Iteration 7: low-rating alerts + follow-up, WS /api/ws/admin push, delivery proof fields, weekly badges + rank."""
import asyncio
import json
import os
import pytest
import requests
import websockets

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
WS = BASE_URL.replace("http", "ws", 1) + "/api/ws/admin"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    assert s.post(f"{API}/admin/reset").status_code == 200
    yield s
    s.post(f"{API}/admin/reset")


@pytest.fixture(scope="module")
def route1(api):
    d = api.get(f"{API}/drivers/drv-1/route").json()
    return d.get("route", d)


def stops_of(api, rid):
    return api.get(f"{API}/routes/{rid}").json()["stops"]


# --- Low rating list / filter
class TestLowRatingList:
    def test_seed_has_open_low_ratings(self, api):
        r = api.get(f"{API}/alerts/low-ratings")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        for a in data:
            assert set(a) >= {"stop", "driver", "route_name"}
            assert a["stop"]["rating"] in (1, 2)
            assert a["stop"]["follow_up_status"] == "open"
            assert "_id" not in a["stop"]
            assert a["driver"] and a["driver"]["id"] and a["route_name"]

    def test_status_filters(self, api):
        o = api.get(f"{API}/alerts/low-ratings?status=open").json()
        rs = api.get(f"{API}/alerts/low-ratings?status=resolved").json()
        al = api.get(f"{API}/alerts/low-ratings?status=all").json()
        assert all(a["stop"]["follow_up_status"] == "resolved" for a in rs)
        assert len(al) == len(o) + len(rs)

    def test_sorted_by_rated_at_desc(self, api):
        al = api.get(f"{API}/alerts/low-ratings?status=all").json()
        ts = [a["stop"]["rated_at"] for a in al]
        assert ts == sorted(ts, reverse=True)


# --- Follow-up
class TestFollowUp:
    def test_save_note_keeps_open_then_resolve(self, api):
        first = api.get(f"{API}/alerts/low-ratings?status=open").json()[0]
        sid = first["stop"]["id"]
        r = api.post(f"{API}/stops/{sid}/follow-up", json={"note": "TEST_ called customer", "resolved": False})
        assert r.status_code == 200
        assert r.json()["stop"]["follow_up_status"] == "open"
        assert r.json()["stop"]["follow_up_note"] == "TEST_ called customer"
        # resolve with empty note keeps previous note
        r = api.post(f"{API}/stops/{sid}/follow-up", json={"note": "", "resolved": True})
        assert r.status_code == 200
        assert r.json()["stop"]["follow_up_status"] == "resolved"
        assert r.json()["stop"]["follow_up_note"] == "TEST_ called customer"
        assert r.json()["stop"]["follow_up_at"]
        open_ids = [a["stop"]["id"] for a in api.get(f"{API}/alerts/low-ratings?status=open").json()]
        res_ids = [a["stop"]["id"] for a in api.get(f"{API}/alerts/low-ratings?status=resolved").json()]
        assert sid not in open_ids and sid in res_ids
        g = api.get(f"{API}/stops/{sid}").json()
        g = g.get("stop", g)
        assert g["follow_up_status"] == "resolved"

    def test_400_for_high_or_unrated(self, api, route1):
        unrated = stops_of(api, route1["id"])[0]
        r = api.post(f"{API}/stops/{unrated['id']}/follow-up", json={"note": "x", "resolved": True})
        assert r.status_code == 400
        # find a 4-5 star seeded history stop through track codes is hard; use scorecard-independent check below
        assert api.post(f"{API}/stops/nope/follow-up", json={"resolved": True}).status_code == 404


# --- Rating a delivered stop with 2 stars creates an open alert + WS push
class TestRatingCreatesAlert:
    def test_two_star_alert_and_ws(self, api, route1):
        stops = [s for s in stops_of(api, route1["id"]) if s["status"] == "pending"]
        a, b = stops[0], stops[1]
        # deliver A via signature + QR
        q = api.post(f"{API}/stops/{a['id']}/verify-qr", json={"code": f"MULTIDROP:{a['tracking_code']}"})
        assert q.status_code == 200 and q.json()["pod_qr_verified"] is True
        d = api.post(f"{API}/stops/{a['id']}/deliver", json={"signature": "M10 10 L100 100"})
        assert d.status_code == 200, d.text
        assert d.json()["pod_signature"] == "M10 10 L100 100" and d.json()["pod_qr_verified"]
        tr = api.get(f"{API}/track/{a['tracking_code']}").json()
        st = tr.get("stop", tr)
        assert "pod_signature" not in st and st["has_signature"] and st["pod_qr_verified"] and st["delivered_at"]

        async def run():
            from conftest import _admin_token  # ws/admin needs ?token=<admin session>
            async with websockets.connect(f"{WS}?token={_admin_token(BASE_URL)}") as ws:
                await asyncio.sleep(0.5)
                r = await asyncio.to_thread(api.post, f"{API}/stops/{a['id']}/rating", json={"stars": 2, "comment": "TEST_ late"})
                assert r.status_code == 200
                assert r.json()["follow_up_status"] == "open"
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                return msg
        msg = asyncio.run(run())
        assert msg["type"] == "low_rating"
        assert msg["alert"]["stop"]["id"] == a["id"] and msg["alert"]["stop"]["rating"] == 2
        assert msg["alert"]["driver"]["id"] == "drv-1"
        o = api.get(f"{API}/alerts/low-ratings?status=open").json()
        assert o[0]["stop"]["id"] == a["id"]  # newest first

        # deliver B and rate 5 -> no alert, follow-up 400
        api.post(f"{API}/stops/{b['id']}/deliver", json={"signature": "M1 1 L2 2"})
        r = api.post(f"{API}/stops/{b['id']}/rating", json={"stars": 5})
        assert r.status_code == 200 and r.json().get("follow_up_status") is None
        assert api.post(f"{API}/stops/{b['id']}/follow-up", json={"resolved": True}).status_code == 400
        all_ids = [x["stop"]["id"] for x in api.get(f"{API}/alerts/low-ratings?status=all").json()]
        assert b["id"] not in all_ids


# --- Badges + rank
METRIC = {"on_time": ("on_time_rate", "windowed_delivered", 3), "top_rated": ("avg_rating", "delivered", 3),
          "speed": ("drops_per_hour", "delivered", 5), "volume": ("delivered", "delivered", 1)}


class TestBadges:
    def test_badges_consistent(self, api):
        cards = api.get(f"{API}/scorecards").json()
        for key, (metric, field, n) in METRIC.items():
            elig = [c for c in cards if c.get(metric) is not None and c[field] >= n]
            holders = [c for c in cards if any(b["key"] == key for b in c["badges"])]
            if not elig:
                assert not holders
                continue
            best = max(c[metric] for c in elig)
            assert {c["driver"]["id"] for c in holders} == {c["driver"]["id"] for c in elig if c[metric] == best}, key
            for c in holders:
                b = next(b for b in c["badges"] if b["key"] == key)
                assert b["value"] == best and b["label"]
        assert any(c["badges"] for c in cards)

    def test_driver_scorecard_badges_rank(self, api):
        cards = api.get(f"{API}/scorecards").json()
        for i, c in enumerate(cards):
            sc = api.get(f"{API}/drivers/{c['driver']['id']}/scorecard").json()
            assert sc["rank"] == i + 1
            assert sc["badges"] == c["badges"]
