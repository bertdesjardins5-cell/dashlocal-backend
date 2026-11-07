# Seed/cleanup for iter70: 33 TEST_ pending stops for mer-2 (inside zone) to verify free-zone-count counts >30 open orders.
# usage: python ui_seed_free_zone_bulk_iter70.py seed | cleanup
import os
import sys
import json
import uuid
from datetime import datetime, timezone
from pymongo import MongoClient

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
STATE = "/tmp/free_zone_bulk_iter70.json"
N = 33


def seed():
    base = db.stops.find_one({"status": "pending"}, {"_id": 0}) or db.stops.find_one({}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()
    ids = []
    for i in range(N):
        sid = f"TEST_zbulk_{uuid.uuid4().hex[:10]}"
        doc = {**base, "id": sid, "tracking_code": f"TZB{i:03d}", "merchant_id": "mer-2", "route_id": None,
               "customer_name": f"TEST_ Bulk {i}", "status": "pending", "lat": 43.4201 + (i % 6) * 0.002,
               "lng": -80.4405 + (i // 6) * 0.002, "created_at": now, "delivered_at": None, "arrived_at": None,
               "share_token": None, "customer_payment": None, "amount_due": None, "batch_run": None, "manifest_id": None}
        db.stops.insert_one(doc)
        ids.append(sid)
    json.dump(ids, open(STATE, "w"))
    print("seeded", len(ids))


def cleanup():
    ids = json.load(open(STATE))
    print("stops deleted", db.stops.delete_many({"id": {"$in": ids}}).deleted_count)


if __name__ == "__main__":
    {"seed": seed, "cleanup": cleanup}[sys.argv[1]]()
