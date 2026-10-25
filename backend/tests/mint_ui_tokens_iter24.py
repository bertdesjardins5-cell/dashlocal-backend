# Helper for UI testing (iteration 24): sends a review request for mer-1 and mints review + set-password tokens
import os
import sys
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


tok = requests.post(f"{BASE}/api/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}).json()["token"]
rr = requests.post(f"{BASE}/api/admin/review-requests", json={"merchant_id": "mer-1"}, headers={"Authorization": f"Bearer {tok}"}).json()
print("RR_ID", rr["id"])
print("REVIEW_TOKEN", mint(rr["id"], "review"))
email = sys.argv[1] if len(sys.argv) > 1 else "test+uiapprove@resend.dev"
u = db.users.find_one({"email": email})
if u and u.get("pw_nonce"):
    print("USER_MID", u["merchant_id"])
    print("SETPW_TOKEN", mint(u["id"], "setpw", n=u["pw_nonce"]))
