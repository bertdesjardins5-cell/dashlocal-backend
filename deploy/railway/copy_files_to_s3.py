"""Copy every uploaded file (doorstep photos, uploads) from the live Emergent app into your own S3 / R2 bucket.

READ-ONLY on production: it only signs in to the old app and downloads files. Nothing in production is changed.
Run it AFTER the database has been restored into the NEW MongoDB. For each row in the new `files` collection it
downloads the original from the OLD app (served decrypted, as admin), re-encrypts it with the same ENCRYPTION_KEY,
uploads it to the bucket under the SAME path, and writes a checksum manifest (sha256 of the original bytes).
Safe to re-run: paths already in the bucket and in the manifest are skipped (that is how the final sync works).

Usage, from the repo's backend/ folder, with the NEW variables exported in your shell (never paste them in chat):
    OLD_APP_URL=https://arrivewise.emergent.host OLD_ADMIN_EMAIL=... OLD_ADMIN_PASSWORD=... \
    python ../deploy/railway/copy_files_to_s3.py [--dry-run] [--manifest photo_manifest.csv]

Needs: MONGO_URL, DB_NAME (NEW database), ENCRYPTION_KEY (same as production), S3_BUCKET, AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, and S3_ENDPOINT_URL (R2) or S3_REGION (AWS).
"""
import csv
import hashlib
import os
import sys
import time

import requests
from pymongo import MongoClient

sys.path.insert(0, os.getcwd())
from core import S3_BUCKET, enc_bytes, s3_client  # noqa: E402  (the app's own encryption + S3 settings)

DRY = "--dry-run" in sys.argv
MANIFEST = sys.argv[sys.argv.index("--manifest") + 1] if "--manifest" in sys.argv else "photo_manifest.csv"
OLD = os.environ["OLD_APP_URL"].rstrip("/")


def old_admin_token() -> str:
    r = requests.post(f"{OLD}/api/auth/login", json={"email": os.environ["OLD_ADMIN_EMAIL"], "password": os.environ["OLD_ADMIN_PASSWORD"]}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def in_bucket(path: str) -> bool:
    try:
        s3_client().head_object(Bucket=S3_BUCKET, Key=path)
        return True
    except Exception:
        return False


def main():
    if not S3_BUCKET:
        sys.exit("S3_BUCKET is not set")
    done = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as fh:
            done = {r["storage_path"]: r for r in csv.DictReader(fh)}
    db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    # every file the app knows about, plus any photo a delivery points at (in case a files row is missing)
    paths = {f["storage_path"] for f in db.files.find({"is_deleted": {"$ne": True}}, {"_id": 0, "storage_path": 1})}
    paths |= {s["pod_photo_path"] for s in db.stops.find({"pod_photo_path": {"$nin": [None, ""]}}, {"_id": 0, "pod_photo_path": 1})}
    todo = sorted(p for p in paths if not (p in done and in_bucket(p)))
    print(f"{len(paths)} files known · {len(paths) - len(todo)} already copied · {len(todo)} to copy{' (dry run)' if DRY else ''}")
    if DRY or not todo:
        return
    token = old_admin_token()
    failed = []
    new_file = not os.path.exists(MANIFEST)
    with open(MANIFEST, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["storage_path", "size", "sha256", "copied_at"])
        if new_file:
            w.writeheader()
        for i, path in enumerate(todo, 1):
            for attempt in range(3):
                try:
                    r = requests.get(f"{OLD}/api/files/{path}", headers={"Authorization": f"Bearer {token}"}, timeout=60)
                    if r.status_code == 401:
                        token = old_admin_token()
                        raise RuntimeError("token refreshed")
                    if r.status_code == 404:  # erased under the privacy policy, or never finished uploading
                        print(f"SKIP (gone on old app) {path}")
                        break
                    r.raise_for_status()
                    s3_client().put_object(Bucket=S3_BUCKET, Key=path, Body=enc_bytes(r.content), ContentType="application/octet-stream")
                    w.writerow({"storage_path": path, "size": len(r.content), "sha256": hashlib.sha256(r.content).hexdigest(),
                                "copied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                    fh.flush()
                    break
                except Exception as e:
                    if attempt == 2:
                        failed.append(path)
                        print(f"FAILED {path}: {e}")
                    time.sleep(2)
            if i % 25 == 0:
                print(f"  {i}/{len(todo)} · failed {len(failed)}")
    print(f"Done: {len(todo) - len(failed)} processed, {len(failed)} failed. Manifest: {MANIFEST}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
