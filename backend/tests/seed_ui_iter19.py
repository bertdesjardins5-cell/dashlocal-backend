# Seed for iter19 UI retest: reset, then deliver MD1001 as drv-1 with photo + signature
import base64
import os
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
BASE = [l.split("=", 1)[1].strip().strip('"') for l in open("/app/frontend/.env") if l.startswith("EXPO_PUBLIC_BACKEND_URL=")][0].rstrip("/")
API = f"{BASE}/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
db.login_attempts.delete_many({"key": {"$regex": "^(drv|trk)"}})
adm = requests.post(f"{API}/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}).json()["token"]
print("reset", requests.post(f"{API}/admin/reset", headers={"Authorization": f"Bearer {adm}"}).status_code)
drv = requests.post(f"{API}/auth/driver-login", json={"phone": "+14155550101", "pin": "482913"}).json()["token"]
H = {"Authorization": f"Bearer {drv}"}
JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")
up = requests.post(f"{API}/upload", files={"file": ("p.jpg", JPEG, "image/jpeg")}, headers=H)
print("upload", up.status_code)
sid = db.stops.find_one({"tracking_code": "MD1001"})["id"]
body = {"signature": "M10 10 L100 100"}
if up.status_code == 200:
    body["photo_path"] = up.json()["path"]
r = requests.post(f"{API}/stops/{sid}/deliver", json=body, headers=H)
print("deliver", r.status_code, r.text[:200])
db.login_attempts.delete_many({"key": {"$regex": "^(drv|trk)"}})
