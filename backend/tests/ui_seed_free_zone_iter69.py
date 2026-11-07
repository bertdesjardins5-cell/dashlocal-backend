# Seed/cleanup for free-zone map UI test (iter69): TEST_ open (pending) stops for mer-1 and mer-2, inside and outside the zone.
# usage: python ui_seed_free_zone_iter69.py seed | cleanup
import os
import sys
import json
import uuid
from datetime import datetime, timezone
from pymongo import MongoClient

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
STATE = "/tmp/free_zone_iter69.json"
# (merchant, code, lat, lng): mer-1 depot 43.4516,-80.4925 ; mer-2 pharmacy 43.4201,-80.4405
PTS = [("mer-1", "TZ9901", 43.4723, -80.5449),   # Uptown Waterloo ~4.8 km -> outside (red)
       ("mer-1", "TZ9902", 43.4400, -80.4700),   # ~2.2 km -> inside
       ("mer-2", "TZ9903", 43.4250, -80.4500),   # ~0.9 km -> inside (green)
       ("mer-2", "TZ9904", 43.4516, -80.4925)]   # ~5.3 km -> outside (red)


def seed():
    base = db.stops.find_one({"status": "pending"}, {"_id": 0}) or db.stops.find_one({}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()
    out = {}
    for mid, code, lat, lng in PTS:
        doc = {**base, "id": f"TEST_zone_{uuid.uuid4().hex[:10]}", "tracking_code": code, "merchant_id": mid, "route_id": None,
               "customer_name": f"TEST_ Zone {code}", "status": "pending", "lat": lat, "lng": lng, "created_at": now,
               "delivered_at": None, "arrived_at": None, "share_token": None, "customer_payment": None, "amount_due": None,
               "batch_run": None, "manifest_id": None}
        db.stops.insert_one({**doc})
        out[code] = doc["id"]
    json.dump(out, open(STATE, "w"))
    print(out)


def cleanup():
    ids = list(json.load(open(STATE)).values())
    print("stops deleted", db.stops.delete_many({"id": {"$in": ids}}).deleted_count)


if __name__ == "__main__":
    {"seed": seed, "cleanup": cleanup}[sys.argv[1]]()
