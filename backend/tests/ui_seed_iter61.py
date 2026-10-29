# Iter 61 UI seed: fake customer payment + tip (TEST_ ids) on a delivered stop for the admin payments screen. `python ui_seed_iter61.py clean` removes it.
import os
import sys
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
if sys.argv[1:] == ["clean"]:
    s = db.stops.find_one({"customer_payment.payment_id": "TEST_ui_pay61"})
    if s:
        db.stops.update_one({"id": s["id"]}, {"$set": {"customer_payment": None, "tips": []}})
    print("cleaned", bool(s))
else:
    s = db.stops.find_one({"status": "delivered", "tracking_code": {"$ne": "MD1002"}}, {"_id": 0, "id": 1, "tracking_code": 1, "route_id": 1})
    drv = (db.routes.find_one({"id": s.get("route_id")}) or {}).get("driver_id") or "drv-1"
    db.stops.update_one({"id": s["id"]}, {"$set": {
        "customer_payment": {"payment_id": "TEST_ui_pay61", "amount": 10.0, "hst_pct": 13.0, "hst": 1.3, "tip": 3.0, "total": 14.3, "paid_at": "2026-01-05T15:00:00Z",
                             "card": "Visa ••1111", "email": "delivered@resend.dev", "tip_driver_id": drv},
        "tips": [{"payment_id": "TEST_ui_tip61", "amount": 5.0, "total": 5.0, "paid_at": "2026-01-06T15:00:00Z", "card": "Visa ••1111", "email": "delivered@resend.dev", "driver_id": drv}]}})
    print("seeded", s["tracking_code"], drv)
