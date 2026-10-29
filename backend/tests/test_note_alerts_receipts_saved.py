# Iteration 21: Saved delivery notes (merchant), note prefill on import (saved > last), read receipt
import os
import subprocess
import time

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
API = f"{BASE_URL}/api"
NOAUTH = {"X-No-Auto-Auth": "1"}
PHONE_RAW, PHONE = "5195550177", "+15195550177"


def mongo(js):
    return subprocess.run(["mongosh", "test_database", "--quiet", "--eval", js], capture_output=True, text=True).stdout.strip()


def login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, headers=NOAUTH)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-No-Auto-Auth": "1"}


def driver_h():
    r = requests.post(f"{API}/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"}, headers=NOAUTH)
    if r.status_code != 200:
        pytest.skip(f"driver login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}", "X-No-Auto-Auth": "1"}


def cleanup():
    mongo(f'db.saved_notes.deleteMany({{phone:"{PHONE}"}}); db.last_customer_notes.deleteMany({{phone:"{PHONE}"}});'
          f'db.stops.deleteMany({{customer_name:/^TEST_/}}); db.login_attempts.deleteMany({{key:/^(drv|trk)/}})')


def import_one(m, name="TEST_SN Cust"):
    body = f"customer_name,phone,address\n{name},{PHONE_RAW},200 King St W Kitchener\n".encode()
    r = requests.post(f"{API}/merchants/mer-1/import?consent=true", headers=m, files={"file": ("t.csv", body, "text/csv")})
    assert r.status_code == 200, r.text
    job = r.json()["id"]
    for _ in range(30):
        row = mongo(f'const r=db.import_rows.findOne({{job_id:"{job}"}}); print(r.status+"|"+(r.stop_id||""))')
        st, sid = row.split("|")
        if st != "queued":
            assert st == "created", row
            return mongo(f'printjson(db.stops.findOne({{id:"{sid}"}},{{_id:0,id:1,tracking_code:1,customer_note:1,customer_note_source:1}}))'), sid
        time.sleep(1)
    pytest.fail("import never finished")


def stop_doc(sid):
    import json
    from pathlib import Path
    from dotenv import dotenv_values
    from cryptography.fernet import Fernet  # notes/customer_note/pod_signature are encrypted at rest ("enc:" prefix)
    d = json.loads(mongo(f'print(JSON.stringify(db.stops.findOne({{id:"{sid}"}},{{_id:0}})))'))
    f = Fernet(dotenv_values(Path(__file__).resolve().parents[1] / ".env")["ENCRYPTION_KEY"].encode())
    for k in ("notes", "customer_note", "pod_signature"):
        if isinstance(d.get(k), str) and d[k].startswith("enc:"):
            d[k] = f.decrypt(d[k][4:].encode()).decode()
    return d


@pytest.fixture(scope="module")
def ctx():
    cleanup()
    a = login("admin@arrivewise.app", "DashLocalAdmin2026")
    requests.post(f"{API}/admin/reset", headers=a)
    m1 = login("bakery@arrivewise.app", "Bakery2026")
    m2 = login("books@arrivewise.app", "Books2026")
    yield {"a": a, "m1": m1, "m2": m2}
    cleanup()
    requests.post(f"{API}/admin/reset", headers=a)


# ---- Saved notes CRUD + access
class TestSavedNotes:
    def test_no_auth_401(self, ctx):
        assert requests.get(f"{API}/merchants/mer-1/saved-notes", headers=NOAUTH).status_code == 401

    def test_other_merchant_403(self, ctx):
        assert requests.get(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m2"]).status_code == 403
        r = requests.put(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m2"], json={"phone": PHONE_RAW, "note": "x"})
        assert r.status_code == 403

    def test_invalid_phone_and_empty_note_400(self, ctx):
        assert requests.put(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"], json={"phone": "12", "note": "hi"}).status_code == 400
        assert requests.put(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"], json={"phone": PHONE_RAW, "note": "   "}).status_code == 400

    def test_upsert_normalizes_and_replaces(self, ctx):
        r = requests.put(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"], json={"phone": PHONE_RAW, "note": "Ring  twice"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["phone"] == PHONE and d["note"] == "Ring twice" and d["id"] and "_id" not in d
        r2 = requests.put(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"], json={"phone": "(519) 555-0177", "note": "Leave at side gate"})
        assert r2.status_code == 200 and r2.json()["id"] == d["id"]
        lst = requests.get(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"]).json()
        mine = [n for n in lst if n["phone"] == PHONE]
        assert len(mine) == 1 and mine[0]["note"] == "Leave at side gate"
        # admin can read too
        assert requests.get(f"{API}/merchants/mer-1/saved-notes", headers=ctx["a"]).status_code == 200

    def test_delete_unknown_404_and_delete(self, ctx):
        assert requests.delete(f"{API}/merchants/mer-1/saved-notes/nope", headers=ctx["m1"]).status_code == 404
        r = requests.put(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"], json={"phone": PHONE_RAW, "note": "tmp"})
        nid = r.json()["id"]
        assert requests.delete(f"{API}/merchants/mer-1/saved-notes/{nid}", headers=ctx["m2"]).status_code == 403
        assert requests.delete(f"{API}/merchants/mer-1/saved-notes/{nid}", headers=ctx["m1"]).status_code == 200
        lst = requests.get(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"]).json()
        assert not [n for n in lst if n["id"] == nid]


# ---- Prefill on import
class TestPrefill:
    def test_saved_note_prefill(self, ctx):
        requests.put(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"], json={"phone": PHONE_RAW, "note": "Saved: dog in yard"})
        _, sid = import_one(ctx["m1"])
        s = stop_doc(sid)
        assert s["customer_note"] == "Saved: dog in yard" and s["customer_note_source"] == "saved"

    def test_last_note_then_saved_wins_then_clear(self, ctx):
        mongo(f'db.saved_notes.deleteMany({{phone:"{PHONE}"}})')
        _, sid = import_one(ctx["m1"], "TEST_SN Last1")
        s = stop_doc(sid)
        assert not s.get("customer_note")
        v = requests.post(f"{API}/track/{s['tracking_code']}/verify", json={"last4": "0177"}, headers=NOAUTH)
        assert v.status_code == 200, v.text
        tok = v.json()["token"]
        r = requests.put(f"{API}/stops/{sid}/customer-note", headers={"X-Order-Token": tok, **NOAUTH}, json={"note": "Customer last note"})
        assert r.status_code == 200, r.text
        _, sid2 = import_one(ctx["m1"], "TEST_SN Last2")
        s2 = stop_doc(sid2)
        assert s2["customer_note"] == "Customer last note" and s2["customer_note_source"] == "last"
        # saved wins over last
        requests.put(f"{API}/merchants/mer-1/saved-notes", headers=ctx["m1"], json={"phone": PHONE_RAW, "note": "Store wins"})
        _, sid3 = import_one(ctx["m1"], "TEST_SN Last3")
        s3 = stop_doc(sid3)
        assert s3["customer_note"] == "Store wins" and s3["customer_note_source"] == "saved"
        # clearing removes remembered last note
        mongo(f'db.saved_notes.deleteMany({{phone:"{PHONE}"}})')
        r = requests.put(f"{API}/stops/{sid}/customer-note", headers={"X-Order-Token": tok, **NOAUTH}, json={"note": ""})
        assert r.status_code == 200
        assert mongo(f'print(db.last_customer_notes.countDocuments({{phone:"{PHONE}"}}))') == "0"
        _, sid4 = import_one(ctx["m1"], "TEST_SN Last4")
        assert not stop_doc(sid4).get("customer_note")


# ---- Read receipt
class TestReadReceipt:
    def test_seen_sets_read_and_new_note_resets(self, ctx):
        sid = mongo('print(db.stops.findOne({tracking_code:"MD1001"}).id)')
        tok = requests.post(f"{API}/track/MD1001/verify", json={"last4": "0100"}, headers=NOAUTH).json()["token"]
        oh = {"X-Order-Token": tok, **NOAUTH}
        assert requests.put(f"{API}/stops/{sid}/customer-note", headers=oh, json={"note": "Gate code 1234"}).status_code == 200
        pub = requests.get(f"{API}/track/MD1001", headers=NOAUTH).json()
        assert pub["stop"]["customer_note_read"] is False
        assert requests.post(f"{API}/stops/{sid}/customer-note/seen", headers=NOAUTH).status_code in (401, 403)
        r = requests.post(f"{API}/stops/{sid}/customer-note/seen", headers=driver_h())
        assert r.status_code == 200, r.text
        assert stop_doc(sid).get("customer_note_read_at")
        pub = requests.get(f"{API}/track/MD1001", headers=NOAUTH).json()
        assert pub["stop"]["customer_note_read"] is True
        assert "customer_note" not in pub["stop"]  # note text not public
        requests.put(f"{API}/stops/{sid}/customer-note", headers=oh, json={"note": "Gate code 9999"})
        pub = requests.get(f"{API}/track/MD1001", headers=NOAUTH).json()
        assert pub["stop"]["customer_note_read"] is False
