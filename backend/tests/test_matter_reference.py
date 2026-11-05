# Iter51: Matter Reference for law offices — CSV matter_ref column (+aliases), flagged-row fix, PUT stop matter tag,
# receipt PDF row / receipts list / ZIP filenames. Creates TEST_ stores + TSMR stops; all cleaned up at teardown.
# Run: python -m pytest tests/test_matter_reference.py -n0
import io
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / "frontend" / ".env")
sys.path.insert(0, str(ROOT))

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
ADMIN = (os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
NA = {"X-No-Auto-Auth": "1"}
SIG = "M20 30 L80 90 L140 60 M200 50 L300 150"
PW = "TestPass2026"


def H(t=None):
    return {"Authorization": f"Bearer {t}", "X-No-Auto-Auth": "1"} if t else dict(NA)


def login(e, p):
    r = requests.post(f"{API}/auth/login", json={"email": e, "password": p}, headers=NA)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def toks():
    db.login_attempts.delete_many({})
    r = requests.post(f"{API}/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"}, headers=NA)
    assert r.status_code == 200, r.text
    return {"admin": login(*ADMIN), "drv1": r.json()["token"], "m1": login("bakery@dashlocal.app", "Bakery2026"),
            "m2": login("books@dashlocal.app", "Books2026")}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    mer1 = db.merchants.find_one({"id": "mer-1"}, {"_id": 0})
    job_ids = set(j["id"] for j in db.import_jobs.find({"merchant_id": "mer-1"}, {"id": 1}))
    yield
    for m in db.merchants.find({"name": {"$regex": "^TEST_"}}, {"id": 1}):
        db.users.delete_many({"merchant_id": m["id"]})
        db.invoices.delete_many({"merchant_id": m["id"]})
        db.merchants.delete_one({"id": m["id"]})
    for s in db.stops.find({"$or": [{"tracking_code": {"$regex": "^TSMR"}}, {"customer_name": {"$regex": "^TEST_MR"}}]}, {"id": 1}):
        db.invoices.delete_many({"stop_id": s["id"]})
    db.stops.delete_many({"$or": [{"tracking_code": {"$regex": "^TSMR"}}, {"customer_name": {"$regex": "^TEST_MR"}}]})
    new_jobs = [j["id"] for j in db.import_jobs.find({"merchant_id": "mer-1"}, {"id": 1}) if j["id"] not in job_ids]
    db.import_rows.delete_many({"job_id": {"$in": new_jobs}})
    db.import_jobs.delete_many({"id": {"$in": new_jobs}})
    db.merchants.replace_one({"id": "mer-1"}, mer1)
    db.login_attempts.delete_many({})


def import_csv(t, body):
    r = requests.post(f"{API}/merchants/mer-1/import?consent=true", headers=H(t), files={"file": ("t.csv", body.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    job = r.json()["id"]
    for _ in range(60):
        rows = list(db.import_rows.find({"job_id": job}, {"_id": 0}).sort("line", 1))
        if rows and all(x["status"] != "queued" for x in rows):
            return rows
        time.sleep(1)
    pytest.fail("import never finished")


def matter_of(sid):
    return db.stops.find_one({"id": sid}, {"_id": 0, "matter_ref": 1}).get("matter_ref")


def new_store(t, btype):
    sfx = uuid.uuid4().hex[:6]
    email = f"test_{btype}_{sfx}@example.com"
    r = requests.post(f"{API}/admin/merchant-accounts", headers=H(t), json={
        "store_name": f"TEST_{btype}_{sfx}", "email": email, "password": PW, "business_type": btype})
    assert r.status_code == 200, r.text
    return r.json()["id"], email


def clone_stop(merchant_id, **over):
    r = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    src = db.stops.find_one({"route_id": r["id"], "status": "pending", "merchant_id": None, "batch_run": {"$in": [None, False]}}, {"_id": 0}) if r else None
    if not src:
        pytest.skip("need a pending drv-1 stop (POST /api/admin/reset)")
    sid = str(uuid.uuid4())
    db.stops.insert_one({**src, "id": sid, "tracking_code": f"TSMR{uuid.uuid4().hex[:5].upper()}", "merchant_id": merchant_id,
                         "seq": 99, "arrived_at": None, "share_token": None, "receipt_email_at": None, "receipt_email_status": None, **over})
    return sid


def pdf_text(content):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages)


# --- CSV import ---
class TestCsvMatter:
    def test_template_header_ends_with_matter_ref(self, toks):
        r = requests.get(f"{API}/merchant-template.csv", headers=H(toks["m1"]))
        assert r.status_code == 200, r.text
        lines = r.text.splitlines()
        assert lines[0].endswith(",matter_ref"), lines[0]
        assert all(len(l.split(",")) == len(lines[0].split(",")) for l in lines[1:]), lines

    def test_import_aliases_whitespace_truncate_blank(self, toks):
        long = "L" * 75
        rows = import_csv(toks["m1"], "customer_name,phone,address,file_number\n"
                          "TEST_MR A,5195550171,200 King St W Kitchener,  2026 -   0142  \n"
                          f"TEST_MR B,5195550172,200 King St W Kitchener,{long}\n"
                          "TEST_MR C,5195550173,200 King St W Kitchener,   \n")
        assert [x["status"] for x in rows] == ["created"] * 3, rows
        assert matter_of(rows[0]["stop_id"]) == "2026 - 0142"
        assert matter_of(rows[1]["stop_id"]) == "L" * 60
        assert matter_of(rows[2]["stop_id"]) is None

    @pytest.mark.parametrize("col", ["matter_ref", "matter", "client_matter", "reference", "file_no", "file", "matter_number", "matter_no"])
    def test_alias_columns(self, toks, col):
        rows = import_csv(toks["m1"], f"customer_name,phone,address,{col}\nTEST_MR {col},5195550174,200 King St W Kitchener,M-{col}\n")
        assert rows[0]["status"] == "created", rows
        assert matter_of(rows[0]["stop_id"]) == f"M-{col}"

    def test_fix_flagged_row_with_matter(self, toks):
        rows = import_csv(toks["m1"], "customer_name,phone,address,matter_ref\nTEST_MR Fix,12,200 King St W Kitchener,OLD-1\n")
        assert rows[0]["status"] == "invalid", rows
        r = requests.put(f"{API}/import-rows/{rows[0]['id']}", headers=H(toks["m1"]), json={"phone": "5195550175", "matter_ref": "  NEW   7 "})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["status"] == "created", out
        assert matter_of(out["stop_id"]) == "NEW 7"


# --- PUT matter tag ---
class TestSetMatter:
    @pytest.fixture(scope="class")
    def stop(self, toks):
        return clone_stop("mer-1", status="pending", route_id=None)

    def url(self, sid, mid="mer-1"):
        return f"{API}/merchants/{mid}/stops/{sid}/matter"

    def test_set_and_visible_in_portal(self, toks, stop):
        r = requests.put(self.url(stop), headers=H(toks["m1"]), json={"matter_ref": "  2026  /0142 "})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "matter_ref": "2026 /0142"}
        assert matter_of(stop) == "2026 /0142"
        d = requests.get(f"{API}/merchants/mer-1", headers=H(toks["m1"])).json()
        o = next((x for x in d["orders"] if x["id"] == stop), None)
        assert o is not None and o.get("matter_ref") == "2026 /0142", o

    def test_truncate_60(self, toks, stop):
        r = requests.put(self.url(stop), headers=H(toks["m1"]), json={"matter_ref": "Z" * 90})
        assert r.status_code == 200 and r.json()["matter_ref"] == "Z" * 60

    def test_clear_blank_and_null(self, toks, stop):
        r = requests.put(self.url(stop), headers=H(toks["m1"]), json={"matter_ref": "   "})
        assert r.status_code == 200 and r.json() == {"ok": True, "matter_ref": None}
        assert matter_of(stop) is None
        requests.put(self.url(stop), headers=H(toks["m1"]), json={"matter_ref": "X1"})
        r = requests.put(self.url(stop), headers=H(toks["m1"]), json={"matter_ref": None})
        assert r.status_code == 200 and r.json()["matter_ref"] is None and matter_of(stop) is None

    def test_admin_allowed(self, toks, stop):
        r = requests.put(self.url(stop), headers=H(toks["admin"]), json={"matter_ref": "ADM-9"})
        assert r.status_code == 200 and r.json()["matter_ref"] == "ADM-9"

    def test_other_store_403_anon_401(self, toks, stop):
        assert requests.put(self.url(stop), headers=H(toks["m2"]), json={"matter_ref": "HACK"}).status_code == 403
        assert requests.put(self.url(stop), headers=H(), json={"matter_ref": "HACK"}).status_code == 401
        assert matter_of(stop) == "ADM-9"

    def test_stop_of_other_store_404(self, toks, stop):
        other = clone_stop("mer-2")
        r = requests.put(self.url(other), headers=H(toks["m1"]), json={"matter_ref": "HACK"})
        assert r.status_code == 404, r.text
        assert matter_of(other) is None
        assert requests.put(self.url("nope-" + uuid.uuid4().hex), headers=H(toks["m1"]), json={"matter_ref": "X"}).status_code == 404


# --- receipts ---
@pytest.fixture(scope="module")
def law(toks):
    mid, email = new_store(toks["admin"], "law_office")
    t = login(email, PW)
    tagged, plain = clone_stop(mid), clone_stop(mid)
    assert requests.put(f"{API}/merchants/{mid}/stops/{tagged}/matter", headers=H(t), json={"matter_ref": "2026/0142 Smith v. Jones"}).status_code == 200
    for sid in (tagged, plain):
        r = requests.post(f"{API}/stops/{sid}/deliver", json={"signature": SIG}, headers=H(toks["drv1"]))
        assert r.status_code == 200, r.text
        time.sleep(1.1)
    return {"id": mid, "token": t, "tagged": tagged, "plain": plain}


class TestReceiptMatter:
    def test_list_includes_matter_ref(self, law):
        r = requests.get(f"{API}/merchants/{law['id']}/receipts", headers=H(law["token"]))
        assert r.status_code == 200, r.text
        by = {x["id"]: x for x in r.json()["receipts"]}
        assert by[law["tagged"]]["matter_ref"] == "2026/0142 Smith v. Jones"
        assert by[law["plain"]]["matter_ref"] is None

    def _pdf(self, law, sid):
        r = requests.post(f"{API}/merchants/{law['id']}/receipts/link", json={"stop_id": sid}, headers=H(law["token"]))
        assert r.status_code == 200, r.text
        g = requests.get(f"{BASE_URL}{r.json()['path']}")
        assert g.status_code == 200 and g.content[:4] == b"%PDF"
        return pdf_text(g.content)

    def test_pdf_shows_matter_row(self, law):
        txt = self._pdf(law, law["tagged"])
        assert "Matter / file no." in txt and "2026/0142 Smith v. Jones" in txt, txt[:800]

    def test_pdf_without_matter_has_no_row(self, law):
        assert "Matter / file no." not in self._pdf(law, law["plain"])

    def test_zip_filenames_include_sanitized_ref(self, law):
        r = requests.post(f"{API}/merchants/{law['id']}/receipts/link", json={"stop_id": None}, headers=H(law["token"]))
        assert r.status_code == 200, r.text
        g = requests.get(f"{BASE_URL}{r.json()['path']}")
        assert g.status_code == 200, g.text[:300]
        names = zipfile.ZipFile(io.BytesIO(g.content)).namelist()
        tcode = db.stops.find_one({"id": law["tagged"]})["tracking_code"]
        pcode = db.stops.find_one({"id": law["plain"]})["tracking_code"]
        assert any(n.startswith(f"DashLocal-receipt-2026_0142_Smith_v_Jones-{tcode}-") for n in names), names
        assert any(n.startswith(f"DashLocal-receipt-{pcode}-") for n in names), names

    def test_receipt_email_attempted(self, law):
        time.sleep(3)
        s = db.stops.find_one({"id": law["tagged"]}, {"_id": 0, "receipt_email_status": 1, "receipt_email_at": 1})
        print("receipt email:", s)
        assert s.get("receipt_email_at"), s
