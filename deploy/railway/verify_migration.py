"""Compare a SOURCE and a DESTINATION MongoDB record-by-record. Read-only on both sides.

Checks:
  1. Every collection: document count, and a content fingerprint (sha256 over every document, keyed by _id),
     so IDs, timestamps, statuses and history must be byte-for-byte identical, not just the counts.
  2. Lists any document that is missing, extra, or different (first 20 per collection).
  3. Relationships in the destination: stop→route, route→driver, stop→merchant, invoice→stop/merchant,
     user→merchant/driver, stop photo→files row, square charge→invoices.
  4. Optional --export-diff DIR: writes the new/changed destination documents per collection as Extended JSON
     files, ready for `mongoimport --mode=upsert --upsertFields=_id` (used for rollback, see the plan).

Usage:
  SRC_MONGO_URL=... SRC_DB=... DST_MONGO_URL=... DST_DB=... python deploy/railway/verify_migration.py [--export-diff DIR]
Exit code 0 = identical and all relationships intact.
"""
import hashlib
import os
import sys

from bson import json_util
from pymongo import MongoClient

src = MongoClient(os.environ["SRC_MONGO_URL"])[os.environ["SRC_DB"]]
dst = MongoClient(os.environ["DST_MONGO_URL"])[os.environ["DST_DB"]]
EXPORT = sys.argv[sys.argv.index("--export-diff") + 1] if "--export-diff" in sys.argv else None


def digest(doc) -> str:
    return hashlib.sha256(json_util.dumps(doc, sort_keys=True).encode()).hexdigest()


def snapshot(coll):
    return {str(d["_id"]): (digest(d), d) for d in coll.find({})}


problems = 0
print(f"{'collection':26}{'source':>9}{'dest':>9}  result")
for name in sorted(set(src.list_collection_names()) | set(dst.list_collection_names())):
    a, b = snapshot(src[name]), snapshot(dst[name])
    missing = [k for k in a if k not in b]
    extra = [k for k in b if k not in a]
    changed = [k for k in a if k in b and a[k][0] != b[k][0]]
    ok = not (missing or extra or changed)
    problems += not ok
    print(f"{name:26}{len(a):>9}{len(b):>9}  {'IDENTICAL' if ok else f'missing {len(missing)} · extra {len(extra)} · changed {len(changed)}'}")
    for label, ids in (("missing in dest", missing), ("extra in dest", extra), ("changed", changed)):
        for k in ids[:20]:
            print(f"    {label}: _id={k} id={(a.get(k) or b.get(k))[1].get('id')}")
    if EXPORT and (extra or changed):
        os.makedirs(EXPORT, exist_ok=True)
        with open(os.path.join(EXPORT, f"{name}.json"), "w") as fh:
            for k in extra + changed:
                fh.write(json_util.dumps(b[k][1]) + "\n")

# relationships: broken links that already exist in the SOURCE are reported as warnings; only links the
# migration itself broke (present in source, broken in destination) fail the check
def broken_links(d):
    ids = lambda c, f="id": {x[f] for x in d[c].find({f: {"$nin": [None, ""]}}, {"_id": 0, f: 1})}
    routes, drivers, merchants, stops, invoices = ids("routes"), ids("drivers"), ids("merchants"), ids("stops"), ids("invoices")
    files = ids("files", "storage_path")
    checks = [
        ("stop.route_id → routes", "stops", "route_id", routes),
        ("stop.merchant_id → merchants", "stops", "merchant_id", merchants),
        ("stop.pod_photo_path → files", "stops", "pod_photo_path", files),
        ("route.driver_id → drivers", "routes", "driver_id", drivers),
        ("invoice.stop_id → stops", "invoices", "stop_id", stops),
        ("invoice.merchant_id → merchants", "invoices", "merchant_id", merchants),
        ("user.merchant_id → merchants", "users", "merchant_id", merchants),
        ("user.driver_id → drivers", "users", "driver_id", drivers),
    ]
    out = {label: {x.get("id") for x in d[c].find({f: {"$nin": [None, ""]}}, {"_id": 0, "id": 1, f: 1}) if x[f] not in target}
           for label, c, f, target in checks}
    out["square_charge.invoice_ids → invoices"] = {c.get("id") for c in d.square_charges.find({"invoice_ids": {"$exists": True}})
                                                   if any(i not in invoices for i in c.get("invoice_ids") or [])}
    return out


print("\nrelationships")
src_broken, dst_broken = broken_links(src), broken_links(dst)
for label, ids_ in dst_broken.items():
    new = ids_ - src_broken[label]
    problems += bool(new)
    pre = len(ids_ & src_broken[label])
    status = "OK" if not new else f"FAIL: {len(new)} broken by the migration, e.g. {sorted(new)[:5]}"
    print(f"  {label:38} {status}{f'  (warning: {pre} already broken in source, e.g. old test data)' if pre else ''}")

newest = lambda d: d.stops.find_one({}, {"_id": 0, "tracking_code": 1, "created_at": 1, "status": 1}, sort=[("created_at", -1)])
print(f"\nnewest delivery  source: {newest(src)}\n                 dest:   {newest(dst)}")
print("\nRESULT:", "PASS, identical" if not problems else f"FAIL, {problems} problem(s) above")
sys.exit(1 if problems else 0)
