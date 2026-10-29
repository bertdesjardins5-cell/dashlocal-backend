# UI helper: put one evening-run Bay Books pharmacy drop first on drv-1's active route (setup) / remove it (cleanup)
import os
import sys
import time
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

if sys.argv[1] == "setup":
    t = requests.post(f"{BASE}/api/auth/login", json={"email": "books@arrivewise.app", "password": "Books2026"}).json()["token"]
    h = {"Authorization": f"Bearer {t}"}
    job = requests.post(f"{BASE}/api/merchants/mer-2/import?run=evening&consent=true", headers=h,
                        files={"file": ("TEST_ui.csv", "customer_name,phone,address\nTEST_UI Pharmacy,+15195550189,200 King St W Kitchener\n", "text/csv")}).json()
    for _ in range(30):
        if requests.get(f"{BASE}/api/imports/{job['id']}", headers=h).json()["status"] != "running":
            break
        time.sleep(1)
    sid = db.import_rows.find_one({"job_id": job["id"]})["stop_id"]
    route = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    db.stops.update_one({"id": sid}, {"$set": {"route_id": route["id"], "seq": 0}})
    print("STOP", sid, "JOB", job["id"], "ROUTE", route["id"])
else:
    for j in db.import_jobs.find({"filename": {"$regex": "^TEST_"}}):
        ids = [r["stop_id"] for r in db.import_rows.find({"job_id": j["id"]}) if r.get("stop_id")]
        db.stops.delete_many({"id": {"$in": ids}})
        db.import_rows.delete_many({"job_id": j["id"]})
        db.import_jobs.delete_one({"id": j["id"]})
        print("cleaned", j["id"], ids)
    print("left TEST_ stops:", db.stops.count_documents({"customer_name": {"$regex": "^TEST_"}}))
