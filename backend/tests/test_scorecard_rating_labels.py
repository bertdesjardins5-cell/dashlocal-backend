"""Iteration 6: weekly driver scorecards, history seed isolation, load-order labels,
customer ratings, booking SMS status (no real SMS: seeded numbers are fictional)."""
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
TZ = ZoneInfo("America/Los_Angeles")
FICTIONAL = re.compile(r"^\+1\d{3}55501\d{2}$")


def day(n):
    return (datetime.now(TZ).date() + timedelta(days=n)).isoformat()


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


# --- Analytics / routes / track-codes exclude history
class TestHistoryIsolation:
    def test_analytics_zero_after_reset(self, api):
        a = api.get(f"{API}/analytics").json()
        assert a["delivered"] == 0 and a["failed"] == 0
        assert a["avg_rating"] is None

    def test_routes_only_today_future(self, api):
        rs = api.get(f"{API}/routes").json()
        assert rs, "expected today's routes"
        assert all(not r["name"].startswith("H-") for r in rs)
        assert all(r.get("service_date", day(0)) >= day(0) for r in rs)

    def test_track_codes_exclude_history(self, api):
        codes = [c["tracking_code"] for c in api.get(f"{API}/track-codes").json()]
        assert codes and not any(c.startswith("MH") for c in codes)

    def test_history_stop_trackable_and_fictional(self, api):
        r = api.get(f"{API}/track/MH2001")
        assert r.status_code == 200


# --- Weekly scorecard per driver
class TestScorecard:
    def test_scorecard_structure(self, api):
        r = api.get(f"{API}/drivers/drv-1/scorecard?days=7")
        assert r.status_code == 200
        d = r.json()
        assert d["driver"]["id"] == "drv-1"
        assert len(d["days"]) == 7
        dates = [x["date"] for x in d["days"]]
        assert dates == sorted(dates) and dates[-1] == day(0) and dates[0] == day(-6)
        assert d["from"] == day(-6) and d["to"] == day(0)
        for k in ("delivered", "failed", "on_time_rate", "drops_per_hour", "km_driven", "avg_rating", "routes"):
            assert k in d["days"][0]
        t = d["totals"]
        assert t["delivered"] == sum(x["delivered"] for x in d["days"])
        assert t["failed"] == sum(x["failed"] for x in d["days"])
        assert t["days_worked"] == sum(1 for x in d["days"] if x["routes"])
        assert t["delivered"] > 0, "history seed should give past deliveries"

    def test_on_time_rate_windowed_only(self, api):
        d = api.get(f"{API}/drivers/drv-1/scorecard").json()
        for x in d["days"]:
            if x["windowed_delivered"]:
                assert x["on_time_rate"] == round(x["windowed_on_time"] / x["windowed_delivered"] * 100)
            else:
                assert x["on_time_rate"] is None
        if d["totals"]["avg_rating"] is not None:
            assert 1 <= d["totals"]["avg_rating"] <= 5

    def test_days_clamped(self, api):
        assert len(api.get(f"{API}/drivers/drv-1/scorecard?days=0").json()["days"]) == 1
        assert len(api.get(f"{API}/drivers/drv-1/scorecard?days=99").json()["days"]) == 31

    def test_scorecard_404(self, api):
        assert api.get(f"{API}/drivers/nope/scorecard").status_code == 404

    def test_scorecards_sorted_with_trend(self, api):
        r = api.get(f"{API}/scorecards")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == len(api.get(f"{API}/drivers").json())
        rates = [x["on_time_rate"] if x["on_time_rate"] is not None else -1 for x in rows]
        assert rates == sorted(rates, reverse=True)
        for x in rows:
            assert len(x["trend"]) == 7 and "driver" in x and "_id" not in x


# --- Load-order labels
class TestLabels:
    def test_labels_reverse_order(self, api, route1):
        rid = route1["id"]
        open_stops = [s for s in stops_of(api, rid) if s["status"] not in ("delivered", "failed")]
        lk = api.post(f"{API}/routes/{rid}/labels-link")  # token-gated print link (admin)
        assert lk.status_code == 200, lk.text
        r = api.get(f"{BASE_URL}{lk.json()['path']}")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        html = r.text
        loads = re.findall(r"LOAD (\d{2})</span><span class=\"seq\">DROP #(\d{2})", html)
        assert len(loads) == len(open_stops)
        assert [int(l) for l, _ in loads] == list(range(1, len(open_stops) + 1))
        drops = [int(d) for _, d in loads]
        assert drops == sorted(drops, reverse=True)
        assert drops[0] == max(s["seq"] for s in open_stops)  # LOAD 01 = last drop

    def test_labels_unknown_route(self, api):
        assert api.get(f"{API}/routes/nope/labels.html").status_code == 401  # no token
        assert api.post(f"{API}/routes/nope/labels-link").status_code == 404


# --- Booking SMS status (seeded fictional numbers only)
class TestBookingSms:
    def test_window_skipped_fictional_then_unchanged(self, api, route1):
        s = [x for x in stops_of(api, route1["id"]) if x["status"] == "pending"][-1]
        assert FICTIONAL.match(s["customer_phone"]), f"safety: {s['customer_phone']} not fictional"
        body = {"start": "22:00", "end": "23:59", "date": day(0)}
        r = api.put(f"{API}/stops/{s['id']}/window", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["sms_status"] == "skipped_fictional"
        r2 = api.put(f"{API}/stops/{s['id']}/window", json=body)
        assert r2.json()["sms_status"] == "unchanged"
        assert api.get(f"{API}/stops/{s['id']}").json()["window_end"] == "23:59"


# --- Customer rating
class TestRating:
    def test_rating_flow(self, api, route1):
        rid = route1["id"]
        api.post(f"{API}/routes/{rid}/start")
        pend = [x for x in stops_of(api, rid) if x["status"] == "pending"]
        s = pend[0]
        # not delivered -> 400
        r = api.post(f"{API}/stops/{s['id']}/rating", json={"stars": 5})
        assert r.status_code == 400 and "delivered" in r.json()["detail"].lower()
        d = api.post(f"{API}/stops/{s['id']}/deliver", json={"signature": "M10 10 L60 40"})
        assert d.status_code == 200, d.text
        for bad in (0, 6, -1):
            r = api.post(f"{API}/stops/{s['id']}/rating", json={"stars": bad})
            assert r.status_code == 400, bad
        assert api.post(f"{API}/stops/{s['id']}/rating", json={"stars": "x"}).status_code == 422
        r = api.post(f"{API}/stops/{s['id']}/rating", json={"stars": 4, "comment": "  TEST_ FRIENDLY DRIVER  "})
        assert r.status_code == 200
        assert r.json()["rating"] == 4 and r.json()["rating_comment"] == "TEST_ FRIENDLY DRIVER"
        g = api.get(f"{API}/stops/{s['id']}").json()
        assert g["rating"] == 4 and g["rating_comment"] == "TEST_ FRIENDLY DRIVER"
        r = api.post(f"{API}/stops/{s['id']}/rating", json={"stars": 5})
        assert r.status_code == 400 and "already" in r.json()["detail"].lower()
        # rating flows into today's analytics + summary + scorecard
        a = api.get(f"{API}/analytics").json()
        assert a["delivered"] == 1 and a["avg_rating"] == 4.0
        sm = api.get(f"{API}/drivers/drv-1/summary").json()
        assert sm["avg_rating"] == 4.0 and sm["ratings"] == 1
        sc = api.get(f"{API}/drivers/drv-1/scorecard").json()
        assert sc["days"][-1]["avg_rating"] == 4.0 and sc["days"][-1]["delivered"] == 1

    def test_rating_404(self, api):
        assert api.post(f"{API}/stops/nope/rating", json={"stars": 3}).status_code == 404
