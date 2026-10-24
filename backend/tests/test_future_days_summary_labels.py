"""Iteration 5: Future-day booking, driver day summary, printable label sheet."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
TZ = ZoneInfo("America/Los_Angeles")


def nice(d):
    return datetime.fromisoformat(d).strftime("%a %d %b")  # server nice_date format


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
def r101(api):
    d = api.get(f"{API}/drivers/drv-1/route").json()
    return d.get("route", d)


def pending_stop(api, rid):
    st = api.get(f"{API}/routes/{rid}").json()["stops"]
    return [s for s in st if s["status"] == "pending"]


# --- Future-day booking validation
def test_window_date_past_400(api, r101):
    s = pending_stop(api, r101["id"])[0]
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "10:00", "end": "12:00", "date": day(-1)})
    assert r.status_code == 400 and "past" in r.json()["detail"].lower()


def test_window_date_too_far_400(api, r101):
    s = pending_stop(api, r101["id"])[0]
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "10:00", "end": "12:00", "date": day(14)})
    assert r.status_code == 400
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "10:00", "end": "12:00", "date": "2026-13-45"})
    assert r.status_code == 400


def test_today_past_slot_400(api, r101):
    if datetime.now(TZ).hour < 1:
        pytest.skip("too early for past slot")
    s = pending_stop(api, r101["id"])[0]
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "00:00", "end": "00:30", "date": day(0)})
    assert r.status_code == 400 and "passed" in r.json()["detail"]


def test_day13_allowed_on_unrouted_semantics(api, r101):
    # +13 allowed by API (will move off route); verify not rejected
    stops = pending_stop(api, r101["id"])
    s = stops[-1]
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "08:00", "end": "10:00", "date": day(13)})
    assert r.status_code == 200, r.text
    assert r.json()["window_date"] == day(13)
    pytest.moved13 = s["id"]


# --- Move off route
def test_book_tomorrow_moves_off_route(api, r101):
    stops = pending_stop(api, r101["id"])
    s = stops[0]
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "08:00", "end": "10:00", "date": day(1)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["moved_off_route"] == r101["name"]
    assert d["route_id"] is None and d["window_date"] == day(1)
    assert d["window_start"] == "08:00"
    pytest.moved = s
    rest = api.get(f"{API}/routes/{r101['id']}").json()["stops"]
    assert s["id"] not in [x["id"] for x in rest]
    assert sorted(x["seq"] for x in rest) == list(range(1, len(rest) + 1))


def test_same_day_booking_no_move(api, r101):
    s = pending_stop(api, r101["id"])[0]
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "22:00", "end": "23:59", "date": day(0)})
    if datetime.now(TZ).strftime("%H:%M") >= "23:59":
        pytest.skip("late")
    assert r.status_code == 200, r.text
    assert r.json()["moved_off_route"] is None and r.json()["route_id"] == r101["id"]


def test_track_scheduled_alert_unrouted(api):
    s = pytest.moved
    t = api.get(f"{API}/track/{s['tracking_code']}").json()
    assert t["alert"] == f"Scheduled for {nice(day(1))}", t


def test_zones_by_date(api):
    s = pytest.moved
    z = api.get(f"{API}/zones", params={"date": day(1)}).json()
    ids = [o["id"] for g in z for o in g["orders"]]
    assert s["id"] in ids
    z0 = api.get(f"{API}/zones").json()
    assert s["id"] not in [o["id"] for g in z0 for o in g["orders"]]
    z13 = [o["id"] for g in api.get(f"{API}/zones", params={"date": day(13)}).json() for o in g["orders"]]
    assert pytest.moved13 in z13 and s["id"] not in z13
    assert api.get(f"{API}/zones", params={"date": day(-1)}).status_code == 400


def test_batch_future_route(api):
    s = pytest.moved
    drivers = api.get(f"{API}/drivers").json()
    drv = [d for d in drivers if d["id"] != "drv-1"][0]["id"]
    r = api.post(f"{API}/routes/batch", json={"zone": s["zone"], "driver_id": drv, "service_date": day(1)})
    assert r.status_code == 200, r.text
    route = r.json()
    assert route["service_date"] == day(1)
    assert s["id"] in [x["id"] for x in route["stops"]]
    for x in route["stops"]:
        assert x.get("planned_eta"), x
        assert datetime.fromisoformat(x["planned_eta"]).astimezone(TZ).date().isoformat() == day(1), x["planned_eta"]
    pytest.future_route = route
    st = api.post(f"{API}/routes/{route['id']}/start")
    assert st.status_code == 400 and "scheduled for" in st.json()["detail"].lower()
    t = api.get(f"{API}/track/{s['tracking_code']}").json()
    assert t["alert"] == f"Scheduled for {nice(day(1))}", t


def test_move_back_to_today_from_future_route(api):
    route = pytest.future_route
    s = route["stops"][0]
    if datetime.now(TZ).strftime("%H:%M") >= "23:00":
        pytest.skip("late")
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "23:00", "end": "23:59", "date": day(0)})
    assert r.status_code == 200
    assert r.json()["moved_off_route"] == route["name"]
    if len(route["stops"]) == 1:
        assert api.get(f"{API}/routes/{route['id']}").status_code == 404


# --- Driver summary
def test_summary_structure(api):
    r = api.get(f"{API}/drivers/drv-1/summary")
    assert r.status_code == 200
    d = r.json()
    for k in ["delivered", "failed", "remaining", "on_time_rate", "km_driven", "planned_km", "drops_per_hour",
              "geofence_arrivals", "pod", "stops", "routes", "all_completed", "date"]:
        assert k in d, k
    assert d["date"] == day(0) and d["routes"]
    assert api.get(f"{API}/drivers/nope/summary").status_code == 404
    pytest.sum0 = d


def test_summary_after_deliver_and_drive(api, r101):
    before = pytest.sum0
    s = pending_stop(api, r101["id"])[0]
    r = api.post(f"{API}/stops/{s['id']}/deliver", json={"signature": "data:image/png;base64,iVBORw0KGgo="})
    assert r.status_code == 200, r.text
    for _ in range(3):
        assert api.post(f"{API}/drivers/drv-1/simulate-step", json={"meters": 300}).status_code == 200
    d = api.get(f"{API}/drivers/drv-1/summary").json()
    assert d["delivered"] == before["delivered"] + 1
    assert d["remaining"] == before["remaining"] - 1
    assert d["on_time_rate"] is not None and 0 <= d["on_time_rate"] <= 100
    assert d["pod"]["signature"] >= 1
    assert d["km_driven"] > before["km_driven"], (before["km_driven"], d["km_driven"])
    row = [x for x in d["stops"] if x["tracking_code"] == s["tracking_code"]][0]
    assert row["status"] == "delivered"


def test_summary_future_date_empty(api):
    d = api.get(f"{API}/drivers/drv-1/summary", params={"date": day(1)}).json()
    assert d["routes"] == [] and d["delivered"] == 0 and d["on_time_rate"] is None


# --- Labels
def _labels_url(api, rid):
    # labels.html now needs a short-lived token from the admin-only labels-link endpoint
    lk = api.post(f"{API}/routes/{rid}/labels-link")
    assert lk.status_code == 200, lk.text
    return f"{BASE_URL}{lk.json()['path']}"


def test_labels_html(api, r101):
    url = _labels_url(api, r101['id'])
    r = api.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<svg" in r.text
    open_ = [x for x in api.get(f"{API}/routes/{r101['id']}").json()["stops"] if x["status"] not in ("delivered", "failed")]
    for x in open_:
        assert x["tracking_code"] in r.text
    assert r.text.count('class="label"') == len(open_)
    assert "window.print" not in r.text
    assert "window.print" in api.get(url, params={"autoprint": 1}).text
    assert api.get(f"{API}/routes/nope/labels.html").status_code == 401  # no token
    assert api.post(f"{API}/routes/nope/labels-link").status_code == 404
