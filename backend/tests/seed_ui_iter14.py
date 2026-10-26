"""UI seed for iteration 14: mer-1 orders with priority, a route for drv-2 with one delivered drop, and one unrouted pending order."""
import os
import time
import requests

API = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") + "/api"
H = lambda t: {"Authorization": f"Bearer {t}"}
m1 = requests.post(f"{API}/auth/login", json={"email": "bakery@dashlocal.app", "password": "Bakery2026"}).json()["token"]
adm = requests.post(f"{API}/auth/login", json={"email": "admin@dashlocal.app", "password": "DashLocalAdmin2026"}).json()["token"]
d2 = requests.post(f"{API}/auth/driver-login", json={"phone": "+14155550102", "pin": "730516"}).json()["token"]


def imp(body):
    r = requests.post(f"{API}/merchants/mer-1/import?consent=true", headers=H(m1), files={"file": ("ui.csv", body, "text/csv")})
    r.raise_for_status()
    jid = r.json()["id"]
    for _ in range(40):
        j = requests.get(f"{API}/imports/{jid}", headers=H(m1)).json()
        if j["status"] == "done":
            return j
        time.sleep(1.5)


print(imp(b"customer_name,phone,address,priority\nTEST_UIA,4155550193,1 Market St,yes\nTEST_UIB,4155550194,101 Market St,yes\nTEST_UIX,4155550195,1 Market St,\n"))
orders = {o["customer_name"]: o for o in requests.get(f"{API}/merchants/mer-1", headers=H(m1)).json()["orders"]}
today = requests.get(f"{API}/digest", headers=H(adm)).json()["date"]
r = requests.post(f"{API}/routes/batch", headers=H(adm), json={"zone": orders["TEST_UIA"]["zone"], "driver_id": "drv-2", "service_date": today}).json()
print("route", r["id"], [(s["customer_name"], s.get("priority"), s["seq"]) for s in r["stops"]])
print("start", requests.post(f"{API}/routes/{r['id']}/start", headers=H(d2)).status_code)
print("deliver", requests.post(f"{API}/stops/{orders['TEST_UIA']['id']}/deliver", headers=H(d2), json={"signature": "M1 1 L2 2"}).status_code)
print(imp(b"customer_name,phone,address\nTEST_UIC,4155550196,1 Market St\n"))
o = [x for x in requests.get(f"{API}/merchants/mer-1", headers=H(m1)).json()["orders"] if x["customer_name"].startswith("TEST_UI")]
print([(x["customer_name"], x["tracking_code"], x["status"], x.get("priority"), x.get("route_id") is not None, x["fee"]["total"]) for x in o])
b = requests.get(f"{API}/merchants/mer-1/bill", headers=H(m1)).json()
print("bill", b["total"], b["drops"], b["open_orders"], [x["tracking_code"] for x in b["lines"]])
