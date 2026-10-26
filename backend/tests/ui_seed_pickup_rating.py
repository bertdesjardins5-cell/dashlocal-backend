# UI helper (iteration 36): two Bay Books evening-run drops on drv-1's active route; one delivered with photo+signature.
# setup -> prints DELIVERED code + PENDING code; cleanup -> removes TEST_ import jobs/stops/pickups/invoices
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
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
       b"\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")

if sys.argv[1] == "setup":
    t = requests.post(f"{BASE}/api/auth/login", json={"email": "books@dashlocal.app", "password": "Books2026"}).json()["token"]
    h = {"Authorization": f"Bearer {t}"}
    csv = "customer_name,phone,address\nTEST_UI Rated,+15195550187,200 King St W Kitchener\nTEST_UI Pending,+15195550188,100 Regina St S Waterloo\n"
    job = requests.post(f"{BASE}/api/merchants/mer-2/import?run=evening&consent=true", headers=h, files={"file": ("TEST_ui36.csv", csv, "text/csv")}).json()
    for _ in range(40):
        if requests.get(f"{BASE}/api/imports/{job['id']}", headers=h).json()["status"] != "running":
            break
        time.sleep(1)
    sids = [r["stop_id"] for r in db.import_rows.find({"job_id": job["id"]}) if r.get("stop_id")]
    route = db.routes.find_one({"driver_id": "drv-1", "status": "active"})
    for i, sid in enumerate(sids):
        db.stops.update_one({"id": sid}, {"$set": {"route_id": route["id"], "seq": i}})
    dt = requests.post(f"{BASE}/api/auth/driver-login", json={"phone": "+15195550101", "pin": "482913"}).json()["token"]
    dh = {"Authorization": f"Bearer {dt}"}
    path = requests.post(f"{BASE}/api/upload", headers=dh, files={"file": ("TEST_pod.png", PNG, "image/png")}).json()["path"]
    r = requests.post(f"{BASE}/api/stops/{sids[0]}/deliver", headers=dh, json={"photo_path": path, "signature": "M10,10 L20,20 L30,15"})
    print("DELIVER", r.status_code, r.text[:150])
    for sid in sids:
        s = db.stops.find_one({"id": sid})
        print("STOP", sid, s["tracking_code"], s["status"], s.get("manifest_id"))
    print("ROUTE", route["id"])
else:
    for j in db.import_jobs.find({"filename": {"$regex": "^TEST_"}}):
        ids = [r["stop_id"] for r in db.import_rows.find({"job_id": j["id"]}) if r.get("stop_id")]
        mids = db.stops.distinct("manifest_id", {"id": {"$in": ids}})
        db.pickups.delete_many({"manifest_id": {"$in": mids}, "route_id": {"$exists": True}, "picked_up_at": {"$exists": True}, "merchant_id": "mer-2"}) if mids and len(db.stops.distinct("id", {"manifest_id": {"$in": mids}})) == len(ids) else None
        db.invoices.delete_many({"stop_id": {"$in": ids}})
        db.contact_logs.delete_many({"stop_id": {"$in": ids}})
        db.stops.delete_many({"id": {"$in": ids}})
        db.import_rows.delete_many({"job_id": j["id"]})
        db.import_jobs.delete_one({"id": j["id"]})
        print("cleaned", j["id"], ids, mids)
    db.files.delete_many({"original_filename": "TEST_pod.png"})
    print("left TEST_ stops:", db.stops.count_documents({"customer_name": {"$regex": "^TEST_"}}))
