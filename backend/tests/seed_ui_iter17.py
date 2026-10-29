"""UI seed for iteration 17: reset, mer-1 delivered drop last week (past-week bill > 0), coverage drv-1 North only."""
import os, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
API = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") + "/api"
TZ = ZoneInfo("America/Los_Angeles")
today = datetime.now(TZ).date()
last_mon = today - timedelta(days=today.weekday()) - timedelta(weeks=1)
a = {"Authorization": "Bearer " + requests.post(f"{API}/auth/login", json={"email": "admin@arrivewise.app", "password": "DashLocalAdmin2026"}).json()["token"]}
m = {"Authorization": "Bearer " + requests.post(f"{API}/auth/login", json={"email": "bakery@arrivewise.app", "password": "Bakery2026"}).json()["token"]}
assert requests.post(f"{API}/admin/reset", headers=a).status_code == 200
for d, z in {"drv-1": "North", "drv-2": None, "drv-3": None}.items():
    requests.put(f"{API}/admin/drivers/{d}/compass-zone", json={"zone": z}, headers=a)
r = requests.post(f"{API}/merchants/mer-1/import?consent=true", headers=m, files={"file": ("p.csv", b"customer_name,phone,address\nTEST_UI Note,4155550193,1 Market St\n", "text/csv")})
jid = r.json()["id"]
for _ in range(40):
    if requests.get(f"{API}/imports/{jid}", headers=m).json()["status"] == "done":
        break
    time.sleep(1.5)
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
st = db.stops.find_one({"customer_name": "TEST_UI Note"}, {"_id": 0})
when = datetime.combine(last_mon + timedelta(days=2), datetime.min.time(), TZ).replace(hour=12).astimezone(timezone.utc)
db.stops.update_one({"id": st["id"]}, {"$set": {"status": "delivered", "delivered_at": when.isoformat()}})
b = requests.get(f"{API}/admin/billing", params={"week": last_mon.isoformat()}, headers=a).json()
print("last week", last_mon, [(s["id"], s["total"], s["can_mark_paid"]) for s in b["stores"]])
print("preview", [(z["zone"], z["count"]) for z in requests.get(f"{API}/dispatch/preview", headers=a).json()["zones"]])
