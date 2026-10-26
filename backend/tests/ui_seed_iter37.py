# UI seed for iteration 37: python ui_seed_iter37.py seed|clean  (driver-feedback stops on drv-1, pharmacy batch invoices on mer-2)
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
if sys.argv[1] == "seed":
    route = db.routes.find_one({"driver_id": "drv-1"}, {"_id": 0, "id": 1})
    now = datetime.now(timezone.utc)
    for i, (st, tags) in enumerate([(5, ["ON TIME", "DISCREET"]), (5, ["HANDLED WITH CARE"]), (2, ["LATE"])]):
        base = db.stops.find_one({"route_id": route["id"]}, {"_id": 0})
        base.update({"id": f"TEST_uifb_{i}", "tracking_code": f"TSTFB{i}", "status": "delivered", "customer_name": "TEST UI Patient", "share_token": None,
                     "rating": st, "rating_tags": tags, "rated_at": (now - timedelta(minutes=i)).isoformat()})
        db.stops.insert_one(base)
    d = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
    for i in range(4):
        db.invoices.insert_one({"id": f"TEST_uiinv_{i}", "number": f"TEST-UI-{i}", "kind": "delivery", "merchant_id": "mer-2", "stop_id": f"TEST_uiinv_{i}",
                                "date": d, "tier": "Pharmacy batch", "total": 7.06, "subtotal": 6.25, "hst": 0.81, "status": "paid", "lines": [],
                                "delivery": {"tracking_code": "TEST", "customer_name": "TEST", "address": "TEST"}})
else:
    print(db.stops.delete_many({"id": {"$regex": "^TEST_uifb_"}}).deleted_count, db.invoices.delete_many({"id": {"$regex": "^TEST_uiinv_"}}).deleted_count)
    db.settings.update_one({"key": "privacy"}, {"$unset": {"value.officer_name": "", "value.officer_email": ""}})
