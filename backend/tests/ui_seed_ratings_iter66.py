# Seed/cleanup for delivery-ratings tests (iter66): clones MH2096 into TEST_ delivered, unrated mer-1 stops on drv-1's H-1023 route.
# usage: python ui_seed_ratings_iter66.py seed | cleanup
import os
import sys
import json
import uuid
from datetime import datetime, timezone
from pymongo import MongoClient

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
STATE = "/tmp/ratings_iter66.json"
ROUTE = "8b6a4d2b-6b59-47ef-bf87-d4ccdacf6327"  # H-1023 Downtown, drv-1 (completed)


def seed():
    base = db.stops.find_one({"tracking_code": "MH2096"}, {"_id": 0})
    out = {}
    for key, code in (("api", "TS9901"), ("ui", "TS9902")):
        doc = {**base, "id": f"TEST_rate_{uuid.uuid4().hex[:10]}", "tracking_code": code, "merchant_id": "mer-1", "route_id": ROUTE, "seq": 99,
               "customer_name": f"TEST_ Rating {key.upper()}", "delivered_at": datetime.now(timezone.utc).isoformat(),
               "rating": None, "rating_comment": None, "rating_tags": [], "rated_at": None, "apology_code": None, "follow_up_status": None,
               "share_token": None, "share_created_at": None, "customer_payment": None, "amount_due": None}
        db.stops.insert_one({**doc})
        out[key] = {"id": doc["id"], "code": code, "last4": "0172"}
    json.dump(out, open(STATE, "w"))
    print(out)


def cleanup():
    st = json.load(open(STATE))
    ids = [v["id"] for v in st.values()]
    print("stops deleted", db.stops.delete_many({"id": {"$in": ids}}).deleted_count)
    print("shoutouts deleted", db.driver_shoutouts.delete_many({"stop_id": {"$in": ids}}).deleted_count)


if __name__ == "__main__":
    {"seed": seed, "cleanup": cleanup}[sys.argv[1]]()
