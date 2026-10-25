"""Iteration 13: driver phone+PIN login, driver authorization, admin PIN management, merchant problem alerts."""
import asyncio
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests
import websockets
from pymongo import MongoClient

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
TZ = ZoneInfo("America/Toronto")
_raw = requests.Session.request  # bypass conftest auto-admin injection
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
PINS = {"drv-1": ("+15195550101", "482913"), "drv-2": ("+15195550102", "730516"), "drv-3": ("+15195550103", "264871")}
SIG = "data:image/png;base64,iVBORw0KGgo="


def raw(method, url, **kw):
    return _raw(requests.Session(), method, url, **kw)


def H(t):
    return {"Authorization": f"Bearer {t}"}


def login(email, pw):
    r = raw("POST", f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def dlogin(phone, pin):
    return raw("POST", f"{API}/auth/driver-login", json={"phone": phone, "pin": pin})


@pytest.fixture(scope="module")
def toks():
    admin = login("admin@dashlocal.app", "DashLocalAdmin2026")
    assert raw("POST", f"{API}/admin/reset", headers=H(admin)).status_code == 200
    for d, (_, pin) in PINS.items():  # make sure demo PINs are in place / lockouts cleared
        raw("PUT", f"{API}/admin/drivers/{d}/pin", json={"pin": pin}, headers=H(admin))
    t = {"admin": admin, "m1": login("bakery@dashlocal.app", "Bakery2026"), "m2": login("books@dashlocal.app", "Books2026")}
    for d, (ph, pin) in PINS.items():
        r = dlogin(ph, pin)
        assert r.status_code == 200, r.text
        t[d] = r.json()["token"]
    return t


# ---------------- Driver login
class TestDriverLogin:
    def test_formats_and_me(self, toks):
        r = dlogin("(519) 555-0101", "482913")
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["role"] == "driver" and u["driver_id"] == "drv-1" and u["driver_name"]
        me = raw("GET", f"{API}/auth/me", headers=H(r.json()["token"])).json()
        me_s = str(me)
        assert "pin_hash" not in me_s and "password_hash" not in me_s
        m = me.get("user", me)
        assert m["role"] == "driver" and m["driver_id"] == "drv-1" and m.get("driver_name"), me

    def test_wrong_pin_unknown_phone_generic(self, toks):
        a = dlogin("+15195550101", "000001")
        b = dlogin("+15195559999", "482913")
        assert a.status_code == 401 and b.status_code == 401
        assert a.json()["detail"] == b.json()["detail"] == "Wrong phone number or PIN"
        assert dlogin("+15195550101", "482913").status_code == 200  # success clears counter

    def test_lockout_drv3_then_admin_reset(self, toks):
        codes = [dlogin("+15195550103", "999990").status_code for _ in range(5)]
        assert codes == [401] * 5, codes
        r = dlogin("+15195550103", "264871")  # correct PIN is still locked out
        assert r.status_code == 429, r.text
        assert raw("PUT", f"{API}/admin/drivers/drv-3/pin", json={"pin": "264871"}, headers=H(toks["admin"])).status_code == 200
        assert dlogin("+15195550103", "264871").status_code == 200


# ---------------- Admin PIN management
class TestAdminPins:
    def test_accounts_list(self, toks):
        r = raw("GET", f"{API}/admin/driver-accounts", headers=H(toks["admin"]))
        assert r.status_code == 200
        rows = {x["id"]: x for x in r.json()}
        assert {"drv-1", "drv-2", "drv-3"} <= set(rows)
        assert rows["drv-1"]["has_pin"] is True and rows["drv-1"]["last_login_at"]
        assert "pin_hash" not in str(r.json())
        assert raw("GET", f"{API}/admin/driver-accounts").status_code == 401
        assert raw("GET", f"{API}/admin/driver-accounts", headers=H(toks["drv-1"])).status_code == 403
        assert raw("GET", f"{API}/admin/driver-accounts", headers=H(toks["m1"])).status_code == 403

    def test_pin_validation_and_change(self, toks):
        h = H(toks["admin"])
        for bad in ["12345", "1234567", "abcdef", "123456", "111111"]:
            r = raw("PUT", f"{API}/admin/drivers/drv-2/pin", json={"pin": bad}, headers=h)
            assert r.status_code == 400, (bad, r.status_code)
        assert raw("PUT", f"{API}/admin/drivers/ghost/pin", json={"pin": "583920"}, headers=h).status_code == 404
        assert raw("PUT", f"{API}/admin/drivers/drv-2/pin", json={"pin": "583920"}, headers=H(toks["drv-1"])).status_code == 403
        try:
            assert raw("PUT", f"{API}/admin/drivers/drv-2/pin", json={"pin": "583920"}, headers=h).status_code == 200
            assert dlogin("+15195550102", "583920").status_code == 200
            assert dlogin("+15195550102", "730516").status_code == 401
        finally:
            assert raw("PUT", f"{API}/admin/drivers/drv-2/pin", json={"pin": "730516"}, headers=h).status_code == 200
        assert dlogin("+15195550102", "730516").status_code == 200


# ---------------- Driver authorization
@pytest.fixture(scope="module")
def ctx(toks):
    a = H(toks["admin"])
    r1 = raw("GET", f"{API}/drivers/drv-1/route", headers=a).json()
    route1 = r1.get("route") or r1
    rid1 = route1["id"]
    det1 = raw("GET", f"{API}/routes/{rid1}", headers=a).json()
    other = next((x for x in raw("GET", f"{API}/routes", headers=a).json() if x.get("driver_id") and x["driver_id"] != "drv-1"), None)
    if not other:  # batch some unassigned orders to drv-2 so we have a foreign route
        for z in raw("GET", f"{API}/zones", headers=a).json():
            zn = z.get("zone") or z.get("name") or z.get("id")
            b = raw("POST", f"{API}/routes/batch", json={"zone": zn, "driver_id": "drv-2"}, headers=a)
            if b.status_code == 200:
                other = b.json()
                break
    rid2 = other["id"] if other else None
    stop2 = None
    if rid2:
        stop2 = raw("GET", f"{API}/routes/{rid2}", headers=a).json()["stops"][0]["id"]
    return {"rid1": rid1, "stops1": det1["stops"], "rid2": rid2, "stop2": stop2, "drv2": other["driver_id"] if other else "drv-2"}


class TestDriverAuthz:
    def test_own_driver_endpoints(self, toks):
        h = H(toks["drv-1"])
        bad = []
        for m, p, body in [("GET", "/drivers/drv-1/route", None), ("GET", "/drivers/drv-1/summary", None),
                           ("GET", "/drivers/drv-1/scorecard", None), ("GET", "/drivers/drv-1/badges/new", None),
                           ("POST", "/drivers/drv-1/badges/seen", {"keys": []}), ("GET", "/drivers/drv-1/coaching", None),
                           ("POST", "/drivers/drv-1/location", {"lat": 43.46, "lng": -80.42}),
                           ("POST", "/drivers/drv-1/simulate-step", None),
                           ("PUT", "/drivers/drv-1/phone", {"phone": "+15195550101"})]:
            r = raw(m, API + p, json=body, headers=h)
            if r.status_code != 200:
                bad.append((m, p, r.status_code, r.text[:120]))
        assert not bad, bad

    def test_other_driver_and_admin_403(self, toks):
        h = H(toks["drv-1"])
        bad = []
        for m, p, body in [("GET", "/drivers/drv-2/route", None), ("GET", "/drivers/drv-2/summary", None),
                           ("GET", "/drivers/drv-2/scorecard", None), ("GET", "/drivers/drv-2/badges/new", None),
                           ("POST", "/drivers/drv-2/badges/seen", {"keys": []}), ("GET", "/drivers/drv-2/coaching", None),
                           ("POST", "/drivers/drv-2/location", {"lat": 43.46, "lng": -80.42}),
                           ("POST", "/drivers/drv-2/simulate-step", None),
                           ("PUT", "/drivers/drv-2/phone", {"phone": "+15195550109"}),
                           ("GET", "/analytics", None), ("GET", "/drivers", None), ("GET", "/routes", None)]:
            r = raw(m, API + p, json=body, headers=h)
            if r.status_code != 403:
                bad.append((m, p, r.status_code))
        assert not bad, bad

    def test_no_token_401(self, ctx):
        bad = []
        sid = ctx["stops1"][0]["id"]
        for m, p in [("GET", "/drivers/drv-1/route"), ("GET", f"/routes/{ctx['rid1']}"), ("GET", f"/stops/{sid}"),
                     ("POST", f"/stops/{sid}/arrive"), ("POST", "/upload"), ("GET", "/drivers/drv-1/summary")]:
            r = raw(m, API + p)
            if r.status_code != 401:
                bad.append((m, p, r.status_code))
        assert not bad, bad

    def test_own_route_and_stops(self, toks, ctx):
        h = H(toks["drv-1"])
        assert raw("GET", f"{API}/routes/{ctx['rid1']}", headers=h).status_code == 200
        assert raw("POST", f"{API}/routes/{ctx['rid1']}/update-seen", headers=h).status_code == 200
        st = raw("POST", f"{API}/routes/{ctx['rid1']}/start", headers=h)
        assert st.status_code in (200, 400), st.text  # already active -> 400 is fine, not 403
        pend = [s for s in ctx["stops1"] if s["status"] == "pending"]
        s = pend[0]
        assert raw("GET", f"{API}/stops/{s['id']}", headers=h).status_code == 200
        q = raw("POST", f"{API}/stops/{s['id']}/verify-qr", json={"code": "WRONG"}, headers=h)
        assert q.status_code not in (401, 403), q.status_code
        assert raw("PUT", f"{API}/stops/{s['id']}/phone", json={"phone": s.get("phone") or "+15195550177"}, headers=h).status_code == 200
        assert raw("POST", f"{API}/stops/{s['id']}/arrive", headers=h).status_code == 200
        up = raw("POST", f"{API}/upload", files={"file": ("pod.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")}, headers=h)
        assert up.status_code == 200, up.text[:200]
        path = up.json().get("path") or up.json().get("storage_path")
        dv = raw("POST", f"{API}/stops/{s['id']}/deliver", json={"photo_path": path}, headers=h)
        assert dv.status_code == 200 and dv.json()["status"] == "delivered", dv.text[:200]
        f = raw("POST", f"{API}/stops/{pend[1]['id']}/fail", json={"reason": "TEST no access"}, headers=h)
        assert f.status_code == 200, f.text[:200]

    def test_foreign_route_and_stops_403(self, toks, ctx):
        if not ctx["rid2"]:
            pytest.skip("no route for another driver after reset")
        h = H(toks["drv-1"])
        sid, rid = ctx["stop2"], ctx["rid2"]
        bad = []
        for m, p, body in [("GET", f"/routes/{rid}", None), ("POST", f"/routes/{rid}/start", None),
                           ("POST", f"/routes/{rid}/update-seen", None), ("GET", f"/stops/{sid}", None),
                           ("POST", f"/stops/{sid}/arrive", None), ("POST", f"/stops/{sid}/deliver", {"signature": SIG}),
                           ("POST", f"/stops/{sid}/fail", {"reason": "x"}), ("POST", f"/stops/{sid}/contact", {"channel": "sms"}),
                           ("POST", f"/stops/{sid}/verify-qr", {"code": "x"}), ("PUT", f"/stops/{sid}/phone", {"phone": "+15195550177"})]:
            r = raw(m, API + p, json=body, headers=h)
            if r.status_code != 403:
                bad.append((m, p, r.status_code))
        assert not bad, bad
        # the route's own driver / admin pass
        assert raw("GET", f"{API}/routes/{rid}", headers=H(toks[ctx["drv2"]])).status_code == 200
        assert raw("GET", f"{API}/stops/{sid}", headers=H(toks["admin"])).status_code == 200

    def test_customer_endpoints_need_order_check(self, ctx):
        s = ctx["stops1"][-1]
        assert raw("GET", f"{API}/track/{s['tracking_code']}").status_code == 200
        calls = [("PUT", f"/stops/{s['id']}/window", {"start": "16:00", "end": "18:00"}),
                 ("POST", f"/stops/{s['id']}/rating", {"stars": 5}), ("POST", f"/stops/{s['id']}/share", None)]
        for m, p, body in calls:  # no login and no last-4 check -> blocked
            assert raw(m, API + p, json=body).status_code == 401, p
        last4 = "".join(c for c in s["customer_phone"] if c.isdigit())[-4:]
        tok = raw("POST", f"{API}/track/{s['tracking_code']}/verify", json={"last4": last4}).json()["token"]
        for m, p, body in calls:  # verified customer passes auth (may still get 400 for business rules)
            r = raw(m, API + p, json=body, headers={"X-Order-Token": tok})
            assert r.status_code not in (401, 403), (p, r.status_code)

    def test_ws_route_gating(self, toks, ctx):
        ws = BASE_URL.replace("http", "ws", 1) + f"/api/ws/route/{ctx['rid1']}"

        async def go(url):
            try:
                async with websockets.connect(url, open_timeout=10) as w:
                    try:
                        await asyncio.wait_for(w.recv(), timeout=2)
                    except asyncio.TimeoutError:
                        pass
                    return "open"
            except Exception as e:
                return f"closed:{type(e).__name__}"
        res = {k: asyncio.run(go(u)) for k, u in {"none": ws, "drv1": f"{ws}?token={toks['drv-1']}",
                                                   "drv2": f"{ws}?token={toks['drv-2']}", "m1": f"{ws}?token={toks['m1']}",
                                                   "admin": f"{ws}?token={toks['admin']}"}.items()}
        assert res["drv1"] == "open" and res["admin"] == "open", res
        assert all(res[k].startswith("closed") for k in ("none", "drv2", "m1")), res


# ---------------- Merchant problem alerts (<=3 real emails)
def wait_alert(code, kind, admin, t=25):
    end = time.time() + t
    while time.time() < end:
        rec = [a for a in raw("GET", f"{API}/merchants/mer-1/alerts", headers=H(admin)).json()["recent"]
               if a["tracking_code"] == code and a["kind"] == kind]
        if rec:
            return rec
        time.sleep(1.5)
    return []


@pytest.fixture(scope="module")
def mstops(toks):
    a = H(toks["admin"])
    assert raw("POST", f"{API}/admin/reset", headers=a).status_code == 200
    csv = ("customer_name,phone,address\nTEST_AlertFail,5195550181,200 King St W Kitchener\nTEST_AlertOff,5195550182,200 King St W Kitchener\n"
           "TEST_AlertLate,5195550183,200 King St W Kitchener\nTEST_AlertDLate,5195550184,200 King St W Kitchener\n").encode()
    r = raw("POST", f"{API}/merchants/mer-1/import", headers=H(toks["m1"]), files={"file": ("a.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    jid, end = r.json()["id"], time.time() + 60
    while time.time() < end:
        j = raw("GET", f"{API}/imports/{jid}", headers=a).json()
        if j["status"] == "done":
            break
        time.sleep(1.5)
    assert j["created"] == 4, j
    orders = {o["customer_name"]: o for o in raw("GET", f"{API}/merchants/mer-1", headers=a).json()["orders"] if o["customer_name"].startswith("TEST_Alert")}
    today = raw("GET", f"{API}/digest", headers=a).json()["date"]
    drv = next(d["id"] for d in raw("GET", f"{API}/drivers", headers=a).json() if not d["route"])
    b = raw("POST", f"{API}/routes/batch", json={"zone": orders["TEST_AlertFail"]["zone"], "driver_id": drv, "service_date": today}, headers=a)
    assert b.status_code == 200, b.text
    rid = b.json()["id"]
    dt = dlogin(*PINS[drv]).json()["token"]
    assert raw("POST", f"{API}/routes/{rid}/start", headers=H(dt)).status_code == 200
    return {"orders": orders, "rid": rid, "drv": drv, "dt": dt}


class TestMerchantAlerts:
    def test_access(self, toks):
        assert raw("GET", f"{API}/merchants/mer-1/alerts").status_code == 401
        assert raw("GET", f"{API}/merchants/mer-1/alerts", headers=H(toks["m2"])).status_code == 403
        assert raw("PUT", f"{API}/merchants/mer-1/alerts", json={"enabled": False}, headers=H(toks["m2"])).status_code == 403
        r = raw("GET", f"{API}/merchants/mer-1/alerts", headers=H(toks["m1"]))
        assert r.status_code == 200 and isinstance(r.json()["enabled"], bool) and isinstance(r.json()["recent"], list)

    def test_failed_alert_real_email_once(self, toks, mstops):
        o = mstops["orders"]["TEST_AlertFail"]
        h = H(mstops["dt"])
        raw("PUT", f"{API}/merchants/mer-1/alerts", json={"enabled": True}, headers=H(toks["m1"]))
        assert raw("POST", f"{API}/stops/{o['id']}/fail", json={"reason": "TEST nobody home"}, headers=h).status_code == 200
        rec = wait_alert(o["tracking_code"], "failed", toks["admin"])
        assert len(rec) == 1 and rec[0]["status"] == "sent", rec
        doc = mdb.merchant_alerts.find_one({"stop_id": o["id"], "kind": "failed"})
        assert doc["recipients"] and doc["recipients"][0]["to"] == "delivered@resend.dev" and doc["recipients"][0]["email_id"], doc
        assert raw("POST", f"{API}/stops/{o['id']}/fail", json={"reason": "again"}, headers=h).status_code == 400
        time.sleep(2)
        assert mdb.merchant_alerts.count_documents({"stop_id": o["id"]}) == 1

    def test_alerts_off_no_email(self, toks, mstops):
        o = mstops["orders"]["TEST_AlertOff"]
        r = raw("PUT", f"{API}/merchants/mer-1/alerts", json={"enabled": False}, headers=H(toks["m1"]))
        assert r.status_code == 200 and r.json()["enabled"] is False
        try:
            assert raw("GET", f"{API}/merchants/mer-1/alerts", headers=H(toks["admin"])).json()["enabled"] is False
            assert raw("POST", f"{API}/stops/{o['id']}/fail", json={"reason": "TEST off"}, headers=H(mstops["dt"])).status_code == 200
            rec = wait_alert(o["tracking_code"], "failed", toks["admin"])
            assert len(rec) == 1 and rec[0]["status"] == "off", rec
            doc = mdb.merchant_alerts.find_one({"stop_id": o["id"]})
            assert not doc["recipients"], doc
        finally:
            assert raw("PUT", f"{API}/merchants/mer-1/alerts", json={"enabled": True}, headers=H(toks["admin"])).json()["enabled"] is True

    def test_late_alert_once(self, toks, mstops):
        o = mstops["orders"]["TEST_AlertLate"]
        now = datetime.now(TZ)
        if now.hour < 1:
            pytest.skip("too early in the day to craft an elapsed window")
        mdb.stops.update_one({"id": o["id"]}, {"$set": {"window_start": "00:00", "window_end": "00:30", "window_date": now.date().isoformat()}})
        h = H(mstops["dt"])
        assert raw("POST", f"{API}/drivers/{mstops['drv']}/location", json={"lat": 43.49, "lng": -80.59}, headers=h).status_code == 200
        rec = wait_alert(o["tracking_code"], "late", toks["admin"])
        assert len(rec) == 1 and rec[0]["status"] == "sent" and rec[0]["late_min"] > 5 and rec[0]["eta"], rec
        for _ in range(2):
            raw("POST", f"{API}/drivers/{mstops['drv']}/location", json={"lat": 43.491, "lng": -80.591}, headers=h)
        time.sleep(3)
        assert mdb.merchant_alerts.count_documents({"stop_id": o["id"]}) == 1
        # delivering after a 'late' email must not add a 'delivered_late' one
        assert raw("POST", f"{API}/stops/{o['id']}/deliver", json={"signature": SIG}, headers=h).status_code == 200
        time.sleep(3)
        assert mdb.merchant_alerts.count_documents({"stop_id": o["id"]}) == 1

    def test_delivered_late_alert(self, toks, mstops):
        o = mstops["orders"]["TEST_AlertDLate"]
        now = datetime.now(TZ)
        if now.hour < 1:
            pytest.skip("too early in the day")
        # elapsed window but already claimed for late? ensure no late email was sent before delivering
        mdb.stops.update_one({"id": o["id"]}, {"$set": {"window_start": "00:00", "window_end": "00:30", "window_date": now.date().isoformat()}})
        r = raw("POST", f"{API}/stops/{o['id']}/deliver", json={"signature": SIG}, headers=H(mstops["dt"]))
        assert r.status_code == 200, r.text
        rec = wait_alert(o["tracking_code"], "delivered_late", toks["admin"])
        assert len(rec) == 1 and rec[0]["status"] == "sent", rec
        assert mdb.merchant_alerts.count_documents({"stop_id": o["id"]}) == 1
