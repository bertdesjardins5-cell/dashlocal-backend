# UI helper iter34: a TEST customer (+15195550976) with one delivered mer-2 order + saved note, for the admin erase flow (setup / cleanup)
import os
import sys
import time
import requests
from datetime import datetime, timezone
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
PHONE = "+15195550976"

if sys.argv[1] == "setup":
    t = requests.post(f"{BASE}/api/auth/login", json={"email": "books@localdash.app", "password": "Books2026"}).json()["token"]
    h = {"Authorization": f"Bearer {t}"}
    job = requests.post(f"{BASE}/api/merchants/mer-2/import?consent=true&run=evening", headers=h,
                        files={"file": ("TEST_erase_ui.csv", f"customer_name,phone,address\nTEST_UI Erase,{PHONE},100 Queen St S Kitchener\n", "text/csv")}).json()
    for _ in range(30):
        if requests.get(f"{BASE}/api/imports/{job['id']}", headers=h).json()["status"] != "running":
            break
        time.sleep(1)
    sid = db.import_rows.find_one({"job_id": job["id"]})["stop_id"]
    db.stops.update_one({"id": sid}, {"$set": {"status": "delivered", "delivered_at": datetime.now(timezone.utc).isoformat()}})
    requests.put(f"{BASE}/api/merchants/mer-2/saved-notes", headers=h, json={"phone": PHONE, "note": "TEST ring twice"})
    print("STOP", sid, "JOB", job["id"])
else:
    db.saved_notes.delete_many({"phone": PHONE})
    db.data_requests.delete_many({"phone": {"$in": [PHONE, "+15195550105", "+15195550909"]}})
    mids = {s.get("manifest_id") for s in db.stops.find({"customer_name": {"$regex": "^(TEST_|Erased \\(customer)"}, "merchant_id": "mer-2"})}
    print("pickups removed", db.pickups.delete_many({"manifest_id": {"$in": [m for m in mids if m]}}).deleted_count)
