# UI prep for account deletion: `python ui_deltest_prep.py <sfx>` marks any auto-issued invoice for the throwaway store paid and resets delete_fails.
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
s = sys.argv[1]
mid = f"mer-deltest-{s}"
r = db.invoices.update_many({"merchant_id": mid, "status": {"$in": ["unpaid", "reported"]}}, {"$set": {"status": "paid"}})
db.users.update_many({"id": {"$in": [f"usr-deltest-m-{s}", f"usr-deltest-d-{s}"]}}, {"$unset": {"delete_fails": ""}})
print("invoices marked paid:", r.modified_count, [i["id"] for i in db.invoices.find({"merchant_id": mid}, {"_id": 0, "id": 1})])
print("users:", list(db.users.find({"id": {"$in": [f"usr-deltest-m-{s}", f"usr-deltest-d-{s}"]}}, {"_id": 0, "id": 1, "disabled": 1, "delete_fails": 1})))
