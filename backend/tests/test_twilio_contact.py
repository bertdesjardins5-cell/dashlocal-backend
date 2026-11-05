"""Twilio masked contact + phone editing tests.
WARNING: test_sms_real_send performs ONE real Twilio SMS (costs money) to a seeded fictional +1519555xxxx number.
Never add tests for channel='call' against an assigned stop (would ring a real phone).
Set SKIP_TWILIO_SEND=1 to skip the real send.
"""
import os
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    assert s.post(f"{API}/admin/reset").status_code == 200
    return s


@pytest.fixture(scope="module")
def stop(api):
    rd = api.get(f"{API}/drivers/drv-1/route").json()["route"]
    return rd["stops"][0]


# ---- contact validation (no Twilio cost)
def test_contact_invalid_channel(api, stop):
    r = api.post(f"{API}/stops/{stop['id']}/contact", json={"channel": "fax"})
    assert r.status_code == 400
    assert "channel" in r.json()["detail"]


def test_contact_stop_404(api):
    r = api.post(f"{API}/stops/nope-123/contact", json={"channel": "sms"})
    assert r.status_code == 404


def test_call_unassigned_stop_400_no_driver(api):
    # unassigned stop -> no driver -> 400 BEFORE any Twilio call is placed
    zones = api.get(f"{API}/zones").json()
    if not zones:
        pytest.skip("no unassigned orders")
    sid = zones[0]["orders"][0]["id"]
    r = api.post(f"{API}/stops/{sid}/contact", json={"channel": "call"})
    assert r.status_code == 400
    assert "Driver phone" in r.json()["detail"]


# ---- phone editing
def test_customer_phone_invalid(api, stop):
    for bad in ["5195550123", "abc", "+0123", ""]:
        r = api.put(f"{API}/stops/{stop['id']}/phone", json={"phone": bad})
        assert r.status_code == 400, bad


def test_customer_phone_normalized_and_persisted(api, stop):
    r = api.put(f"{API}/stops/{stop['id']}/phone", json={"phone": "+1 (519) 555-0123"})
    assert r.status_code == 200
    assert r.json()["customer_phone"] == "+15195550123"
    assert api.get(f"{API}/stops/{stop['id']}").json()["customer_phone"] == "+15195550123"


def test_customer_phone_404(api):
    assert api.put(f"{API}/stops/nope-123/phone", json={"phone": "+15195550123"}).status_code == 404


def test_driver_phone_invalid(api):
    r = api.put(f"{API}/drivers/drv-1/phone", json={"phone": "555-0101"})
    assert r.status_code == 400


def test_driver_phone_normalized_and_persisted(api):
    r = api.put(f"{API}/drivers/drv-1/phone", json={"phone": "+1 (519) 555-0101"})
    assert r.status_code == 200
    assert r.json()["phone"] == "+15195550101"
    drivers = api.get(f"{API}/drivers").json()
    d = next(x for x in drivers if x["id"] == "drv-1")
    assert d["phone"] == "+15195550101"


def test_driver_phone_404(api):
    assert api.put(f"{API}/drivers/drv-x/phone", json={"phone": "+15195550101"}).status_code == 404


# ---- ONE real SMS send
@pytest.mark.skipif(os.environ.get("SKIP_TWILIO_SEND") == "1", reason="real Twilio send skipped")
def test_sms_real_send(api, stop):
    phone = api.get(f"{API}/stops/{stop['id']}").json()["customer_phone"]
    assert phone.startswith("+1519555"), "safety: only send to fictional seeded numbers"
    r = api.post(f"{API}/stops/{stop['id']}/contact", json={"channel": "sms", "message": "TEST_ DashLocal test message"})
    print("SMS response:", r.status_code, r.text[:300])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "sms"
    assert body["proxy_number"].startswith("+")
    assert body["twilio_sid"].startswith("SM")
    assert body["status"] in ("queued", "accepted", "sending", "sent")
    assert "_id" not in body
