# Seed/cleanup for bill-line distance UI test (iter67): two TEST_ delivered mer-1 stops this week (one with extra distance, one without).
# usage: python ui_seed_bill_distance_iter67.py seed | cleanup
import os
import sys
import json
import uuid
from datetime import datetime, timezone
from pymongo import MongoClient

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
STATE = "/tmp/bill_distance_iter67.json"


def seed():
    base = db.stops.find_one({"delivered_at": {"$ne": None}, "status": "delivered"}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()
    out = {}
    for code, km, dist, pri in (("TB9901", 7.9, 6.12, 0.0), ("TB9902", 1.4, 0.0, 16.0)):
        doc = {**base, "id": f"TEST_bill_{uuid.uuid4().hex[:10]}", "tracking_code": code, "merchant_id": "mer-1", "route_id": None,
               "customer_name": f"TEST_ Bill {code}", "delivered_at": now, "status": "delivered", "priority": pri > 0,
               "fee": {"base": 8.99, "km": km, "distance_fee": dist, "priority_fee": pri, "total": round(8.99 + dist + pri, 2),
                       "currency": "CAD", "priced_at": now}, "share_token": None, "customer_payment": None, "amount_due": None}
        db.stops.insert_one({**doc})
        out[code] = doc["id"]
    json.dump(out, open(STATE, "w"))
    print(out)


def cleanup():
    ids = list(json.load(open(STATE)).values())
    print("stops deleted", db.stops.delete_many({"id": {"$in": ids}}).deleted_count)


if __name__ == "__main__":
    {"seed": seed, "cleanup": cleanup}[sys.argv[1]]()
