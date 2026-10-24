"""Iteration 11: Merchant login (JWT), access control, admin merchant accounts, merchant delivery report CSV.
Uses real accounts from /app/memory/test_credentials.md; lockout is tested on a throwaway email only.
"""
import csv
import io
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
ADMIN = ("admin@multidrop.app", "MultiDropAdmin2026")
M1 = ("bakery@multidrop.app", "Bakery2026")
M2 = ("books@multidrop.app", "Books2026")


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def toks():
    return {"admin": _login(*ADMIN)["token"], "m1": _login(*M1)["token"], "m2": _login(*M2)["token"]}


# ---------------- Auth
class TestAuth:
    def test_login_admin_and_merchants(self):
        a = _login(*ADMIN)
        assert a["user"]["role"] == "admin" and "password_hash" not in a["user"] and "_id" not in a["user"]
        m = _login(" Bakery@Multidrop.app ", M1[1])  # case/space-insensitive email
        assert m["user"]["role"] == "merchant" and m["user"]["merchant_id"] == "mer-1"
        assert m["user"]["merchant_name"]
        assert _login(*M2)["user"]["merchant_id"] == "mer-2"

    def test_wrong_password_generic(self):
        e = f"test_unknown_{uuid.uuid4().hex[:6]}@example.com"
        r1 = requests.post(f"{API}/auth/login", json={"email": e, "password": "x"})
        assert r1.status_code == 401
        assert r1.json()["detail"] == "Wrong email or password"

    def test_lockout_throwaway(self):
        e = f"test_lock_{uuid.uuid4().hex[:6]}@example.com"
        codes = [requests.post(f"{API}/auth/login", json={"email": e, "password": "bad"}).status_code for _ in range(6)]
        assert codes[:5] == [401] * 5 and codes[5] == 429, codes
        r = requests.post(f"{API}/auth/login", json={"email": e, "password": "bad"})
        assert r.status_code == 429 and "min" in r.json()["detail"]

    def test_lockout_xff_spoof(self):
        """Security probe: does a client-supplied X-Forwarded-For bypass the lock?"""
        e = f"test_lock_{uuid.uuid4().hex[:6]}@example.com"
        for _ in range(5):
            requests.post(f"{API}/auth/login", json={"email": e, "password": "bad"})
        r = requests.post(f"{API}/auth/login", json={"email": e, "password": "bad"}, headers={"X-Forwarded-For": "9.9.9.9"})
        print("spoofed XFF after lock ->", r.status_code)
        assert r.status_code == 429, "lockout bypassable via spoofed X-Forwarded-For"

    def test_me(self, toks):
        r = requests.get(f"{API}/auth/me", headers=H(toks["m1"]))
        assert r.status_code == 200 and r.json()["merchant_id"] == "mer-1" and "password_hash" not in r.json()
        assert requests.get(f"{API}/auth/me").status_code == 401
        assert requests.get(f"{API}/auth/me", headers=H("garbage")).status_code == 401


# ---------------- Access control
class TestAccess:
    def test_list_merchants_admin_only(self, toks):
        assert requests.get(f"{API}/merchants").status_code == 401
        assert requests.get(f"{API}/merchants", headers=H(toks["m1"])).status_code == 403
        r = requests.get(f"{API}/merchants", headers=H(toks["admin"]))
        assert r.status_code == 200 and {"mer-1", "mer-2"} <= {m["id"] for m in r.json()}

    def test_merchant_detail(self, toks):
        assert requests.get(f"{API}/merchants/mer-1").status_code == 401
        assert requests.get(f"{API}/merchants/mer-2", headers=H(toks["m1"])).status_code == 403
        assert requests.get(f"{API}/merchants/mer-1", headers=H(toks["m1"])).json()["id"] == "mer-1"
        assert requests.get(f"{API}/merchants/mer-2", headers=H(toks["admin"])).status_code == 200

    def test_import_access(self, toks):
        f = {"file": ("t.csv", b"customer_name,phone\n", "text/csv")}
        assert requests.post(f"{API}/merchants/mer-1/import", files=f).status_code == 401
        assert requests.post(f"{API}/merchants/mer-1/import", files=f, headers=H(toks["m2"])).status_code == 403
        # own store reaches validation (400 missing address column) -> auth passed
        assert requests.post(f"{API}/merchants/mer-1/import", files=f, headers=H(toks["m1"])).status_code == 400


# ---------------- Admin merchant accounts
@pytest.fixture(scope="module")
def new_acct(toks):
    email = f"test_store_{uuid.uuid4().hex[:6]}@example.com"
    r = requests.post(f"{API}/admin/merchant-accounts", headers=H(toks["admin"]),
                      json={"store_name": "TEST_Store", "email": email, "password": "TestPass123"})
    assert r.status_code == 200, r.text
    return {**r.json(), "email": email}


class TestAdminAccounts:
    def test_list(self, toks):
        assert requests.get(f"{API}/admin/merchant-accounts", headers=H(toks["m1"])).status_code == 403
        assert requests.get(f"{API}/admin/merchant-accounts").status_code == 401
        d = requests.get(f"{API}/admin/merchant-accounts", headers=H(toks["admin"])).json()
        m1 = next(m for m in d if m["id"] == "mer-1")
        assert m1["accounts"] and "password_hash" not in m1["accounts"][0]

    def test_create_validation(self, toks, new_acct):
        url, h = f"{API}/admin/merchant-accounts", H(toks["admin"])
        assert requests.post(url, headers=h, json={"store_name": "X", "email": new_acct["email"], "password": "TestPass123"}).status_code == 409
        assert requests.post(url, headers=h, json={"store_name": "X", "email": "bad", "password": "TestPass123"}).status_code == 400
        assert requests.post(url, headers=h, json={"store_name": "X", "email": "a@b.co", "password": "short"}).status_code == 400
        assert requests.post(url, headers=h, json={"store_name": " ", "email": "a@b.co", "password": "TestPass123"}).status_code == 400
        assert requests.post(url, headers=H(toks["m1"]), json={"store_name": "X", "email": "z@b.co", "password": "TestPass123"}).status_code == 403

    def test_new_account_login_empty_store(self, new_acct):
        j = _login(new_acct["email"], "TestPass123")
        assert j["user"]["merchant_id"] == new_acct["id"]
        d = requests.get(f"{API}/merchants/{new_acct['id']}", headers=H(j["token"])).json()
        assert d["orders"] == [] and d["flagged"] == []
        assert requests.get(f"{API}/merchants/mer-1", headers=H(j["token"])).status_code == 403

    def test_reset_password(self, toks, new_acct):
        uid = new_acct["accounts"][0]["id"]
        url = f"{API}/admin/merchant-accounts/{uid}/password"
        assert requests.put(url, headers=H(toks["m1"]), json={"password": "NewPass1234"}).status_code == 403
        assert requests.put(url, headers=H(toks["admin"]), json={"password": "short"}).status_code == 400
        assert requests.put(f"{API}/admin/merchant-accounts/nope/password", headers=H(toks["admin"]), json={"password": "NewPass1234"}).status_code == 404
        assert requests.put(url, headers=H(toks["admin"]), json={"password": "NewPass1234"}).status_code == 200
        assert requests.post(f"{API}/auth/login", json={"email": new_acct["email"], "password": "TestPass123"}).status_code == 401
        _login(new_acct["email"], "NewPass1234")


# ---------------- Report CSV with a full delivered + rated order
@pytest.fixture(scope="module")
def delivered(toks):
    h = H(toks["m1"])
    r = requests.post(f"{API}/merchants/mer-1/import", headers=h,
                      files={"file": ("r.csv", b"customer_name,phone,address\nTEST_Report,4155550188,1 Market St\n", "text/csv")})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    end = time.time() + 60
    while time.time() < end:
        j = requests.get(f"{API}/imports/{jid}", headers=h).json()
        if j["status"] == "done":
            break
        time.sleep(1.5)
    assert j["created"] == 1, j
    # job access
    assert requests.get(f"{API}/imports/{jid}").status_code == 401
    assert requests.get(f"{API}/imports/{jid}", headers=H(toks["m2"])).status_code == 403
    assert requests.get(f"{API}/imports/{jid}", headers=H(toks["admin"])).status_code == 200
    order = next(o for o in requests.get(f"{API}/merchants/mer-1", headers=h).json()["orders"] if o["customer_name"] == "TEST_Report")
    today = requests.get(f"{API}/digest").json()["date"]
    driver = next((d["id"] for d in requests.get(f"{API}/drivers").json() if not d["route"]), "drv-3")
    b = requests.post(f"{API}/routes/batch", json={"zone": order["zone"], "driver_id": driver, "service_date": today})
    assert b.status_code == 200, b.text
    route = b.json()
    assert requests.post(f"{API}/routes/{route['id']}/start").status_code == 200
    dv = requests.post(f"{API}/stops/{order['id']}/deliver", json={"signature": "data:image/png;base64,iVBORw0KGgo="})
    assert dv.status_code == 200, dv.text
    rt = requests.post(f"{API}/stops/{order['id']}/rating", json={"stars": 4, "comment": "TEST nice"})
    assert rt.status_code == 200, rt.text
    return order


def _csv(path):
    r = requests.get(f"{BASE_URL}{path}")
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"] and "attachment" in r.headers.get("content-disposition", "")
    return list(csv.DictReader(io.StringIO(r.text.lstrip("\ufeff"))))


class TestReport:
    def test_link_validation(self, toks):
        u = f"{API}/merchants/mer-1/report-link"
        assert requests.post(u, json={"range": "7d"}).status_code == 401
        assert requests.post(u, json={"range": "bogus"}, headers=H(toks["m1"])).status_code == 400
        assert requests.post(u, json={"range": "7d"}, headers=H(toks["m2"])).status_code == 403
        for rg in ("today", "7d", "30d", "all"):
            r = requests.post(u, json={"range": rg}, headers=H(toks["m1"]))
            assert r.status_code == 200 and r.json()["path"].startswith("/api/merchants/mer-1/report.csv?token=")
        assert requests.post(u, json={"range": "all"}, headers=H(toks["admin"])).status_code == 200

    def test_token_store_mismatch_and_bad(self, toks):
        p = requests.post(f"{API}/merchants/mer-1/report-link", json={"range": "all"}, headers=H(toks["m1"])).json()["path"]
        tok = p.split("token=")[1]
        assert requests.get(f"{API}/merchants/mer-2/report.csv?token={tok}").status_code == 403
        assert requests.get(f"{API}/merchants/mer-1/report.csv?token=bad").status_code == 401
        # a session token must not work as a report token
        assert requests.get(f"{API}/merchants/mer-1/report.csv?token={toks['m1']}").status_code == 401

    def test_csv_delivered_row(self, toks, delivered):
        p = requests.post(f"{API}/merchants/mer-1/report-link", json={"range": "today"}, headers=H(toks["m1"])).json()["path"]
        rows = _csv(p)
        assert list(rows[0].keys())[0] == "tracking_code" and list(rows[0].keys())[-3:] == ["proof_link", "rating", "rating_comment"]
        row = next(r for r in rows if r["tracking_code"] == delivered["tracking_code"])
        print("CSV row:", row)
        assert row["status"] == "delivered" and row["delivered_at"]
        assert "signature" in row["proof_types"]
        assert row["rating"] == "4" and row["rating_comment"] == "TEST nice"
        assert row["proof_link"].startswith("https://")
        pr = requests.get(row["proof_link"])
        assert pr.status_code == 200, pr.status_code
        # report only includes own orders
        all_rows = _csv(requests.post(f"{API}/merchants/mer-1/report-link", json={"range": "all"}, headers=H(toks["m1"])).json()["path"])
        own = {o["tracking_code"] for o in requests.get(f"{API}/merchants/mer-1", headers=H(toks["m1"])).json()["orders"]}
        assert {r["tracking_code"] for r in all_rows} <= own

    def test_mer2_report_excludes_mer1(self, toks, delivered):
        rows = _csv(requests.post(f"{API}/merchants/mer-2/report-link", json={"range": "all"}, headers=H(toks["m2"])).json()["path"])
        assert delivered["tracking_code"] not in {r["tracking_code"] for r in rows}
