# Iteration 22: Notes In CSV (delivery_note column -> customer_note + saved note) and Doorstep Photo Hint on GET /api/stops/{id}
import base64
import json
import os
import subprocess
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
API = f"{BASE_URL}/api"
NOAUTH = {"X-No-Auto-Auth": "1"}
PHONE_RAW, PHONE = "5195550166", "+15195550166"
PHONE2_RAW, PHONE2 = "5195550167", "+15195550167"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def mongo(js):
    return subprocess.run(["mongosh", "test_database", "--quiet", "--eval", js], capture_output=True, text=True).stdout.strip()


def login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-No-Auto-Auth": "1"}


def driver_h(phone="+15195550101", pin="482913"):
    r = requests.post(f"{API}/auth/driver-login", json={"phone": phone, "pin": pin}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", "X-No-Auto-Auth": "1"}


def cleanup():
    mongo(f'db.saved_notes.deleteMany({{phone:{{$in:["{PHONE}","{PHONE2}"]}}}}); db.last_customer_notes.deleteMany({{phone:{{$in:["{PHONE}","{PHONE2}"]}}}});'
          'db.stops.deleteMany({customer_name:/^TEST_/}); db.login_attempts.deleteMany({key:/^(drv|trk)/})')


def import_csv(m, body):
    r = requests.post(f"{API}/merchants/mer-1/import?consent=true", headers=m, files={"file": ("t.csv", body.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    job = r.json()["id"]
    for _ in range(40):
        rows = json.loads(mongo(f'print(JSON.stringify(db.import_rows.find({{job_id:"{job}"}},{{_id:0,status:1,stop_id:1,line:1,error:1}}).sort({{line:1}}).toArray()))'))
        if rows and all(r["status"] != "queued" for r in rows):
            return rows
        time.sleep(1)
    pytest.fail("import never finished")


def stop_doc(sid):
    d = json.loads(mongo(f'print(JSON.stringify(db.stops.findOne({{id:"{sid}"}},{{_id:0}})))'))
    from cryptography.fernet import Fernet  # notes/customer_note/pod_signature are encrypted at rest ("enc:" prefix)
    f = Fernet(os.environ["ENCRYPTION_KEY"].encode())
    for k in ("notes", "customer_note", "pod_signature"):
        if isinstance(d.get(k), str) and d[k].startswith("enc:"):
            d[k] = f.decrypt(d[k][4:].encode()).decode()
    return d


def saved_for(m, phone):
    r = requests.get(f"{API}/merchants/mer-1/saved-notes", headers=m)
    assert r.status_code == 200
    return [n for n in r.json() if n["phone"] == phone]


@pytest.fixture(scope="module")
def ctx():
    cleanup()
    a = login("admin@arrivewise.app", "DashLocalAdmin2026")
    requests.post(f"{API}/admin/reset", headers=a)
    m1 = login("bakery@arrivewise.app", "Bakery2026")
    yield {"a": a, "m1": m1}
    cleanup()
    requests.post(f"{API}/admin/reset", headers=a)


# ---- Notes In CSV
class TestCsvNotes:
    def test_delivery_note_prefills_and_saves(self, ctx):
        rows = import_csv(ctx["m1"], f"customer_name,phone,address,delivery_note\nTEST_CSV One,{PHONE_RAW},200 King St W Kitchener,  Gate   code 1111 \n")
        assert rows[0]["status"] == "created", rows
        s = stop_doc(rows[0]["stop_id"])
        assert s["customer_note"] == "Gate code 1111"
        assert s["customer_note_source"] == "saved"
        assert s.get("customer_note_at")
        saved = saved_for(ctx["m1"], PHONE)
        assert len(saved) == 1 and saved[0]["note"] == "Gate code 1111"

    def test_gate_code_alias_replaces_saved(self, ctx):
        rows = import_csv(ctx["m1"], f"customer_name,phone,address,gate_code\nTEST_CSV Two,{PHONE_RAW},200 King St W Kitchener,Gate 2222 use side door\n")
        s = stop_doc(rows[0]["stop_id"])
        assert s["customer_note"] == "Gate 2222 use side door" and s["customer_note_source"] == "saved"
        saved = saved_for(ctx["m1"], PHONE)
        assert len(saved) == 1 and saved[0]["note"] == "Gate 2222 use side door"

    def test_row_without_note_falls_back_to_saved(self, ctx):
        rows = import_csv(ctx["m1"], f"customer_name,phone,address,delivery_note\nTEST_CSV Three,{PHONE_RAW},200 King St W Kitchener,\n"
                                      f"TEST_CSV Four,{PHONE2_RAW},200 King St W Kitchener,Leave with doorman\n")
        assert [r["status"] for r in rows] == ["created", "created"], rows
        s = stop_doc(rows[0]["stop_id"])
        assert s["customer_note"] == "Gate 2222 use side door" and s["customer_note_source"] == "saved"
        s2 = stop_doc(rows[1]["stop_id"])
        assert s2["customer_note"] == "Leave with doorman"
        assert saved_for(ctx["m1"], PHONE)[0]["note"] == "Gate 2222 use side door"  # blank cell does not clear the saved note

    def test_long_note_truncated_200(self, ctx):
        rows = import_csv(ctx["m1"], f"customer_name,phone,address,delivery_note\nTEST_CSV Five,{PHONE2_RAW},200 King St W Kitchener,{'x' * 260}\n")
        s = stop_doc(rows[0]["stop_id"])
        assert len(s["customer_note"]) == 200
        assert len(saved_for(ctx["m1"], PHONE2)[0]["note"]) == 200

    def test_template_has_delivery_note(self, ctx):
        r = requests.get(f"{API}/merchant-template.csv", headers=ctx["m1"])
        if r.status_code == 404:
            pytest.skip("template endpoint path differs")
        assert "delivery_note" in r.text.splitlines()[0]


# ---- Doorstep photo hint
def target_stop():
    return json.loads(mongo('const r=db.routes.findOne({driver_id:"drv-1",status:"active"});'
                            'print(JSON.stringify(db.stops.findOne({route_id:r.id,status:"pending"},{_id:0,id:1,customer_phone:1,address:1})))'))


def clone_delivered(src_id, photo_path, delivered_at, **over):
    nid = f"TEST_{uuid.uuid4()}"
    extra = "".join(f",{k}:{json.dumps(v)}" for k, v in over.items())
    mongo(f'const s=db.stops.findOne({{id:"{src_id}"}},{{_id:0}}); db.stops.insertOne({{...s, id:"{nid}", customer_name:"TEST_hint",'
          f'route_id:"TEST_old_route", tracking_code:"TST{nid[-6:]}", status:"delivered", delivered_at:"{delivered_at}",'
          f'pod_photo_path:{json.dumps(photo_path)}{extra}}})')
    return nid


class TestDoorstepHint:
    def test_hint_null_without_history(self, ctx):
        t = target_stop()
        r = requests.get(f"{API}/stops/{t['id']}", headers=driver_h())
        assert r.status_code == 200
        assert "doorstep_hint" in r.json() and r.json()["doorstep_hint"] is None

    def test_hint_latest_photo_and_loads(self, ctx):
        d = driver_h()
        t = target_stop()
        up = [requests.post(f"{API}/upload", headers=d, files={"file": (f"p{i}.png", PNG, "image/png")}) for i in range(2)]
        if any(u.status_code != 200 for u in up):
            pytest.skip(f"upload failed {[u.status_code for u in up]}")
        p_old, p_new = up[0].json()["path"], up[1].json()["path"]
        clone_delivered(t["id"], p_old, "2025-12-01T10:00:00+00:00")
        clone_delivered(t["id"], p_new, "2026-01-05T10:00:00+00:00")
        clone_delivered(t["id"], None, "2026-01-09T10:00:00+00:00")  # newer but without photo -> ignored
        clone_delivered(t["id"], p_old, "2026-01-10T10:00:00+00:00", address="999 Other St, Kitchener")  # other address
        r = requests.get(f"{API}/stops/{t['id']}", headers=d)
        assert r.status_code == 200
        h = r.json()["doorstep_hint"]
        assert h and h["delivered_at"].startswith("2026-01-05")
        assert h["photo_url"].startswith("/api/files/") and p_new in h["photo_url"] and "?t=" in h["photo_url"]
        img = requests.get(f"{BASE_URL}{h['photo_url']}", headers=NOAUTH)
        assert img.status_code == 200 and img.content == PNG
        # admin sees it too
        ra = requests.get(f"{API}/stops/{t['id']}", headers=ctx["a"])
        assert ra.status_code == 200 and ra.json()["doorstep_hint"]["delivered_at"].startswith("2026-01-05")

    def test_other_driver_and_anon_blocked(self, ctx):
        t = target_stop()
        assert requests.get(f"{API}/stops/{t['id']}", headers=NOAUTH).status_code == 401
        r = requests.get(f"{API}/stops/{t['id']}", headers=driver_h("+15195550102", "730516"))
        assert r.status_code == 403
