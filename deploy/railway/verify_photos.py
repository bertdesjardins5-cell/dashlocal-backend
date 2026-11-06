"""Prove every photo in the bucket is the exact original. Read-only.

For each row of photo_manifest.csv (written by copy_files_to_s3.py from the ORIGINAL bytes served by the old app)
it downloads the object from the bucket, decrypts it with ENCRYPTION_KEY and compares size + sha256.
It also checks that every photo a delivery points at, and every live `files` row, is in the manifest.

Usage, from backend/ with the NEW variables exported:
    python ../deploy/railway/verify_photos.py [--manifest photo_manifest.csv]
"""
import csv
import hashlib
import os
import sys

from pymongo import MongoClient

sys.path.insert(0, os.getcwd())
from core import S3_BUCKET, dec_bytes, s3_client  # noqa: E402

MANIFEST = sys.argv[sys.argv.index("--manifest") + 1] if "--manifest" in sys.argv else "photo_manifest.csv"
with open(MANIFEST) as fh:
    rows = {r["storage_path"]: r for r in csv.DictReader(fh)}
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
needed = {f["storage_path"] for f in db.files.find({"is_deleted": {"$ne": True}}, {"_id": 0, "storage_path": 1})}
needed |= {s["pod_photo_path"] for s in db.stops.find({"pod_photo_path": {"$nin": [None, ""]}}, {"_id": 0, "pod_photo_path": 1})}

bad = 0
not_copied = sorted(needed - set(rows))
for p in not_copied:
    print(f"NOT COPIED  {p}")
bad += len(not_copied)
for i, (path, r) in enumerate(sorted(rows.items()), 1):
    try:
        body = dec_bytes(s3_client().get_object(Bucket=S3_BUCKET, Key=path)["Body"].read())
    except Exception as e:
        print(f"MISSING     {path}: {e}")
        bad += 1
        continue
    if len(body) != int(r["size"]) or hashlib.sha256(body).hexdigest() != r["sha256"]:
        print(f"MISMATCH    {path}")
        bad += 1
    if i % 50 == 0:
        print(f"  checked {i}/{len(rows)}")
print(f"\n{len(needed)} photos needed · {len(rows)} in manifest · {bad} problem(s)")
print("RESULT:", "PASS, every photo is byte-identical to the original" if not bad else "FAIL")
sys.exit(1 if bad else 0)
