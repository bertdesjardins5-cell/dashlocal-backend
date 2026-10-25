"""Iteration 12: admin-only console endpoints, token-gated label sheet, ws/admin token, weekly merchant report email."""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests
import websockets

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
WS = BASE_URL.replace("http", "ws", 1) + "/api/ws/admin"
TZ = ZoneInfo("America/Toronto")
_raw = requests.Session.request  # bypass conftest auto-admin injection for gating checks


def raw(method, url, **kw):
    return _raw(requests.Session(), method, url, **kw)


def login(email, pw):
    r = raw("POST", f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def toks():
    return {"admin": login("admin@dashlocal.app", "DashLocalAdmin2026"),
            "m1": login("bakery@dashlocal.app", "Bakery2026"),
            "m2": login("books@dashlocal.app", "Books2026")}


@pytest.fixture(scope="module")
def ids(toks):
    raw("POST", f"{API}/admin/reset", headers=H(toks["admin"]))
    routes = raw("GET", f"{API}/routes", headers=H(toks["admin"])).json()
    rid = routes[0]["id"]
    route = raw("GET", f"{API}/routes/{rid}", headers=H(toks["admin"])).json()
    rid2 = routes[1]["id"] if len(routes) > 1 else "other-route"
    return {"rid": rid, "rid2": rid2, "stop": route["stops"][0]["id"], "drv": route.get("driver_id") or "drv-1"}


ADMIN_EPS = [
    ("GET", "/analytics", None), ("GET", "/routes", None), ("GET", "/zones", None), ("GET", "/scorecards", None),
    ("POST", "/routes/batch", {"zone": "mission", "driver_id": "drv-3"}), ("PUT", "/routes/{rid}/sequence", {"stop_ids": []}),
    ("POST", "/routes/{rid}/optimize", None), ("GET", "/alerts/trends", None), ("GET", "/alerts/low-ratings", None),
    ("POST", "/stops/{stop}/follow-up", {"resolved": True}), ("GET", "/codes/apology", None),
    ("POST", "/codes/redeem", {"code": "X"}), ("POST", "/drivers/{drv}/coaching", {"note": "x"}),
    ("GET", "/digest", None), ("POST", "/admin/reset", None), ("POST", "/routes/{rid}/labels-link", None),
]


# --- Admin-only endpoints: 401 without token, 403 with merchant token
class TestAdminGating:
    def test_no_token_401_merchant_403(self, toks, ids):
        bad = []
        for m, p, body in ADMIN_EPS:
            url = API + p.format(**ids)
            r0 = raw(m, url, json=body)
            r1 = raw(m, url, json=body, headers=H(toks["m1"]))
            if r0.status_code != 401 or r1.status_code != 403:
                bad.append((m, p, r0.status_code, r1.status_code))
        assert not bad, bad

    def test_admin_ok(self, toks, ids):
        for m, p in [("GET", "/analytics"), ("GET", "/routes"), ("GET", "/zones"), ("GET", "/scorecards"),
                     ("GET", "/alerts/trends"), ("GET", "/alerts/low-ratings"), ("GET", "/codes/apology"), ("GET", "/digest")]:
            r = raw(m, API + p, headers=H(toks["admin"]))
            assert r.status_code == 200, (p, r.status_code, r.text[:200])

    def test_open_endpoints_still_open(self, ids):
        # iteration 13: routes/drivers are now driver/admin-gated; only customer tracking stays open
        for p in ["/track/MD1001"]:
            r = raw("GET", API + p)
            assert r.status_code == 200, (p, r.status_code, r.text[:200])


# --- Labels print link
class TestLabels:
    def test_labels_token(self, toks, ids):
        rid, rid2 = ids["rid"], ids["rid2"]
        assert raw("GET", f"{API}/routes/{rid}/labels.html").status_code == 401
        assert raw("GET", f"{API}/routes/{rid}/labels.html?token=garbage").status_code == 401
        lk = raw("POST", f"{API}/routes/{rid}/labels-link", headers=H(toks["admin"]))
        assert lk.status_code == 200 and "token=" in lk.json()["path"]
        path = lk.json()["path"]
        r = raw("GET", BASE_URL + path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "LOAD 01" in r.text
        other = path.replace(f"/routes/{rid}/", f"/routes/{rid2}/")
        assert raw("GET", BASE_URL + other).status_code == 403
        # a session token must not be accepted as a labels token
        assert raw("GET", f"{API}/routes/{rid}/labels.html?token={toks['admin']}").status_code == 401


# --- ws/admin requires admin session token
class TestWsAdmin:
    def _try(self, url):
        async def go():
            try:
                async with websockets.connect(url, open_timeout=10) as ws:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=2)
                    except asyncio.TimeoutError:
                        return "open"
                    return "open"
            except Exception as e:
                return f"closed:{type(e).__name__}"
        return asyncio.run(go())

    def test_ws_gating(self, toks):
        assert self._try(WS).startswith("closed")
        assert self._try(f"{WS}?token={toks['m1']}").startswith("closed")
        assert self._try(f"{WS}?token={toks['admin']}") == "open"


# --- Weekly email settings
class TestWeeklyEmail:
    def test_access(self, toks):
        assert raw("GET", f"{API}/merchants/mer-1/weekly-email").status_code == 401
        assert raw("GET", f"{API}/merchants/mer-1/weekly-email", headers=H(toks["m2"])).status_code == 403
        assert raw("PUT", f"{API}/merchants/mer-1/weekly-email", json={"enabled": False}, headers=H(toks["m2"])).status_code == 403
        assert raw("POST", f"{API}/merchants/mer-1/weekly-email/send-now", headers=H(toks["m2"])).status_code == 403
        assert raw("GET", f"{API}/merchants/mer-1/weekly-email", headers=H(toks["admin"])).status_code == 200

    def test_status_shape_next_monday(self, toks):
        j = raw("GET", f"{API}/merchants/mer-1/weekly-email", headers=H(toks["m1"])).json()
        assert isinstance(j["enabled"], bool) and "bakery@dashlocal.app" in j["recipients"] and "last" in j
        nxt = datetime.fromisoformat(j["next_send"]).astimezone(TZ)
        assert nxt.weekday() == 0 and nxt.hour == 8 and nxt.minute == 0
        assert nxt > datetime.now(TZ) and (nxt - datetime.now(TZ)).days < 7

    def test_toggle_persists(self, toks):
        h = H(toks["m2"])
        r = raw("PUT", f"{API}/merchants/mer-2/weekly-email", json={"enabled": False}, headers=h)
        assert r.status_code == 200 and r.json()["enabled"] is False
        assert raw("GET", f"{API}/merchants/mer-2/weekly-email", headers=h).json()["enabled"] is False
        assert raw("PUT", f"{API}/merchants/mer-2/weekly-email", json={"enabled": "nope"}, headers=h).status_code == 422
        r = raw("PUT", f"{API}/merchants/mer-2/weekly-email", json={"enabled": True}, headers=h)
        assert r.json()["enabled"] is True

    def test_send_now_real_and_cooldown(self, toks):
        h = H(toks["m2"])
        r = raw("POST", f"{API}/merchants/mer-2/weekly-email/send-now", headers=h, timeout=60)
        if r.status_code == 429:
            pytest.skip("mer-2 send-now on cooldown from a previous run")
        assert r.status_code == 200, r.text
        s = r.json()["sent"]
        assert s["status"] == "sent", s
        assert s["trigger"] == "manual" and s["recipients"]
        assert all(x["to"] == "delivered@resend.dev" and x["status"] == "sent" and x["email_id"] for x in s["recipients"])
        assert r.json()["last"]["id"] == s["id"]
        r2 = raw("POST", f"{API}/merchants/mer-2/weekly-email/send-now", headers=h)
        assert r2.status_code == 429

    def test_lastweek_report_link(self, toks):
        r = raw("POST", f"{API}/merchants/mer-1/report-link", json={"range": "lastweek"}, headers=H(toks["m1"]))
        assert r.status_code == 200, r.text
        c = raw("GET", BASE_URL + r.json()["path"])
        assert c.status_code == 200 and c.text.splitlines()[0].startswith(("tracking", "Tracking", "\ufeff"))
