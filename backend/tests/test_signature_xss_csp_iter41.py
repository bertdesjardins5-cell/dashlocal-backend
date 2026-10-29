# Iter41: security audit #2 fixes - signature SVG-path validation on deliver (all stops), attribute-safe escaping on the
# public proof page, Content-Security-Policy on server-rendered HTML (proof + labels), CSV proof links never use a forged host.
# Needs a fresh POST /api/admin/reset (drv-1 active route with >=3 pending stops). Mutates seed data -> reset afterwards.
import base64
import csv
import hashlib
import io
import os
import re
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
ADMIN = (os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
NA = {"X-No-Auto-Auth": "1"}
JPEG = b"\xff\xd8\xff\xe0" + b"SIGXSSTEST" * 40 + b"\xff\xd9"
SIG_MSG = "The signature must be drawn on the signature pad"
EVIL = 'M0 0" onmouseover="alert(1)'
VALID = "M10.5 20 L30 40.2 M5 5 L6 6"
PAD = "M12.0 34.0 L13.5 35.2 L15.1 36.9 L-2.0 4.5 M40.0 40.0 L41.2 42.3"  # SignaturePad output shape


def H(t):
    return {"Authorization": f"Bearer {t}", "X-No-Auto-Auth": "1"}


def _tok(r):
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def toks():
    db.login_attempts.delete_many({})
    lg = lambda e, p: requests.post(f"{BASE_URL}/api/auth/login", json={"email": e, "password": p}, headers=NA)
    dl = lambda ph, pin: requests.post(f"{BASE_URL}/api/auth/driver-login", json={"phone": ph, "pin": pin}, headers=NA)
    return {"admin": _tok(lg(*ADMIN)), "drv1": _tok(dl("+15195550101", "482913")), "m1": _tok(lg("bakery@arrivewise.app", "Bakery2026"))}


@pytest.fixture(scope="module")
def ctx():
    r = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    ss = list(db.stops.find({"route_id": r["id"], "status": "pending", "merchant_id": None, "batch_run": {"$in": [None, False]}},
                            {"_id": 0, "id": 1}).limit(3)) if r else []
    if len(ss) < 3:
        pytest.skip("need 3 pending drv-1 stops (run POST /api/admin/reset)")
    ids = [s["id"] for s in ss]
    db.stops.update_one({"id": ids[1]}, {"$set": {"merchant_id": "mer-2"}})  # pharmacy partner
    db.stops.update_one({"id": ids[2]}, {"$set": {"merchant_id": "mer-1"}})  # bakery (for report CSV)
    return {"route": r["id"], "plain": ids[0], "pharm": ids[1], "bakery": ids[2]}


def strip_cf(html):  # Cloudflare edge injects a challenge beacon <script> into HTML responses (not app code; blocked by CSP)
    return re.sub(r"<script>\(function\(\)\{function c\(\).*?</script>", "", html, flags=re.S)


def deliver(sid, t, **body):
    return requests.post(f"{BASE_URL}/api/stops/{sid}/deliver", json=body, headers=H(t))


def upload(t):
    r = requests.post(f"{BASE_URL}/api/upload", files={"file": ("pod.jpg", io.BytesIO(JPEG), "image/jpeg")}, headers=H(t))
    assert r.status_code == 200, r.text
    return r.json()["path"]


def proof(sid, t):
    r = requests.post(f"{BASE_URL}/api/stops/{sid}/share", headers=H(t))
    assert r.status_code == 200, r.text
    return requests.get(f"{BASE_URL}{r.json()['path']}")


# --- signature validation on deliver ---
class TestSignatureValidation:
    @pytest.mark.parametrize("sig", [EVIL, "<script>alert(1)</script>", "M0 0 Z\"/><script>", "hello", "L10 10", "M0 0 e5", "M1 1;"])
    def test_non_pharmacy_bad_signature_rejected(self, toks, ctx, sig):
        r = deliver(ctx["plain"], toks["drv1"], signature=sig)
        assert r.status_code == 400 and r.json()["detail"] == SIG_MSG, r.text
        assert db.stops.find_one({"id": ctx["plain"]})["status"] == "pending"

    def test_admin_also_validated(self, toks, ctx):
        r = deliver(ctx["plain"], toks["admin"], signature=EVIL)
        assert r.status_code == 400 and r.json()["detail"] == SIG_MSG

    def test_over_200k_rejected(self, toks, ctx):
        sig = "M0 0" + " L1 1" * 40001  # ~200k+ chars
        assert len(sig) > 200000
        r = deliver(ctx["plain"], toks["drv1"], signature=sig)
        assert r.status_code == 400 and r.json()["detail"] == SIG_MSG

    def test_long_valid_under_200k_accepted_spec(self, toks, ctx):
        # Review spec says <=200000 chars allowed; a 100k-char drawn path must be accepted (throwaway copy of a pending stop).
        sig = "M0 0" + " L1 1" * 20000  # 100004 chars
        sid = ctx["plain"] + "-long"
        src = db.stops.find_one({"id": ctx["plain"]}, {"_id": 0})
        db.stops.delete_many({"id": sid})
        db.stops.insert_one({**src, "id": sid, "tracking_code": "TESTLONG1"})
        try:
            r = deliver(sid, toks["admin"], signature=sig)
            assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        finally:
            db.stops.delete_many({"id": sid})

    def test_valid_signature_delivers(self, toks, ctx):
        r = deliver(ctx["plain"], toks["drv1"], signature=VALID)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "delivered"

    def test_pharmacy_bad_signature_rejected(self, toks, ctx):
        p = upload(toks["drv1"])
        r = deliver(ctx["pharm"], toks["drv1"], photo_path=p, signature=EVIL)
        assert r.status_code == 400, r.text
        assert db.stops.find_one({"id": ctx["pharm"]})["status"] == "pending"
        r = deliver(ctx["pharm"], toks["drv1"], photo_path=p, signature=PAD)
        assert r.status_code == 200, r.text

    def test_signaturepad_shape_delivers(self, toks, ctx):
        r = deliver(ctx["bakery"], toks["drv1"], signature=PAD)
        assert r.status_code == 200, r.text


# --- proof page escaping + CSP ---
class TestProofPage:
    def test_valid_signature_rendered_with_csp(self, toks, ctx):
        r = proof(ctx["plain"], toks["admin"])
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        csp = r.headers.get("content-security-policy", "")
        assert "default-src 'none'" in csp and "script-src" not in csp, csp
        assert f'<path d="{VALID}"' in r.text
        assert "<script" not in strip_cf(r.text).lower()

    @pytest.mark.parametrize("encrypt", [False, True])
    def test_legacy_malicious_signature_stays_in_attribute(self, toks, ctx, encrypt):
        val = EVIL + "' x='<b>"
        if encrypt:
            from cryptography.fernet import Fernet
            val = "enc:" + Fernet(os.environ["ENCRYPTION_KEY"].encode()).encrypt(val.encode()).decode()
        db.stops.update_one({"id": ctx["bakery"]}, {"$set": {"pod_signature": val}})
        r = proof(ctx["bakery"], toks["admin"])
        assert r.status_code == 200
        assert 'onmouseover="alert' not in r.text and "x='<b>" not in r.text
        m = re.search(r'<path d="([^"]*)"', r.text)
        assert m and "&quot;" in m.group(1) and "&#x27;" in m.group(1) and "&lt;b&gt;" in m.group(1), r.text[-800:]
        assert "default-src 'none'" in r.headers.get("content-security-policy", "")

    def test_expired_page_has_csp(self, toks, ctx):
        r0 = requests.post(f"{BASE_URL}/api/stops/{ctx['plain']}/share", headers=H(toks["admin"])).json()
        db.stops.update_one({"id": ctx["plain"]}, {"$set": {"share_created_at": "2020-01-01T00:00:00+00:00"}})
        r = requests.get(f"{BASE_URL}{r0['path']}")
        assert r.status_code == 410 and "default-src 'none'" in r.headers.get("content-security-policy", "")

    def test_json_endpoint_unaffected(self):
        r = requests.get(f"{BASE_URL}/api/merchant-template.csv")
        assert r.status_code == 200 and "content-security-policy" not in r.headers
        assert "strict-transport-security" in r.headers


# --- printable labels CSP ---
class TestLabels:
    def _get(self, rid, extra=""):
        p = requests.post(f"{BASE_URL}/api/routes/{rid}/labels-link").json()["path"]
        return requests.get(f"{BASE_URL}{p}{extra}")

    def test_autoprint_hash_matches_inline_script(self, ctx):
        r = self._get(ctx["route"], "&autoprint=1")
        assert r.status_code == 200 and "LOAD" in r.text
        csp = r.headers.get("content-security-policy", "")
        m = re.search(r"<script>(window\.onload.*?)</script>", r.text, re.S)
        assert m, "inline print script missing"
        sha = base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode()
        assert f"script-src 'sha256-{sha}'" in csp and "default-src 'none'" in csp and "unsafe-inline' " not in csp.split("script-src")[1]

    def test_no_autoprint_still_has_csp(self, ctx):
        r = self._get(ctx["route"])
        body = strip_cf(r.text)
        assert r.status_code == 200 and "<script" not in body
        assert "default-src 'none'" in r.headers.get("content-security-policy", "")

    def test_route_name_escaped(self, ctx):
        old = db.routes.find_one({"id": ctx["route"]})["name"]
        db.routes.update_one({"id": ctx["route"]}, {"$set": {"name": 'R"<img src=x onerror=alert(1)>'}})
        try:
            r = self._get(ctx["route"])
            assert "<img src=x" not in r.text and "&lt;img" in r.text
        finally:
            db.routes.update_one({"id": ctx["route"]}, {"$set": {"name": old}})


# --- report CSV proof links ---
class TestReportCsvHost:
    def _rows(self, toks, headers=None, base=BASE_URL):
        p = requests.post(f"{BASE_URL}/api/merchants/mer-1/report-link", json={"range": "all"}, headers=H(toks["m1"])).json()["path"]
        r = requests.get(f"{base}{p}", headers=headers or {})
        assert r.status_code == 200, r.text
        return list(csv.DictReader(io.StringIO(r.text.lstrip("\ufeff"))))

    def _links(self, rows):
        return [x["proof_link"] for x in rows if x["proof_link"]]

    def test_forged_forwarded_host_public(self, toks, ctx):
        links = self._links(self._rows(toks, {"X-Forwarded-Host": "evil.com"}))
        assert links and all(lk.startswith("https://arrivewise.preview.emergentagent.com/api/proof/") for lk in links), links

    def test_forged_host_internal(self, toks, ctx):
        # ingress may rewrite headers; hit the backend directly with forged Host + X-Forwarded-Host
        links = self._links(self._rows(toks, {"Host": "evil.com", "X-Forwarded-Host": "evil.com", "X-Forwarded-Proto": "https"},
                                       base="http://localhost:8001"))
        assert links and all(lk.startswith("https://arrivewise.preview.emergentagent.com/api/proof/") for lk in links), links
        assert db.settings.find_one({"key": "public_base"})["value"] == "https://arrivewise.preview.emergentagent.com"
