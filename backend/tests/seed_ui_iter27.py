# Seed for UI test of invoices (iteration 27): CSV-import 3 mer-1 orders, put on a TEST_INV route for today, deliver them -> 3 invoices
import io, os, time, uuid
from pathlib import Path
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]).rstrip("/") + "/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
SIG = "M10 10 L20 20 L30 15"
tok = lambda e, p: {"Authorization": "Bearer " + requests.post(f"{API}/auth/login", json={"email": e, "password": p}).json()["token"]}
admin, bakery = tok("admin@localdash.app", "DashLocalAdmin2026"), tok("bakery@localdash.app", "Bakery2026")
r101 = db.routes.find_one({"status": "active", "driver_id": "drv-1"}, {"_id": 0})
rid = "TEST_INV_route_ui"
db.routes.delete_one({"id": rid})
db.routes.insert_one({"id": rid, "name": "TEST_INV Route UI", "zone": "Mission", "driver_id": "drv-2", "status": "active", "planned_distance_m": 0,
                      "actual_distance_m": 0, "started_at": r101["started_at"], "completed_at": None, "resequenced_at": None,
                      "resequence_seen": True, "service_date": r101["service_date"], "created_at": r101["created_at"]})
before = [s["id"] for s in db.stops.find({"merchant_id": "mer-1"}, {"id": 1})]
csv = ("customer_name,phone,address\nTEST_INV UI One,+14155550181,\"3150 18th St, San Francisco, CA 94110\"\n"
       "TEST_INV UI Two,+14155550182,\"500 Castro St, San Francisco, CA 94114\"\nTEST_INV UI Three,+14155550183,\"2000 Mission St, San Francisco, CA 94110\"\n"
       "TEST_INV UI Keep,+14155550184,\"1 Ferry Building, San Francisco, CA 94111\"\n")
job = requests.post(f"{API}/merchants/mer-1/import?consent=true", files={"file": ("o.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=bakery).json()["id"]
for _ in range(40):
    j = requests.get(f"{API}/imports/{job}", headers=bakery).json()
    if (j.get("job") or j).get("status") != "running":
        break
    time.sleep(1)
new = sorted(db.stops.find({"merchant_id": "mer-1", "id": {"$nin": before}}, {"_id": 0}), key=lambda s: s["customer_name"])
for i, s in enumerate(new, 1):
    db.stops.update_one({"id": s["id"]}, {"$set": {"route_id": rid, "seq": i}})
for s in new:
    if "Keep" not in s["customer_name"]:
        print(s["customer_name"], requests.post(f"{API}/stops/{s['id']}/deliver", json={"signature": SIG}, headers=admin).status_code)
print([(i["number"], i["total"], i["status"]) for i in db.invoices.find({"merchant_id": "mer-1"}, {"_id": 0})])
