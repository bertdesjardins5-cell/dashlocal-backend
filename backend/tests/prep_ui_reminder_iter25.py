# Helper for UI testing (iteration 25): one reminded review request for mer-1 (run after prep_ui_iter25.py if it hit the email rate limit)
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
H = {"Authorization": "Bearer " + requests.post(f"{BASE}/api/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}).json()["token"]}
r = requests.post(f"{BASE}/api/admin/review-requests", json={"merchant_id": "mer-1"}, headers=H)
print(r.status_code, r.text[:200])
rr = r.json()
db.review_requests.update_one({"id": rr["id"]}, {"$set": {"created_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()}})
time.sleep(3)
print("REMIND", requests.post(f"{BASE}/api/admin/review-requests/run-reminders", headers=H).json())
print("RR_ID", rr["id"], db.review_requests.find_one({"id": rr["id"]}, {"_id": 0, "reminder_status": 1, "reminded_at": 1}))
