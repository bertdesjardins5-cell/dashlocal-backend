"""Iteration 10: Daily ops digest, admin re-ordering + driver alert, nearby SMS (fictional numbers -> skipped), merchant CSV import.
Geocoding uses OpenStreetMap Nominatim (~1 req/s) -> keep CSVs tiny.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    assert s.post(f"{API}/admin/reset").status_code == 200
    # merchant endpoints now need a sign-in token (iteration 11) -> sign in as admin
    lr = s.post(f"{API}/auth/login", json={"email": "admin@multidrop.app", "password": "MultiDropAdmin2026"})
    assert lr.status_code == 200, lr.text
    s.headers["Authorization"] = f"Bearer {lr.json()['token']}"
    return s


def _today():
    return requests.get(f"{API}/digest").json()["date"]


# ---------------- Daily digest
class TestDigest:
    def test_digest_today(self, api):
        r = api.get(f"{API}/digest")
        assert r.status_code == 200
        d = r.json()
        for k in ("date", "routes", "total_stops", "delivered", "failed", "remaining", "hours", "drops_per_hour",
                  "late_count", "on_time_rate", "complaint_count", "hourly", "late", "failed_stops", "complaints", "drivers"):
            assert k in d, k
        assert isinstance(d["hourly"], list) and isinstance(d["drivers"], list)
        assert d["late_count"] == len(d["late"])
        assert d["complaint_count"] == len(d["complaints"])
        assert d["failed"] == len(d["failed_stops"])
        assert d["delivered"] + d["failed"] + d["remaining"] == d["total_stops"]
        assert sum(h["count"] for h in d["hourly"]) == d["delivered"]
        assert sum(p["delivered"] for p in d["drivers"]) == d["delivered"]

    def test_digest_explicit_dates(self, api):
        today = _today()
        r = api.get(f"{API}/digest", params={"date": today})
        assert r.status_code == 200 and r.json()["date"] == today
        y = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
        ry = api.get(f"{API}/digest", params={"date": y})
        assert ry.status_code == 200
        dy = ry.json()
        assert dy["date"] == y
        print("yesterday digest:", {k: dy[k] for k in ("routes", "delivered", "failed", "late_count", "complaint_count")})
        for l in dy["late"]:
            assert l["late_min"] >= 1

    @pytest.mark.parametrize("bad", ["2026-13-01", "abc", "2026/01/01"])
    def test_digest_invalid_date(self, api, bad):
        assert api.get(f"{API}/digest", params={"date": bad}).status_code == 400


# ---------------- Re-order + driver alert + nearby SMS
@pytest.fixture(scope="module")
def active_route(api):
    r = api.get(f"{API}/drivers/drv-1/route").json()["route"]
    assert r and r["status"] == "active"
    return r


class TestResequence:
    def test_swap_open_stops_sets_flag(self, api, active_route):
        rid = active_route["id"]
        stops = api.get(f"{API}/routes/{rid}").json()["stops"]
        ids = [s["id"] for s in stops]
        done = [s["id"] for s in stops if s["status"] in ("delivered", "failed")]
        opn = [s["id"] for s in stops if s["status"] not in ("delivered", "failed")]
        assert len(opn) >= 2
        new = done + [opn[1], opn[0]] + opn[2:]
        r = api.put(f"{API}/routes/{rid}/sequence", json={"stop_ids": new})
        assert r.status_code == 200, r.text
        d = r.json()
        assert [s["id"] for s in d["stops"]] == new
        assert d["resequenced_at"] and d["resequence_seen"] is False
        # persisted
        g = api.get(f"{API}/routes/{rid}").json()
        assert [s["id"] for s in g["stops"]] == new and g["resequence_seen"] is False
        # driver route reflects the flag
        assert api.get(f"{API}/drivers/drv-1/route").json()["route"]["resequence_seen"] is False

    def test_unchanged_order_noop(self, api, active_route):
        rid = active_route["id"]
        g = api.get(f"{API}/routes/{rid}").json()
        api.post(f"{API}/routes/{rid}/update-seen")
        r = api.put(f"{API}/routes/{rid}/sequence", json={"stop_ids": [s["id"] for s in g["stops"]]})
        assert r.status_code == 200
        assert r.json()["resequenced_at"] == g["resequenced_at"]
        assert r.json()["resequence_seen"] is True

    def test_update_seen(self, api, active_route):
        rid = active_route["id"]
        r = api.post(f"{API}/routes/{rid}/update-seen")
        assert r.status_code == 200 and r.json()["resequence_seen"] is True
        assert api.post(f"{API}/routes/nope/update-seen").status_code == 404

    def test_finished_stop_must_stay_front(self, api, active_route):
        rid = active_route["id"]
        stops = api.get(f"{API}/routes/{rid}").json()["stops"]
        done = [s["id"] for s in stops if s["status"] in ("delivered", "failed")]
        if not done:
            first = next(s for s in stops if s["status"] in ("pending", "arrived"))
            assert api.post(f"{API}/stops/{first['id']}/deliver", json={"signature": "TEST_sig"}).status_code == 200
            stops = api.get(f"{API}/routes/{rid}").json()["stops"]
            done = [s["id"] for s in stops if s["status"] in ("delivered", "failed")]
        opn = [s["id"] for s in stops if s["id"] not in done]
        bad = [opn[0]] + done + opn[1:]
        r = api.put(f"{API}/routes/{rid}/sequence", json={"stop_ids": bad})
        assert r.status_code == 400

    def test_missing_stop_ids_400(self, api, active_route):
        rid = active_route["id"]
        stops = api.get(f"{API}/routes/{rid}").json()["stops"]
        assert api.put(f"{API}/routes/{rid}/sequence", json={"stop_ids": [s["id"] for s in stops][:-1]}).status_code == 400

    def test_completed_route_400(self, api):
        from pymongo import MongoClient
        from dotenv import dotenv_values
        env = dotenv_values("/app/backend/.env")
        doc = MongoClient(env["MONGO_URL"])[env["DB_NAME"]].routes.find_one({"status": "completed"}, {"_id": 0, "id": 1})
        if not doc:
            pytest.skip("no completed route in seed")
        rid = doc["id"]
        stops = api.get(f"{API}/routes/{rid}").json()["stops"]
        ids = [s["id"] for s in stops]
        assert api.put(f"{API}/routes/{rid}/sequence", json={"stop_ids": ids[::-1]}).status_code == 400


class TestNearbySms:
    def test_nearby_status_once(self, api, active_route):
        rid = active_route["id"]
        api.post(f"{API}/drivers/drv-1/simulate-step")
        time.sleep(2)
        stops = api.get(f"{API}/routes/{rid}").json()["stops"]
        opn = [s for s in stops if s["status"] in ("pending", "arrived")]
        first3_pending = [s for s in opn[:3] if s["status"] == "pending"]
        assert first3_pending
        for s in first3_pending:
            assert s["nearby_sms_status"] == "skipped_fictional", (s["tracking_code"], s["nearby_sms_status"])
        # stops beyond the first 3 open are untouched
        for s in opn[3:]:
            if s["status"] == "pending":
                assert s["nearby_sms_status"] in (None,), s
        snap = {s["id"]: s["nearby_sms_at"] for s in first3_pending}
        for _ in range(2):
            api.post(f"{API}/drivers/drv-1/simulate-step")
        time.sleep(1.5)
        after = {s["id"]: s for s in api.get(f"{API}/routes/{rid}").json()["stops"]}
        for sid, at in snap.items():
            assert after[sid]["nearby_sms_at"] == at, "nearby SMS re-processed"

    def test_tracking_payload_has_status(self, api, active_route):
        stops = api.get(f"{API}/routes/{active_route['id']}").json()["stops"]
        s = next(s for s in stops if s["nearby_sms_status"] and s["status"] == "pending")
        t = api.get(f"{API}/track/{s['tracking_code']}").json()
        assert t["stop"]["nearby_sms_status"] == s["nearby_sms_status"]
        assert t["stops_away"] is not None and t["stops_away"] <= 2


# ---------------- Merchants & CSV import
def _upload(api, content, name="t.csv", mid="mer-1"):
    return api.post(f"{API}/merchants/{mid}/import", files={"file": (name, content.encode(), "text/csv")})


def _wait_job(api, jid, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = api.get(f"{API}/imports/{jid}").json()
        if j["status"] == "done":
            return j
        time.sleep(1.5)
    raise AssertionError("import did not finish")


class TestMerchants:
    def test_list_and_detail(self, api):
        ms = api.get(f"{API}/merchants").json()
        assert {m["id"] for m in ms} >= {"mer-1", "mer-2"}
        for m in ms:
            assert "_id" not in m and {"orders", "delivered", "flagged"} <= set(m)
        d = api.get(f"{API}/merchants/mer-1").json()
        assert d["id"] == "mer-1" and {"orders", "flagged", "jobs", "counts"} <= set(d)
        assert api.get(f"{API}/merchants/nope").status_code == 404

    def test_template(self, api):
        r = api.get(f"{API}/merchant-template.csv")
        assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
        assert r.text.startswith("customer_name,phone,address")

    def test_import_errors(self, api):
        assert _upload(api, "name,phone\nA,4155550100\n").status_code == 400
        assert _upload(api, "").status_code == 400
        assert _upload(api, "customer_name,phone,address\n").status_code == 400
        big = "customer_name,phone,address\n" + "".join(f"P{i},4155550{i:03d},1 Market St\n" for i in range(201))
        r = _upload(api, big)
        assert r.status_code == 400 and "200" in r.json()["detail"]
        assert _upload(api, "customer_name,phone,address\nA,4155550100,1 Market St\n", mid="nope").status_code == 404
        assert api.get(f"{API}/imports/nope").status_code == 404


@pytest.fixture(scope="module")
def import_job(api):
    csv_text = ("Name,Phone,Address,Notes\n"
                "TEST_Valid,415 555 0177,1 Market St,front desk\n"
                "TEST_BadPhone,123,450 Golden Gate Ave,\n"
                "TEST_Bogus,4155550166,99999 Zzyzx Nowhere Blvd,\n")
    r = _upload(api, csv_text)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] == 3 and j["status"] == "running"
    return _wait_job(api, j["id"])


class TestImportFlow:
    def test_job_summary(self, import_job):
        assert import_job["processed"] == 3
        assert import_job["created"] == 1 and import_job["flagged"] == 2, import_job

    def test_rows_and_stop(self, api, import_job):
        d = api.get(f"{API}/merchants/mer-1").json()
        flagged = {r["data"]["customer_name"]: r for r in d["flagged"] if r["job_id"] == import_job["id"]}
        assert flagged["TEST_BadPhone"]["status"] == "invalid"
        assert flagged["TEST_Bogus"]["status"] == "not_found"
        order = next(o for o in d["orders"] if o["customer_name"] == "TEST_Valid")
        assert order["merchant_id"] == "mer-1" and order["zone"]
        assert order["tracking_code"].startswith("MC") and len(order["tracking_code"]) == 7
        assert order["customer_phone"] == "+14155550177"
        zones = api.get(f"{API}/zones").json()
        assert any(o["id"] == order["id"] for z in zones for o in z["orders"]), "imported stop not batchable"

    def test_fix_and_retry_and_dismiss(self, api, import_job):
        d = api.get(f"{API}/merchants/mer-1").json()
        flagged = {r["data"]["customer_name"]: r for r in d["flagged"] if r["job_id"] == import_job["id"]}
        bp = flagged["TEST_BadPhone"]
        r = api.put(f"{API}/import-rows/{bp['id']}", json={"phone": "+1 415 555 0199"})
        assert r.status_code == 200 and r.json()["status"] == "created", r.text
        assert r.json()["tracking_code"].startswith("MC")
        # already created -> 400
        assert api.put(f"{API}/import-rows/{bp['id']}", json={"phone": "4155550100"}).status_code == 400
        assert api.delete(f"{API}/import-rows/{bp['id']}").status_code == 400
        job = api.get(f"{API}/imports/{import_job['id']}").json()
        assert job["created"] == 2 and job["flagged"] == 1
        bg = flagged["TEST_Bogus"]
        assert api.delete(f"{API}/import-rows/{bg['id']}").status_code == 200
        job = api.get(f"{API}/imports/{import_job['id']}").json()
        assert job["flagged"] == 0
        assert api.delete(f"{API}/import-rows/{bg['id']}").status_code == 400
        assert api.put(f"{API}/import-rows/nope", json={}).status_code == 404
        assert api.delete(f"{API}/import-rows/nope").status_code == 404
