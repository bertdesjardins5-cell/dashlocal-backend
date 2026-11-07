# Fee estimate (price preview) + bill-line distance fields — iteration 67
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
ADDR = "550 King St N, Waterloo"


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"})
    if r.status_code != 200:
        pytest.skip(f"admin login failed {r.status_code}")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


# Fee estimate: shape + math for a regular store
@pytest.mark.parametrize("mid,pharm", [("mer-1", False), ("mer-2", True)])
def test_estimate_shape_and_math(admin, mid, pharm):
    r = admin.post(f"{BASE_URL}/api/merchants/{mid}/fee-estimate", json={"address": ADDR, "priority": False})
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ("km", "base", "distance_fee", "priority_fee", "total", "included_km", "per_km", "tax_pct", "pharmacy", "batch_rate", "currency"):
        assert k in d, k
    assert d["included_km"] == 3.0
    assert d["priority_fee"] == 0.0
    assert d["pharmacy"] is pharm
    assert (d["batch_rate"] == 6.25) if pharm else d["batch_rate"] is None
    exp = round(max(0.0, d["km"] - d["included_km"]) * d["per_km"], 2)
    assert abs(d["distance_fee"] - exp) < 0.011
    assert abs(d["total"] - round(d["base"] + d["distance_fee"], 2)) < 0.011
    print(mid, d)


# Fee estimate: priority adds the surcharge
def test_estimate_priority(admin):
    a = admin.post(f"{BASE_URL}/api/merchants/mer-1/fee-estimate", json={"address": ADDR, "priority": False}).json()
    b = admin.post(f"{BASE_URL}/api/merchants/mer-1/fee-estimate", json={"address": ADDR, "priority": True}).json()
    assert b["priority_fee"] > 0
    assert abs(b["total"] - a["total"] - b["priority_fee"]) < 0.011


# Fee estimate: errors
def test_estimate_short(admin):
    r = admin.post(f"{BASE_URL}/api/merchants/mer-1/fee-estimate", json={"address": "ab ", "priority": False})
    assert r.status_code == 400
    assert "street address" in r.json()["detail"].lower()


def test_estimate_unknown(admin):
    r = admin.post(f"{BASE_URL}/api/merchants/mer-1/fee-estimate", json={"address": "zzqx nowhere blvd 99999 qqq", "priority": False})
    assert r.status_code == 404, r.text


def test_estimate_unknown_merchant(admin):
    r = admin.post(f"{BASE_URL}/api/merchants/mer-nope/fee-estimate", json={"address": ADDR, "priority": False})
    assert r.status_code in (403, 404)


def test_estimate_no_auth():
    r = requests.post(f"{BASE_URL}/api/merchants/mer-1/fee-estimate", json={"address": ADDR}, headers={"X-No-Auto-Auth": "1"})
    assert r.status_code == 401


# Bill lines carry km / base / distance_fee / priority_fee / total
@pytest.mark.parametrize("mid", ["mer-1", "mer-2"])
def test_bill_lines_fields(admin, mid):
    b = admin.get(f"{BASE_URL}/api/merchants/{mid}/bill").json()
    found = 0
    for w in b["weeks"]:
        bw = admin.get(f"{BASE_URL}/api/merchants/{mid}/bill", params={"week": w}).json()
        for x in bw["lines"]:
            found += 1
            for k in ("km", "base", "distance_fee", "priority_fee", "total"):
                assert isinstance(x[k], (int, float))
        print(mid, w, len(bw["lines"]), [(x["tracking_code"], x["km"], x["distance_fee"]) for x in bw["lines"][:3]])
    print(mid, "total lines in 8 weeks:", found)
