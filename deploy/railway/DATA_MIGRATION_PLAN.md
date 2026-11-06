# DashLocal: zero-data-loss migration plan (Emergent → independent hosting)

**Status:** plan only. Nothing has been run, exported, deleted or changed in production.
Every command below is **read-only on production**. Imports only ever go into a **new, empty** database.
No secret values appear here; set them in your shell or password manager, never in chat or Git.

---

## Part 1: Assessment of the current app

### 1.1 Database
- **Provider and type:** MongoDB, hosted by Emergent for the live app. The app reaches it only through `MONGO_URL` + `DB_NAME`, using the async `motor` / `pymongo` drivers.
- **No access from outside:** production has no public connection string. You need Emergent support to get an export or a temporary read-only URI.
- **Not in GitHub:** the GitHub export contains **code only**, with no data, no photos and no secrets.
- **Record IDs:** every record has MongoDB's own `_id` (ObjectId) **and** an app ID `id` (UUID or short code such as `mer-1`, `drv-1`). All links between records use the app `id`, not `_id`.
- **Timestamps** are ISO-8601 text in UTC (`created_at`, `delivered_at`, `arrived_at`, `rated_at`, …). They copy exactly as-is; there is no time-zone conversion.
- **Delivery history:** each delivery (`stops`) holds its own status and timeline fields (`created_at`, `arrived_at`, `delivered_at`, `fail_reason`, ratings, follow-ups). Every API change is also logged in `audit_log`, and every text or call in `contact_logs`.
- **Indexes:** the app creates its 13 indexes itself at startup, and `mongodump` / `mongorestore` copy them too.

**Collections** (counts from the preview; production will differ):

| Group | Collections |
|---|---|
| Deliveries | `stops` (deliveries: customer, address, status, timeline, proof), `routes` (driver runs), `pickups`, `missed_pickup_alerts`, `batch_alerts`, `merchant_alerts` |
| People | `users` (logins, bcrypt password hashes), `drivers`, `merchants` (stores), `signup_requests`, `login_attempts` |
| Photos | `files` (one row per uploaded file: `storage_path`, `size`, `content_type`, `uploaded_by`, `is_deleted`) |
| Money | `invoices`, `square_charges`, `square_locks`, `bill_payments`, `payment_sessions`, `code_redemptions`, `counters` (invoice numbers) |
| Messages | `contact_logs`, `report_emails`, `award_emails`, `review_requests`, `push_devices` |
| Privacy | `consent_log`, `data_requests`, `audit_log` |
| Other | `settings`, `geocodes`, `import_jobs`, `import_rows`, `saved_notes`, `last_customer_notes`, `coaching_notes`, `driver_shoutouts`, `testimonials`, `crash_reports` |

**How records link** (all by app `id`):
```
merchants.id ─┬─< stops.merchant_id            users.merchant_id >─ merchants.id
              └─< invoices.merchant_id         users.driver_id   >─ drivers.id
drivers.id ───< routes.driver_id               routes.id ───< stops.route_id
stops.id ─────< invoices.stop_id, contact_logs.stop_id
stops.pod_photo_path ──> files.storage_path ──> object in photo storage
square_charges.invoice_ids[] ──> invoices.id     square_charges.payment_id ──> Square
```

### 1.2 Photos and proof of delivery
- **Doorstep photos and uploaded files:**
  1. The driver's app uploads the photo first (`POST /api/upload`).
  2. The server encrypts it (Fernet, using `ENCRYPTION_KEY`) and stores it in **Emergent object storage** at `multidrop-delivery/uploads/pod/<uuid>.<ext>`.
  3. It records the file in the `files` collection.
  4. When the driver finishes the stop, that path is saved on the delivery as `stops.pod_photo_path`.
- **Signatures aren't files:** they are stored **inside the delivery record** (`stops.pod_signature`, encrypted). They move with the database.
- **Links never contain the storage address:** the app only stores the *path* and creates a short-lived signed link when someone views it (`/api/files/<path>?t=…`, signed with `JWT_SECRET`). **No references need rewriting.** Keep the same paths and the same `JWT_SECRET` / `ENCRYPTION_KEY`, and every link works.
- **Getting the originals out:** the app's own download route (`GET /api/files/<path>`, admin sign-in) returns the original, decrypted file. `copy_files_to_s3.py` uses this, so you don't depend on raw storage access. Emergent support can also provide a raw storage export.
- **Erased photos:** files erased by the privacy retention job are flagged `is_deleted` and emptied on purpose. They are skipped, which is correct.

### 1.3 Logins
- **Own login system, no outside provider:** `users` holds bcrypt password hashes, store and admin logins are email + password, and driver logins are phone + PIN. Sign-in tokens are JWTs signed with `JWT_SECRET`.
- **Existing users keep working** after the move, because their password hashes travel with the database.
- **Keep `JWT_SECRET` the same** so nobody is signed out and photo links stay valid. If it changes, people sign in once more and nothing is lost.
- **`ENCRYPTION_KEY` is critical:** without the exact value, notes, signatures and photos can't be read.

### 1.4 How driver updates reach the server, and what happens offline
- **No offline queue:** the app has no local outbox. Every driver action (arrive, deliver, fail, photo upload, signature) is a direct call to the server.
  - If the phone is offline, the action fails on screen and the driver taps again. Nothing is silently stored on the phone to be sent later.
  - So there is **no hidden backlog** to lose during a move, as long as drivers aren't on a route during the window.
- **Retries:** data screens retry up to 3 times. Actions (delivering, uploading) are **not** retried automatically, so they can't be sent twice by accident.
- **Photo then delivery, two steps:** if the upload succeeds but finishing the stop fails, an unused `files` row is left. The copy script still copies it, so nothing breaks.
- **Location pings:** fire-and-forget, every few seconds while a route is active. Pings lost during the window only mean a short gap on the map.
- **Crash reports** are queued on the phone and re-sent, and duplicates are harmless.

### 1.5 Things that could cause missing or duplicated deliveries
| # | Risk | Effect | Prevention in this plan |
|---|---|---|---|
| R1 | Writes to the old app after the final export (orders, deliveries, photos) | Missing | Maintenance window with no active routes; final export *after* writes stop; a fingerprint check proves the copy is identical |
| R2 | Phones on old app versions still talking to the old server | Missing (saved on the wrong server) | New builds approved and held **before** the window; old server kept read-only in practice; daily check of the old admin for stray orders |
| R3 | Both servers running background jobs (Monday auto-billing, weekly emails, monthly fee, late alerts) | Duplicate charges or emails | Destination stays **stopped** until cutover; old deployment shut down before the next scheduled job; Square idempotency keys as a backstop |
| R4 | Wrong `ENCRYPTION_KEY` | Unreadable notes, signatures, photos | Verified by test before going live |
| R5 | Photo copied but corrupted or missed | Broken photo on a delivery | sha256 manifest plus `verify_photos.py` re-check of every file |
| R6 | Re-importing on top of a non-empty destination | Mixed or duplicate data | Always restore into a **new empty database name**; never `--drop`, never touch production |
| R7 | Stores bulk-importing during the window | Missing | Announce the window; check `import_jobs` is idle before export |
| R8 | No "last updated" field on deliveries | A partial "only changes" sync would miss updates | Do a **full** re-export in the window (the database is small); the fingerprint diff catches any difference |
| R9 | Links in texts and emails already sent point at `arrivewise.emergent.host` | Old tracking links stop working once the old app is shut down | Keep the old app up 30 days, or point that address at the new server if Emergent allows it |

---

## Part 2: Where to move it (low cost, works with the app as-is)

| Need | Recommendation | Approx. cost | Why |
|---|---|---|---|
| Database | **MongoDB Atlas** M0 (free, 512 MB) to start, then M10 (about US$57/mo) or Flex (about US$8–30/mo) for backups | $0–30/mo | Same MongoDB, no code change, daily backups on paid tiers, Toronto region (`ca-central-1`) for Canadian data |
| Alternative | Railway MongoDB plugin | About $5–10/mo | Simplest, but backups are your job (scheduled `mongodump`) |
| Photos | **Cloudflare R2** (private bucket) | 10 GB free, then $0.015/GB, **no download fees** | S3-compatible; the app already supports it (`S3_BUCKET`, `S3_ENDPOINT_URL`) |
| Alternative | AWS S3 `ca-central-1` | About $0.025/GB + download fees | Canadian region |
| Backend | Railway, 1 replica (`deploy/railway/`) | About $5–20/mo | Already prepared |

---

## Part 3: Migration procedure

Tools on your computer: `mongodump`, `mongorestore`, `mongoimport` ([MongoDB Database Tools](https://www.mongodb.com/try/download/database-tools)), Python 3.11, and the repo from GitHub. In the repo's `backend/`, run `pip install -r ../deploy/railway/requirements.railway.txt`.

Shell variables (fill in privately; shown here by name only):
```
SRC_URI     # read-only production URI from Emergent support (or skip and use their dump folder)
SRC_DB      # production database name (ask support)
DST_URI     # new Atlas connection string
DUMP        # a local folder, e.g. ./dump-YYYYMMDD-HHMM
```

### Step 0: Ask Emergent support (1–2 weeks ahead)
Email support@emergent.sh asking for:
1. a **read-only** connection string, or a `mongodump` archive, of the production database for `arrivewise.emergent.host`, plus its database name;
2. the exact live values of `ENCRYPTION_KEY` and `JWT_SECRET` (delivered securely; store them in a password manager);
3. optionally, a raw export of the object storage (a second copy of the photos);
4. whether they can briefly **pause the production backend** during your window, or later point `arrivewise.emergent.host` at your new server.

### Step 1: Set up the destination (no user impact)
1. Create the Atlas cluster in `ca-central-1` and a database user with access to the **new** database only. Allow your IP and Railway in Network Access.
2. Create the private R2 bucket and an API token limited to that bucket.
3. Create the Railway service with all variables, and keep it **stopped** (no deployment running), so its background jobs can't run against anything.

### Step 2: Rehearsal (a few days before; production is only read)
```bash
# 1. Export production (read-only). Writes a local folder; changes nothing in production.
mongodump --uri "$SRC_URI" --db "$SRC_DB" --out "$DUMP"

# 2. Import into a NEW, EMPTY rehearsal database (never --drop, never production)
mongorestore --uri "$DST_URI" --nsFrom "$SRC_DB.*" --nsTo "dashlocal_rehearsal.*" "$DUMP"

# 3. Prove the copy is identical: every document, ID, timestamp and status, plus relationships
SRC_MONGO_URL="$SRC_URI" SRC_DB="$SRC_DB" DST_MONGO_URL="$DST_URI" DST_DB=dashlocal_rehearsal \
  python deploy/railway/verify_migration.py

# 4. Copy photos into the bucket (reads from the old app as admin; resumable; writes photo_manifest.csv)
cd backend
export MONGO_URL="$DST_URI" DB_NAME=dashlocal_rehearsal ENCRYPTION_KEY=... S3_BUCKET=... AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... S3_ENDPOINT_URL=...
OLD_APP_URL=https://arrivewise.emergent.host OLD_ADMIN_EMAIL=admin@dashlocal.app OLD_ADMIN_PASSWORD=... \
  python ../deploy/railway/copy_files_to_s3.py --dry-run     # shows counts only
OLD_APP_URL=... OLD_ADMIN_EMAIL=... OLD_ADMIN_PASSWORD=... python ../deploy/railway/copy_files_to_s3.py

# 5. Prove every photo is byte-identical to the original
python ../deploy/railway/verify_photos.py
```
6. Start Railway **temporarily** pointed at `dashlocal_rehearsal` with `SKIP_TWILIO_SEND=1` and **no** Square or Resend keys, so no real texts, emails or charges go out. Then:
   - sign in as admin, a store and a driver with their existing passwords;
   - open three old deliveries: notes, signature and photo all show.
7. Stop Railway. Write down how long steps 1, 2 and 4 took; that sets your window length.

`verify_migration.py` passes only when **every collection is IDENTICAL** and no relationship was broken by the copy. Links already broken in production (for example old test data) are listed as warnings.

### Step 3: Phone apps ready before the window
Build new versions with `EXPO_PUBLIC_BACKEND_URL=https://<railway domain>` on your own Expo account, get them approved, and **hold the release** (manual release in both stores).

### Step 4: Cutover (maintenance window; suggested Sunday 21:00–23:00 Eastern, after deliveries, before Monday's 08:00 billing)

| When | Action | Check |
|---|---|---|
| 1 day before | Email stores: no ordering or importing 21:00–23:00 Sunday | n/a |
| T-30 | Old admin: no active routes, no deliveries "en route", no store imports running, no Square charge "pending" | All finished or rescheduled |
| **T-0** | **Stop writes:** drivers and stores stop using the app. Ideally Emergent support pauses the production backend (best guarantee). Note the exact time. | Old admin shows no new activity |
| T+5 | **Final export:** `mongodump --uri "$SRC_URI" --db "$SRC_DB" --out "$DUMP-final"` | Folder created |
| T+15 | **Import into a fresh database:** `mongorestore --uri "$DST_URI" --nsFrom "$SRC_DB.*" --nsTo "dashlocal.*" "$DUMP-final"` (new name `dashlocal`, empty) | No errors |
| T+20 | **Verify records:** `verify_migration.py` with `DST_DB=dashlocal` | PASS, all IDENTICAL |
| T+25 | **Photos, final sync:** run `copy_files_to_s3.py` with `DB_NAME=dashlocal`. It copies only files added since the rehearsal (same manifest file). | 0 failed |
| T+35 | **Verify photos:** `verify_photos.py` | PASS |
| T+40 | **Start Railway** with `DB_NAME=dashlocal`, all real keys, 1 replica. `GET /health` → ok | ok |
| T+45 | **Test the destination:** sign in as admin, store and driver; open an old delivery's photo and signature; place one test order, then deliver it with a photo; check the customer text and tracking page; check Admin → Billing shows the same invoices | All good |
| T+55 | **Go/no-go.** If GO: release the held app versions, and tell stores and drivers to update and resume | n/a |
| T+60 | Keep the old app paused or unused. Make sure its scheduled jobs can't run before Monday 08:00 (ask support to stop it, or keep it paused) | n/a |

**Catching anything written late:** if anyone used the old app after T-0, compare its database with your snapshot to see exactly what changed:
```bash
# restore the newer old-app export into a scratch DB, compare with the cutover snapshot, export only the differences
mongorestore --uri "$DST_URI" --nsFrom "$SRC_DB.*" --nsTo "dashlocal_late.*" "$DUMP-late"
SRC_MONGO_URL="$DST_URI" SRC_DB=dashlocal_snapshot DST_MONGO_URL="$DST_URI" DST_DB=dashlocal_late \
  python deploy/railway/verify_migration.py --export-diff late_changes/
```
`dashlocal_snapshot` is a second restore of `$DUMP-final`, kept untouched. Review `late_changes/*.json`, then import them **by ID** (existing records updated, new ones added, nothing deleted):
`mongoimport --uri "$DST_URI" --db dashlocal --collection stops --mode upsert --upsertFields _id --file late_changes/stops.json`
For late photos, re-run `copy_files_to_s3.py`.

### Step 5: Rollback

**Before T+55 (apps not released):** nothing has changed for users.
1. Stop Railway.
2. Ask support to un-pause the old backend.
3. Tell stores to resume.
4. The new database and bucket stay as they are; delete them later at your choice.

**After T+55 (people are using the new server):** new deliveries now exist only on Railway, so a rollback must carry them back.
1. Pause writes on Railway: scale to 0, or announce a short pause.
2. Export what changed since cutover, comparing the frozen snapshot with live Railway:
   ```bash
   SRC_MONGO_URL="$DST_URI" SRC_DB=dashlocal_snapshot DST_MONGO_URL="$DST_URI" DST_DB=dashlocal \
     python deploy/railway/verify_migration.py --export-diff rollback_changes/
   ```
3. Give `rollback_changes/` to Emergent support to import **with upsert by `_id`** into production. This adds new deliveries and updates changed ones, and deletes nothing.
4. Photos taken after cutover live in your bucket. Run `copy_files_to_s3.py` in reverse with support's help, or keep the R2 bucket and set `S3_BUCKET` on the old app if Emergent allows.
5. Re-release builds pointing at `arrivewise.emergent.host`. Phones that never updated are already pointing there.
6. Check the old admin shows the post-cutover deliveries before resuming.

**Rule:** decide go/no-go at T+55. After that, prefer fixing forward on Railway; rollback is for real emergencies only.

### Step 6: After cutover
- **Day 1:** confirm the morning routes, texts, photos and the late-alert emails all work on Railway.
- **First Monday:** Admin → Billing shows **one** auto-charge per store; compare with the Square dashboard.
- **Daily for 2 weeks:** open the old admin and look for stray new orders. If you find any, re-enter them or use the late-changes procedure.
- **Backups:** turn on Atlas daily backups (paid tier), or schedule a nightly `mongodump`. Turn on R2 object versioning.
- **Day 30:** ask Emergent to shut down the old app once old links and old app versions are no longer used.

---

## Scripts (in `deploy/railway/`, all read-only on production)
| Script | Does |
|---|---|
| `verify_migration.py` | Compares two databases document by document (sha256 fingerprint per record, so IDs, timestamps and statuses must match exactly), lists missing, extra or changed records, checks relationships, and with `--export-diff` writes the differences as upsert-ready files |
| `copy_files_to_s3.py` | Downloads originals from the old app as admin, re-encrypts them with the same key, uploads them to the bucket under the same path, and writes a sha256 manifest. Resumable; also used for the final sync |
| `verify_photos.py` | Re-downloads every photo from the bucket, decrypts it, and checks size + sha256 against the manifest. Flags any delivery photo that was never copied |
