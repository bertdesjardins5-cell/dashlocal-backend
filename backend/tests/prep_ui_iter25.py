# Helper for UI testing (iteration 25): approves a TEST_ signup, mints its set-password token, and makes one reminded review request for mer-1
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def mint(sub, purpose, **ex):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": sub, "role": "merchant", "purpose": purpose, "iat": now, "exp": now + timedelta(hours=2), **ex},
                      os.environ["JWT_SECRET"], algorithm="HS256")


H = {"Authorization": "Bearer " + requests.post(f"{BASE}/api/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}).json()["token"]}
email = f"test+ui{uuid.uuid4().hex[:6]}@resend.dev"
name = "TEST_UI Shop " + uuid.uuid4().hex[:4]
print(requests.post(f"{BASE}/api/signup-requests", json={"store_name": name, "contact_name": "TEST Owner", "email": email, "privacy_consent": True, "consent_timestamp": "2026-01-01T00:00:00Z"}).json())
sr = db.signup_requests.find_one({"email": email})
time.sleep(2)
ap = requests.post(f"{BASE}/api/admin/signup-requests/{sr['id']}/approve", headers=H).json()
print("APPROVE", ap)
u = db.users.find_one({"email": email})
print("EMAIL", email)
print("MID", ap["merchant_id"])
print("SETPW_TOKEN", mint(u["id"], "setpw", n=u["pw_nonce"]))
time.sleep(2)
rr = requests.post(f"{BASE}/api/admin/review-requests", json={"merchant_id": "mer-1"}, headers=H).json()
db.review_requests.update_one({"id": rr["id"]}, {"$set": {"created_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()}})
time.sleep(2)
print("REMIND", requests.post(f"{BASE}/api/admin/review-requests/run-reminders", headers=H).json())
print("RR_ID", rr["id"], db.review_requests.find_one({"id": rr["id"]}, {"_id": 0, "reminder_status": 1, "reminded_at": 1}))
