# Repro iter34: a pickup checked in for TODAY's afternoon run (after its 1:30 PM cut-off) is invisible in GET /api/merchants/mer-2 runs[]
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from pathlib import Path
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
today = datetime.now(ZoneInfo("America/Toronto")).date().isoformat()
mid = f"mer-2:{today}:afternoon"
db.pickups.insert_one({"manifest_id": mid, "merchant_id": "mer-2", "route_id": "TEST", "driver_id": "drv-1", "picked_up_at": datetime.utcnow().isoformat() + "Z",
                       "parcel_count": 3, "expected": 3, "on_time": True, "TEST": True})
try:
    t = requests.post(f"{BASE}/api/auth/login", json={"email": "books@arrivewise.app", "password": "Books2026"}).json()["token"]
    m = requests.get(f"{BASE}/api/merchants/mer-2", headers={"Authorization": f"Bearer {t}"}).json()
    aft = [r for r in m["runs"] if r["key"] == "afternoon"][0]
    print("now", datetime.now(ZoneInfo("America/Toronto")).strftime("%H:%M"), "| afternoon run date:", aft["date"], "today:", aft["today"], "| pickup:", aft["pickup"])
    print("in recent_pickups:", any(p["manifest_id"] == mid for p in m.get("recent_pickups", [])))
finally:
    db.pickups.delete_many({"TEST": True})
