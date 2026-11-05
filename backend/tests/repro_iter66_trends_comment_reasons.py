# Repro (iter66): a 1-star rating with only a free-text comment (no tags) leaks the comment into /alerts/trends top_reasons.
import os
import uuid
import requests
from datetime import datetime, timezone
from pymongo import MongoClient

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
base = db.stops.find_one({"tracking_code": "MH2096"}, {"_id": 0})
sid = f"TEST_rate_{uuid.uuid4().hex[:10]}"
db.stops.insert_one({**base, "id": sid, "tracking_code": "TS9903", "merchant_id": "mer-1", "route_id": "8b6a4d2b-6b59-47ef-bf87-d4ccdacf6327",
                     "customer_name": "TEST_ Trends", "delivered_at": datetime.now(timezone.utc).isoformat(), "rating": None, "rating_tags": [],
                     "rating_comment": None, "rated_at": None, "apology_code": None, "follow_up_status": None})
try:
    tok = requests.post(f"{BASE}/api/track/TS9903/verify", json={"last4": "0172"}).json()["token"]
    r = requests.post(f"{BASE}/api/stops/{sid}/rating", json={"stars": 1, "comment": "TEST_ parcel was soaked, driver never knocked"}, headers={"X-Order-Token": tok})
    print("rate", r.status_code, r.json().get("apology_sms_status"))
    adm = requests.post(f"{BASE}/api/auth/login", json={"email": "admin@localdash.app", "password": "DashLocalAdmin2026"}).json()["token"]
    t = requests.get(f"{BASE}/api/alerts/trends?days=7", headers={"Authorization": f"Bearer {adm}"}).json()
    for row in t["by_driver"]:
        if row["key"] == "drv-1":
            print("drv-1 top_reasons:", row["top_reasons"])
finally:
    print("deleted", db.stops.delete_one({"id": sid}).deleted_count)
