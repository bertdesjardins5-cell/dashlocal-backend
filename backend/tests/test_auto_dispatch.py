# Zone-based auto dispatch (N/S/E/W of depot): compass-zone coverage, dispatch preview, auto-dispatch, driver manifests
import math
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
DEPOT = {"lat": 37.7749, "lng": -122.4194}
COMPASS = ("North", "South", "East", "West")
DRIVERS = {"drv-1": ("+14155550101", "482913"), "drv-2": ("+14155550102", "730516"), "drv-3": ("+14155550103", "264871")}
DEFAULT_COV = {"drv-1": "North", "drv-2": "South", "drv-3": "East"}
NO = {"X-No-Auto-Auth": "1"}


def compass(p):
    dy = p["lat"] - DEPOT["lat"]
    dx = (p["lng"] - DEPOT["lng"]) * math.cos(math.radians(DEPOT["lat"]))
    if abs(dy) >= abs(dx):
        return "North" if dy >= 0 else "South"
    return "East" if dx >= 0 else "West"


@pytest.fixture(scope="module")
def admin():
    r = requests.post(f"{API}/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}, headers=NO)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def drv_tokens():
    out = {}
    for did, (phone, pin) in DRIVERS.items():
        r = requests.post(f"{API}/auth/driver-login", json={"phone": phone, "pin": pin}, headers=NO)
        assert r.status_code == 200, r.text
        out[did] = {"Authorization": f"Bearer {r.json()['token']}"}
    return out


def tz_today():
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()


def reset(admin):
    assert requests.post(f"{API}/admin/reset", headers=admin).status_code == 200


def set_cov(admin, cov):
    for did, z in cov.items():
        r = requests.put(f"{API}/admin/drivers/{did}/compass-zone", json={"zone": z}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["compass_zone"] == z


def route(admin, rid):
    r = requests.get(f"{API}/routes/{rid}", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module", autouse=True)
def restore(admin):
    yield
    reset(admin)
    set_cov(admin, DEFAULT_COV)


# ---- auth gating
class TestAuthGating:
    def test_preview_requires_admin(self, drv_tokens):
        assert requests.get(f"{API}/dispatch/preview", headers=NO).status_code == 401
        assert requests.get(f"{API}/dispatch/preview", headers=drv_tokens["drv-1"]).status_code == 403

    def test_auto_dispatch_requires_admin(self, drv_tokens):
        assert requests.post(f"{API}/routes/auto-dispatch", json={}, headers=NO).status_code == 401
        assert requests.post(f"{API}/routes/auto-dispatch", json={}, headers=drv_tokens["drv-2"]).status_code == 403

    def test_compass_zone_requires_admin(self, drv_tokens):
        assert requests.put(f"{API}/admin/drivers/drv-1/compass-zone", json={"zone": "West"}, headers=NO).status_code == 401
        assert requests.put(f"{API}/admin/drivers/drv-1/compass-zone", json={"zone": "West"}, headers=drv_tokens["drv-1"]).status_code == 403


# ---- coverage endpoint
class TestCompassZone:
    def test_invalid_zone_400(self, admin):
        r = requests.put(f"{API}/admin/drivers/drv-1/compass-zone", json={"zone": "Northeast"}, headers=admin)
        assert r.status_code == 400
        r = requests.put(f"{API}/admin/drivers/drv-1/compass-zone", json={"zone": "north"}, headers=admin)
        assert r.status_code == 400

    def test_unknown_driver_404(self, admin):
        r = requests.put(f"{API}/admin/drivers/drv-nope/compass-zone", json={"zone": "West"}, headers=admin)
        assert r.status_code == 404

    def test_set_and_clear_persist(self, admin):
        set_cov(admin, {"drv-3": "West"})
        p = requests.get(f"{API}/dispatch/preview", headers=admin).json()
        assert {d["id"]: d["compass_zone"] for d in p["drivers"]}["drv-3"] == "West"
        assert next(z for z in p["zones"] if z["zone"] == "West")["driver"]["id"] == "drv-3"
        set_cov(admin, {"drv-3": None})
        p = requests.get(f"{API}/dispatch/preview", headers=admin).json()
        assert {d["id"]: d["compass_zone"] for d in p["drivers"]}["drv-3"] is None
        set_cov(admin, {"drv-3": "East"})


# ---- preview
class TestPreview:
    def test_preview_counts_match_eligible(self, admin):
        reset(admin)
        p = requests.get(f"{API}/dispatch/preview", headers=admin).json()
        assert p["service_date"] == tz_today().isoformat()
        assert [z["zone"] for z in p["zones"]] == list(COMPASS)
        assert sum(z["count"] for z in p["zones"]) == p["total"]
        zones = requests.get(f"{API}/zones", headers=admin).json()
        orders = [o for g in zones for o in g["orders"]]
        assert len(orders) == p["total"]
        for z in COMPASS:
            assert next(x for x in p["zones"] if x["zone"] == z)["count"] == sum(1 for o in orders if compass(o) == z)
        cov = {z["zone"]: (z["driver"] or {}).get("id") for z in p["zones"]}
        assert cov == {"North": "drv-1", "South": "drv-2", "East": "drv-3", "West": None}
        print("PREVIEW", {z["zone"]: z["count"] for z in p["zones"]}, "total", p["total"])

    def test_preview_bad_dates(self, admin):
        assert requests.get(f"{API}/dispatch/preview?date=2020-01-01", headers=admin).status_code == 400
        assert requests.get(f"{API}/dispatch/preview?date=bad", headers=admin).status_code == 400


# ---- auto dispatch today (seed: drv-1 has active Mission route)
class TestAutoDispatchToday:
    def test_dispatch_today(self, admin, drv_tokens):
        reset(admin)
        today = tz_today().isoformat()
        preview = requests.get(f"{API}/dispatch/preview", headers=admin).json()
        eligible = {o["id"]: o for g in requests.get(f"{API}/zones", headers=admin).json() for o in g["orders"]}
        drv1_before = requests.get(f"{API}/drivers/drv-1/route", headers=drv_tokens["drv-1"]).json()["route"]
        assert drv1_before and drv1_before["status"] == "active"
        before_ids = {s["id"] for s in drv1_before["stops"]}

        r = requests.post(f"{API}/routes/auto-dispatch", json={"service_date": today}, headers=admin)
        assert r.status_code == 200, r.text
        res = r.json()
        print("RESULT", res)
        assert res["dispatched"] == preview["total"] == len(eligible)
        drivers_in = [x["driver_id"] for x in res["routes"]]
        assert len(drivers_in) == len(set(drivers_in)), "one route per driver"
        assert sum(x["stops"] for x in res["routes"]) == len(eligible)

        seen = set()
        for x in res["routes"]:
            rd = route(admin, x["route_id"])
            assert rd["driver_id"] == x["driver_id"] and rd["service_date"] == today
            for z in x["zones"]:
                if z not in x["fallback"]:
                    assert z == DEFAULT_COV.get(x["driver_id"])
            new_stops = [s for s in rd["stops"] if s["id"] in eligible]
            assert len(new_stops) == x["stops"]
            for s in new_stops:
                assert s["compass_zone"] == compass(s)
                assert s["compass_zone"] in x["zones"]
                seen.add(s["id"])
            seqs = [s["seq"] for s in rd["stops"]]
            assert sorted(seqs) == list(range(1, len(seqs) + 1)), f"seq not contiguous {seqs}"
            if x["driver_id"] == "drv-1":
                assert x["created"] is False
                assert before_ids <= {s["id"] for s in rd["stops"]}
                assert rd["resequence_seen"] is False
        assert seen == set(eligible)
        # West uncovered: must be fallback to driver with fewest open stops
        if next(z for z in preview["zones"] if z["zone"] == "West")["count"]:
            wx = [x for x in res["routes"] if "West" in x["zones"]]
            assert len(wx) == 1 and "West" in wx[0]["fallback"]
        # nothing left
        assert requests.get(f"{API}/dispatch/preview", headers=admin).json()["total"] == 0
        r2 = requests.post(f"{API}/routes/auto-dispatch", json={"service_date": today}, headers=admin)
        assert r2.status_code == 400

    def test_driver_manifests_zone_only(self, admin, drv_tokens):
        for did in ("drv-2", "drv-3"):
            r = requests.get(f"{API}/drivers/{did}/route", headers=drv_tokens[did])
            assert r.status_code == 200
            rt = r.json()["route"]
            assert rt, f"{did} has no route"
            zs = {s["compass_zone"] for s in rt["stops"]}
            print(did, rt["zone"], zs, len(rt["stops"]))
            allowed = set(rt["zone"].split("+"))
            assert zs <= allowed
            assert len(zs) >= 1
        # driver can't read another driver's manifest
        assert requests.get(f"{API}/drivers/drv-3/route", headers=drv_tokens["drv-2"]).status_code == 403


# ---- fallback logic
class TestFallback:
    def test_uncovered_goes_to_fewest(self, admin):
        reset(admin)
        set_cov(admin, {"drv-1": "North", "drv-2": None, "drv-3": None})
        p = requests.get(f"{API}/dispatch/preview", headers=admin).json()
        counts = {z["zone"]: z["count"] for z in p["zones"]}
        r = requests.post(f"{API}/routes/auto-dispatch", json={}, headers=admin)
        assert r.status_code == 200, r.text
        res = r.json()
        print("FALLBACK", res, counts)
        by_drv = {x["driver_id"]: x for x in res["routes"]}
        # drv-1 only gets North (plus anything if it's fewest - it has an active route so unlikely)
        assert "North" in by_drv["drv-1"]["zones"] and "North" not in by_drv["drv-1"]["fallback"]
        for x in res["routes"]:
            for z in x["zones"]:
                if z != "North":
                    assert z in x["fallback"]
        # the largest uncovered zone should go to a driver with 0 load (drv-2 by id tiebreak)
        unc = sorted([z for z in ("South", "East", "West") if counts[z]], key=lambda z: -counts[z])
        if unc:
            assert unc[0] in by_drv.get("drv-2", {}).get("zones", [])
        set_cov(admin, DEFAULT_COV)

    def test_all_uncovered(self, admin):
        reset(admin)
        set_cov(admin, {"drv-1": None, "drv-2": None, "drv-3": None})
        r = requests.post(f"{API}/routes/auto-dispatch", json={}, headers=admin)
        assert r.status_code == 200, r.text
        res = r.json()
        for x in res["routes"]:
            assert set(x["zones"]) == set(x["fallback"])
        set_cov(admin, DEFAULT_COV)


# ---- future date dispatch
class TestFutureDate:
    def test_future_day_untouched_other_days(self, admin):
        reset(admin)
        tomorrow = (tz_today() + timedelta(days=1)).isoformat()
        today = tz_today().isoformat()
        today_dated = [o for g in requests.get(f"{API}/zones?date={today}", headers=admin).json() for o in g["orders"] if o.get("window_date") == today]
        fut = requests.get(f"{API}/dispatch/preview?date={tomorrow}", headers=admin).json()
        if fut["total"] == 0:
            pytest.skip("no orders for tomorrow")
        r = requests.post(f"{API}/routes/auto-dispatch", json={"service_date": tomorrow}, headers=admin)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["service_date"] == tomorrow and res["dispatched"] == fut["total"]
        for x in res["routes"]:
            assert x["created"] is True  # nobody has a route tomorrow
            assert route(admin, x["route_id"])["service_date"] == tomorrow
        still = {o["id"] for g in requests.get(f"{API}/zones?date={today}", headers=admin).json() for o in g["orders"]}
        assert {o["id"] for o in today_dated} <= still, "today's dated orders must stay unassigned"


# ---- neighbourhood batching still works
class TestManualBatch:
    def test_manual_batch(self, admin):
        reset(admin)
        zones = requests.get(f"{API}/zones", headers=admin).json()
        assert zones
        z = zones[0]
        r = requests.post(f"{API}/routes/batch", json={"zone": z["zone"], "driver_id": "drv-2"}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["total"] == z["count"] and r.json()["driver_id"] == "drv-2"
        # auto dispatch afterwards appends to drv-2's planned route
        r = requests.post(f"{API}/routes/auto-dispatch", json={}, headers=admin)
        assert r.status_code == 200, r.text
        d2 = [x for x in r.json()["routes"] if x["driver_id"] == "drv-2"]
        if d2:
            assert d2[0]["created"] is False
