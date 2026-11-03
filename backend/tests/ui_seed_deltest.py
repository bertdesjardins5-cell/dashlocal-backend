# UI seed for account deletion: creates a throwaway TEST store + driver (no blockers). `python ui_seed_deltest.py cleanup <sfx>` removes them by id.
import os
import sys
import uuid
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import hash_pw, TERMS_VERSION, DRIVER_TERMS_VERSION  # noqa: E402

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
if len(sys.argv) > 2 and sys.argv[1] == "cleanup":
    s = sys.argv[2]
    db.users.delete_many({"id": {"$in": [f"usr-deltest-m-{s}", f"usr-deltest-d-{s}"]}})
    db.merchants.delete_one({"id": f"mer-deltest-{s}"})
    db.drivers.delete_one({"id": f"drv-deltest-{s}"})
    print("cleaned", s)
    sys.exit()
s = uuid.uuid4().hex[:8]
phone = "+151955501" + str(10 + uuid.uuid4().int % 80)  # fictional 555-01xx: deletion now texts the driver
db.merchants.insert_one({"id": f"mer-deltest-{s}", "name": f"TEST_UIDel {s}", "contact": "test@example.com"})
db.users.insert_one({"id": f"usr-deltest-m-{s}", "email": f"test_uidel_{s}@example.com", "password_hash": hash_pw("TEST_Pw_123!"), "role": "merchant",
                     "name": "TEST", "merchant_id": f"mer-deltest-{s}", "disabled": False, "terms_version": TERMS_VERSION, "terms_consent": True})
db.drivers.insert_one({"id": f"drv-deltest-{s}", "name": f"TEST_UIDrv {s}", "phone": phone, "vehicle": "TEST", "lat": 43.45, "lng": -80.49})
db.users.insert_one({"id": f"usr-deltest-d-{s}", "role": "driver", "driver_id": f"drv-deltest-{s}", "pin_hash": hash_pw("583917"), "disabled": False,
                     "driver_terms_version": DRIVER_TERMS_VERSION, "phipa_consent_given": True, "consent_version": "2026-06"})
print(s, f"test_uidel_{s}@example.com", phone)
