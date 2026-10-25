"""Iteration 4: Parcel QR proof-of-delivery + delivery time windows + time-window-aware optimizer."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
TZ = ZoneInfo("America/Toronto")


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    assert s.post(f"{API}/admin/reset").status_code == 200
    yield s
    s.post(f"{API}/admin/reset")


def r101(api):
    r = api.get(f"{API}/drivers/drv-1/route")
    assert r.status_code == 200
    d = r.json()
    return d.get("route", d)


def get_route(api, rid):
    r = api.get(f"{API}/routes/{rid}")
    assert r.status_code == 200
    return r.json()


# --- Seed windows
def test_seed_windows(api):
    h = datetime.now(TZ).hour
    stops = {s["tracking_code"]: s for s in r101(api)["stops"]}
    if h + 1 <= 23:
        assert stops["MD1006"]["window_start"] == f"{h:02d}:00"
        assert stops["MD1006"]["window_end"] == f"{h + 1:02d}:00"
    if h + 3 <= 23:
        assert stops["MD1002"]["window_start"] == f"{h + 2:02d}:00"


# --- Route detail fields
def test_route_detail_eta_fields(api):
    r = r101(api)
    d = get_route(api, r["id"])
    assert "late_stops" in d and isinstance(d["late_stops"], int)
    for s in d["stops"]:
        assert "planned_eta" in s and "late_min" in s
        if s["status"] not in ("delivered", "failed"):
            assert s["planned_eta"] is not None


# --- QR verify
def test_verify_qr_wrong_and_right(api):
    stops = r101(api)["stops"]
    s = [x for x in stops if x["status"] == "pending"][0]
    bad = api.post(f"{API}/stops/{s['id']}/verify-qr", json={"code": "MD9999"})
    assert bad.status_code == 400
    assert "Wrong parcel" in bad.json()["detail"]
    assert api.get(f"{API}/stops/{s['id']}").json()["pod_qr_verified"] is False
    ok = api.post(f"{API}/stops/{s['id']}/verify-qr", json={"code": f"multidrop:{s['tracking_code'].lower()}"})
    assert ok.status_code == 200
    assert ok.json()["pod_qr_verified"] is True
    assert api.get(f"{API}/stops/{s['id']}").json()["pod_qr_verified"] is True


def test_verify_qr_plain_code_and_404(api):
    stops = r101(api)["stops"]
    s = [x for x in stops if x["status"] == "pending"][1]
    ok = api.post(f"{API}/stops/{s['id']}/verify-qr", json={"code": f"  {s['tracking_code']} "})
    assert ok.status_code == 200 and ok.json()["pod_qr_verified"] is True
    assert api.post(f"{API}/stops/nope/verify-qr", json={"code": "MD1001"}).status_code == 404
    assert api.post(f"{API}/stops/{s['id']}/verify-qr", json={}).status_code == 422


# --- Deliver with QR-only PoD vs no PoD
def test_deliver_qr_only_and_no_pod(api):
    stops = r101(api)["stops"]
    pend = [x for x in stops if x["status"] == "pending"]
    verified = [x for x in pend if api.get(f"{API}/stops/{x['id']}").json()["pod_qr_verified"]]
    unverified = [x for x in pend if x not in verified]
    assert verified and unverified
    r = api.post(f"{API}/stops/{unverified[0]['id']}/deliver", json={})
    assert r.status_code == 400
    r = api.post(f"{API}/stops/{verified[0]['id']}/deliver", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "delivered" and body["pod_qr_verified"] is True
    assert body["pod_photo_path"] is None and body["pod_signature"] is None
    # verify after delivered -> 400
    assert api.post(f"{API}/stops/{verified[0]['id']}/verify-qr", json={"code": verified[0]["tracking_code"]}).status_code == 400


# --- Regression: signature-only drop
def test_signature_only_drop(api):
    stops = r101(api)["stops"]
    s = [x for x in stops if x["status"] == "pending" and not api.get(f"{API}/stops/{x['id']}").json()["pod_qr_verified"]][0]
    r = api.post(f"{API}/stops/{s['id']}/deliver", json={"signature": "M0,0 L10,10"})
    assert r.status_code == 200 and r.json()["status"] == "delivered"


# --- Window validation
@pytest.mark.parametrize("payload", [
    {"start": "15:00", "end": "14:00"},
    {"start": "14:00", "end": "14:00"},
    {"start": "9:00", "end": "11:00"},
    {"start": "25:00", "end": "26:00"},
    {"start": "10:00"},
    {"start": "ab:cd", "end": "11:00"},
])
def test_window_validation(api, payload):
    s = [x for x in r101(api)["stops"] if x["status"] == "pending"][0]
    r = api.put(f"{API}/stops/{s['id']}/window", json=payload)
    assert r.status_code == 400, f"{payload} -> {r.status_code} {r.text}"


def test_window_set_and_clear(api):
    s = [x for x in r101(api)["stops"] if x["status"] == "pending"][0]
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": "10:00", "end": "22:00"})
    assert r.status_code == 200 and r.json()["window_start"] == "10:00" and r.json()["window_end"] == "22:00"
    assert api.get(f"{API}/stops/{s['id']}").json()["window_end"] == "22:00"
    r = api.put(f"{API}/stops/{s['id']}/window", json={"start": None, "end": None})
    assert r.status_code == 200
    g = api.get(f"{API}/stops/{s['id']}").json()
    assert g["window_start"] is None and g["window_end"] is None
    assert api.put(f"{API}/stops/nope/window", json={"start": None, "end": None}).status_code == 404


# --- Track payload
def test_track_window_and_late(api):
    s = [x for x in r101(api)["stops"] if x["status"] == "pending"][0]
    api.put(f"{API}/stops/{s['id']}/window", json={"start": "10:00", "end": "22:00"})
    t = api.get(f"{API}/track/{s['tracking_code']}")
    assert t.status_code == 200
    d = t.json()
    assert "late_min" in d and isinstance(d["late_min"], int)
    assert d["stop"]["window_start"] == "10:00" and d["stop"]["window_end"] == "22:00"


# --- Time-window-aware optimizer
def test_optimize_moves_urgent_stop_earlier(api):
    h = datetime.now(TZ).hour
    if h >= 23:
        pytest.skip("SF hour 23 – window h..h+1 cannot be expressed")
    api.post(f"{API}/admin/reset")
    route = r101(api)
    rid = route["id"]
    # clear all windows, then set a tight window on the last stop
    for s in route["stops"]:
        api.put(f"{API}/stops/{s['id']}/window", json={"start": None, "end": None})
    route = get_route(api, rid)
    last = sorted(route["stops"], key=lambda x: x["seq"])[-1]
    win = {"start": f"{h:02d}:00", "end": f"{h + 1:02d}:00"}
    # make window tight: end ~ now + 10 min to force lateness at the end of the route
    now = datetime.now(TZ)
    end_min = now.minute + 12
    end_h, end_m = h + end_min // 60, end_min % 60
    win = {"start": f"{h:02d}:00", "end": f"{end_h:02d}:{end_m:02d}"}
    assert api.put(f"{API}/stops/{last['id']}/window", json=win).status_code == 200
    before = get_route(api, rid)
    last_b = next(s for s in before["stops"] if s["id"] == last["id"])
    print("before late_stops", before["late_stops"], "last late_min", last_b["late_min"], "seq", last_b["seq"])
    r = api.post(f"{API}/routes/{rid}/optimize")
    assert r.status_code == 200
    after = r.json()
    last_a = next(s for s in after["stops"] if s["id"] == last["id"])
    print("after late_stops", after["late_stops"], "seq", last_a["seq"], "late", last_a["late_min"])
    assert last_a["seq"] < last_b["seq"]
    assert after["late_stops"] == 0
    # persisted
    again = get_route(api, rid)
    assert next(s for s in again["stops"] if s["id"] == last["id"])["seq"] == last_a["seq"]
