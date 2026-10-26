# UI seed for iteration 38: python ui_seed_iter38.py seed|clean  (unrated delivered stop TSOUI1 on a drv-1 route for shout-out UI test)
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
if sys.argv[1] == "seed":
    route = db.routes.find_one({"driver_id": "drv-1", "status": {"$ne": "active"}}, {"_id": 0, "id": 1})
    base = db.stops.find_one({"route_id": route["id"], "status": "delivered"}, {"_id": 0})
    base.update({"id": "TEST_SO_ui1", "tracking_code": "TSOUI1", "customer_phone": "+15195550185", "customer_name": "TEST UI Patient",
                 "share_token": None, "rating": None, "rating_tags": [], "rating_comment": None, "rated_at": None,
                 "delivered_at": datetime.now(timezone.utc).isoformat()})
    for k in ("follow_up_status", "apology_code", "apology_sms_status"):
        base.pop(k, None)
    db.stops.insert_one(base)
    db.driver_shoutouts.update_many({"driver_id": "drv-1", "seen_at": None}, {"$set": {"seen_at": datetime.now(timezone.utc).isoformat()}})
    print("seeded", route["id"])
else:
    print(db.driver_shoutouts.delete_many({"stop_id": {"$regex": "^TEST_SO_"}}).deleted_count,
          db.stops.delete_many({"id": {"$regex": "^TEST_SO_"}}).deleted_count,
          db.login_attempts.delete_many({"key": {"$regex": "TSOUI"}}).deleted_count)
