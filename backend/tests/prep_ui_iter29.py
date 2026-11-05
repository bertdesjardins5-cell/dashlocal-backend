# Iteration 29 UI prep: A,B checkout for DL-000001+2 (B newest), D checkout for DL-000003. Writes /tmp/iter29_sessions.json
import json, os
from pathlib import Path
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]).rstrip("/") + "/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
h = {"Authorization": "Bearer " + requests.post(f"{API}/auth/login", json={"email": "bakery@dashlocal.app", "password": "Bakery2026"}).json()["token"], "X-No-Auto-Auth": "1"}
ids = {i["number"]: i["id"] for i in db.invoices.find({"merchant_id": "mer-1", "number": {"$in": ["DL-000001", "DL-000002", "DL-000003"]}})}
co = lambda nums: requests.post(f"{API}/merchants/mer-1/invoices/checkout", json={"ids": [ids[n] for n in nums]}, headers=h, timeout=60).json()
a = co(["DL-000001", "DL-000002"])
b = co(["DL-000001", "DL-000002"])
d = co(["DL-000003"])
out = {"A": a, "B": b, "D": d, "ids": ids}
for k in "ABD":
    out[k]["db_status"] = db.payment_sessions.find_one({"session_id": out[k]["session_id"]})["status"]
json.dump(out, open("/tmp/iter29_sessions.json", "w"), indent=1)
print(json.dumps({k: (out[k]["session_id"][-10:], out[k]["amount"], out[k]["db_status"]) for k in "ABD"}))
print("A_URL", a["url"]); print("B_URL", b["url"]); print("D_URL", d["url"])
