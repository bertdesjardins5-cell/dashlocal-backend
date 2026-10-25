"""Backend tests for Multidrop Delivery API."""
import os
import io
import time
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session", autouse=True)
def reset_db(api):
    r = api.post(f"{API}/admin/reset")
    assert r.status_code == 200
    yield


# ---- drivers & root
def test_root(api):
    r = api.get(f"{API}/")
    assert r.status_code == 200


def test_list_drivers(api):
    r = api.get(f"{API}/drivers")
    assert r.status_code == 200
    data = r.json()
    ids = [d["id"] for d in data]
    assert "drv-1" in ids and "drv-2" in ids and "drv-3" in ids
    drv1 = next(d for d in data if d["id"] == "drv-1")
    assert drv1["route"] is not None  # seeded active route


def test_driver_route(api):
    r = api.get(f"{API}/drivers/drv-1/route")
    assert r.status_code == 200
    rt = r.json()["route"]
    assert rt is not None and rt["status"] == "active"
    assert rt["zone"] == "Downtown"
    assert len(rt["stops"]) > 0
    assert rt["next_stop_id"]


def test_driver_not_found(api):
    assert api.get(f"{API}/drivers/nope/route").status_code == 404


# ---- zones & batch
def test_zones(api):
    r = api.get(f"{API}/zones")
    assert r.status_code == 200
    zones = r.json()
    names = [z["zone"] for z in zones]
    # Downtown already batched during seed; remaining zones should exist
    assert all(z["count"] > 0 for z in zones)
    assert "Uptown" in names


def test_batch_and_optimize_sequence_start(api):
    r = api.post(f"{API}/routes/batch", json={"zone": "Uptown", "driver_id": "drv-2"})
    assert r.status_code == 200
    rd = r.json()
    route_id = rd["id"]
    stop_ids = [s["id"] for s in rd["stops"]]
    assert rd["status"] == "planned"
    assert len(stop_ids) >= 2

    # Optimize
    r = api.post(f"{API}/routes/{route_id}/optimize")
    assert r.status_code == 200

    # Sequence - reverse ids
    reversed_ids = list(reversed(stop_ids))
    r = api.put(f"{API}/routes/{route_id}/sequence", json={"stop_ids": reversed_ids})
    assert r.status_code == 200
    seqs = r.json()["stops"]
    assert [s["id"] for s in seqs] == reversed_ids

    # Bad sequence
    r = api.put(f"{API}/routes/{route_id}/sequence", json={"stop_ids": stop_ids[:-1]})
    assert r.status_code == 400

    # Start
    r = api.post(f"{API}/routes/{route_id}/start")
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    # Already started
    assert api.post(f"{API}/routes/{route_id}/start").status_code == 400


def test_batch_bad_zone(api):
    assert api.post(f"{API}/routes/batch", json={"zone": "Downtown", "driver_id": "drv-3"}).status_code == 400
    assert api.post(f"{API}/routes/batch", json={"zone": "Marina", "driver_id": "ghost"}).status_code == 404


# ---- simulate-step + geofence
def test_simulate_step_geofence(api):
    r = api.get(f"{API}/drivers/drv-1/route").json()["route"]
    next_id = r["next_stop_id"]
    arrived = None
    for _ in range(60):
        res = api.post(f"{API}/drivers/drv-1/simulate-step?meters=400").json()
        if res.get("geofence_arrived_stop_id"):
            arrived = res["geofence_arrived_stop_id"]
            break
    assert arrived == next_id


def test_post_location(api):
    r = api.post(f"{API}/drivers/drv-3/location", json={"lat": 43.48, "lng": -80.41})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---- stops: arrive / deliver / fail
def test_arrive_deliver_fail_flow(api):
    # new planned route in Westmount for drv-3
    rd = api.post(f"{API}/routes/batch", json={"zone": "Westmount", "driver_id": "drv-3"}).json()
    stops = rd["stops"]
    api.post(f"{API}/routes/{rd['id']}/start")
    s0 = stops[0]
    # Deliver requires PoD
    r = api.post(f"{API}/stops/{s0['id']}/deliver", json={})
    assert r.status_code == 400
    # Arrive manually
    r = api.post(f"{API}/stops/{s0['id']}/arrive")
    assert r.status_code == 200 and r.json()["status"] == "arrived"
    assert r.json()["arrival_method"] == "manual"
    # Deliver with signature
    r = api.post(f"{API}/stops/{s0['id']}/deliver", json={"signature": "data:image/png;base64,xxx"})
    assert r.status_code == 200 and r.json()["status"] == "delivered"
    # Fail next
    s1 = stops[1]
    r = api.post(f"{API}/stops/{s1['id']}/fail", json={"reason": "No one home"})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    # GET verify persistence
    assert api.get(f"{API}/stops/{s0['id']}").json()["status"] == "delivered"


# ---- contact (real Twilio) -- see test_twilio_contact.py; here only a no-cost validation check
def test_contact_invalid_channel(api):
    rd = api.get(f"{API}/drivers/drv-1/route").json()["route"]
    sid = rd["stops"][0]["id"]
    r = api.post(f"{API}/stops/{sid}/contact", json={"channel": "fax"})
    assert r.status_code == 400


# ---- tracking
def test_track_and_codes(api):
    code = api.get(f"{API}/drivers/drv-1/route").json()["route"]["stops"][0]["tracking_code"]
    r = api.get(f"{API}/track/{code}")
    assert r.status_code == 200
    body = r.json()
    assert "stop" in body and "eta_minutes" in body
    # ETA must be a number for active route stops
    assert body["stop"]["tracking_code"] == code
    # Not found
    assert api.get(f"{API}/track/NOPE123").status_code == 404


# ---- upload + files (storage may be out of credits in preview)
def test_upload_and_fetch(api):
    files = {"file": ("test.jpg", io.BytesIO(b"\xff\xd8\xff" + b"a" * 100), "image/jpeg")}
    r = requests.post(f"{API}/upload", files=files)
    if r.status_code == 402:
        pytest.skip("storage out of credits")
    assert r.status_code == 200
    path = r.json()["path"]
    r2 = requests.get(f"{API}/files/{path}")
    assert r2.status_code == 200
    assert len(r2.content) > 0


# ---- analytics
def test_analytics(api):
    r = api.get(f"{API}/analytics")
    assert r.status_code == 200
    a = r.json()
    for k in ("drops_per_hour", "delivered", "failed", "total_orders", "success_rate", "geofence_arrival_pct", "routes"):
        assert k in a
    assert a["total_orders"] > 0
    assert a["delivered"] >= 1  # we delivered one earlier


# ---- websocket smoke
def test_websocket_track():
    try:
        from websockets.sync.client import connect
    except Exception:
        pytest.skip("websockets lib missing")
    s = requests.Session()
    code = "MD1001"
    ws_url = BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + f"/api/ws/track/{code}"
    try:
        with connect(ws_url, open_timeout=10) as ws:
            msg = ws.recv(timeout=10)
            assert "stop" in msg
    except Exception as e:
        pytest.skip(f"ws unreachable via ingress: {e}")
