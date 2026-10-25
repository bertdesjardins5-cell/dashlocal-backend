"""UI seed for iteration 15: mer-1 two drops delivered last week + 2 weeks ago, mer-2 one drop delivered last week (all unpaid)."""
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
API = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") + "/api"
H = lambda t: {"Authorization": f"Bearer {t}"}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
TZ = ZoneInfo("America/Los_Angeles")
today = datetime.now(TZ).date()
mon = today - timedelta(days=today.weekday())


def tok(e, p):
    return requests.post(f"{API}/auth/login", json={"email": e, "password": p}).json()["token"]


def imp(mid, t, body):
    r = requests.post(f"{API}/merchants/{mid}/import", headers=H(t), files={"file": ("ui.csv", body, "text/csv")})
    r.raise_for_status()
    jid = r.json()["id"]
    for _ in range(40):
        j = requests.get(f"{API}/imports/{jid}", headers=H(t)).json()
        if j["status"] == "done":
            return j
        time.sleep(1.5)


def deliver(name, weeks_ago):
    when = datetime.combine(mon - timedelta(weeks=weeks_ago, days=-2), datetime.min.time(), TZ).replace(hour=12).astimezone(timezone.utc)
    print(name, db.stops.update_one({"customer_name": name}, {"$set": {"status": "delivered", "delivered_at": when.isoformat()}}).modified_count)


m1, m2 = tok("bakery@multidrop.app", "Bakery2026"), tok("books@multidrop.app", "Books2026")
print(imp("mer-1", m1, b"customer_name,phone,address\nTEST_UI15A,4155550193,1 Market St\nTEST_UI15B,4155550194,101 Market St\n"))
print(imp("mer-2", m2, b"customer_name,phone,address\nTEST_UI15C,4155550195,1 Market St\n"))
deliver("TEST_UI15A", 1)
deliver("TEST_UI15B", 2)
deliver("TEST_UI15C", 1)
