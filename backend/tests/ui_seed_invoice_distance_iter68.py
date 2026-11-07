# Seed/cleanup for InvoicesCard distance line UI test (iter68): temporary TEST_ delivery invoices for mer-1 / mer-2.
# usage: python ui_seed_invoice_distance_iter68.py seed | cleanup   (source backend/.env first)
import os
import sys
import json
import uuid
from datetime import datetime, timezone
from pymongo import MongoClient

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
STATE = "/tmp/invoice_distance_iter68.json"


def inv(mid, number, code, km, dist, pri):
    lines = [{"description": "Same-day delivery fee", "amount": 8.99}]
    if dist:
        lines.append({"description": f"Distance ({km} km)", "amount": dist})
    if pri:
        lines.append({"description": "Priority surcharge", "amount": pri})
    sub = round(sum(x["amount"] for x in lines), 2)
    hst = round(sub * 0.13, 2)
    now = datetime.now(timezone.utc).isoformat()
    return {"id": f"TEST_inv_{uuid.uuid4().hex[:10]}", "number": number, "merchant_id": mid, "stop_id": f"TEST_stop_{uuid.uuid4().hex[:8]}",
            "date": now[:10], "issued_at": now, "currency": "CAD", "business": {"hst_pct": 13},
            "merchant": {"name": "TEST", "address": None},
            "delivery": {"tracking_code": code, "customer_name": f"TEST_ Cust {code}", "address": "550 King St N, Waterloo",
                         "delivered_at": now, "delivered_local": now[:16], "km": km, "priority": bool(pri)},
            "lines": lines, "subtotal": sub, "hst_pct": 13, "hst": hst, "total": round(sub + hst, 2),
            "status": "unpaid", "paid_at": None, "paid_note": None}


def seed():
    docs = [inv("mer-1", "TEST-9901", "TB9901", 7.9, 6.12, 0.0), inv("mer-1", "TEST-9902", "TB9902", 1.4, 0.0, 16.0),
            inv("mer-2", "TEST-9903", "TB9903", 14.7, 14.62, 0.0)]
    for d in docs:
        db.invoices.insert_one(dict(d))
    json.dump([d["id"] for d in docs], open(STATE, "w"))
    print([(d["number"], d["id"]) for d in docs])


def cleanup():
    ids = json.load(open(STATE))
    print("invoices deleted", db.invoices.delete_many({"id": {"$in": ids}}).deleted_count)


if __name__ == "__main__":
    {"seed": seed, "cleanup": cleanup}[sys.argv[1]]()
