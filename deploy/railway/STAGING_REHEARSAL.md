# DashLocal staging rehearsal on Railway (sample data only)

This runs entirely in **your** Railway project `dashlocal-staging`, with your private R2 bucket `dashlocal-staging-photos`.
It never touches the live Emergent app, its database or its photo storage.
Credentials are entered only in Railway's Variables screen, never in chat or Git.

## 1. Database
Railway → project `dashlocal-staging` → **New → Database → MongoDB**. After it starts, its `MONGO_URL` is available as a reference variable.

## 2. Backend service
1. **New → GitHub Repo** → your DashLocal repo (push it first with Emergent's "Save to GitHub").
2. Service → Settings:
   - Root Directory: *(empty)*
   - Config file path: `deploy/railway/railway.json`
3. Service → **Variables** (Raw Editor). Paste the block below and fill in your own values:
```
MONGO_URL=${{MongoDB.MONGO_URL}}
DB_NAME=dashlocal_staging
JWT_SECRET=<any new long random text: staging only>
ENCRYPTION_KEY=<a NEW Fernet key: staging only, see below>
ADMIN_EMAIL=admin@dashlocal.app
ADMIN_PASSWORD=<a staging-only password>
CORS_ORIGINS=*
S3_BUCKET=dashlocal-staging-photos
AWS_ACCESS_KEY_ID=<R2 staging access key id>
AWS_SECRET_ACCESS_KEY=<R2 staging secret>
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
SKIP_TWILIO_SEND=1
PUSH_PROVIDER=expo
```
   - Make a staging `ENCRYPTION_KEY` on any computer with Python: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - Leave out **all** Square, Twilio and Resend keys, so staging can't charge cards, send texts or send email.
4. Deploy. With an empty database, the app automatically creates the **sample** stores, drivers, routes and deliveries on first start.
5. Settings → Networking → **Generate Domain**.

## 3. Rehearsal checks (your phone or computer browser)
| # | Check | Expected |
|---|---|---|
| 1 | `https://<staging-domain>/health` | `{"status":"ok"}` |
| 2 | `https://<staging-domain>/api/privacy-policy` | Privacy page loads |
| 3 | Sign in as admin: `POST /api/auth/login` with the staging admin email/password (or point a dev build at the staging domain) | 200 + token |
| 4 | Sample driver: `(519) 555-0101`, PIN `482913` | Signs in, route listed |
| 5 | Upload a photo as the driver (`POST /api/upload`) | Returns a path; the object appears in R2 → `dashlocal-staging-photos` → `multidrop-delivery/uploads/pod/…` (encrypted, unreadable in R2: correct) |
| 6 | Download it as admin (`GET /api/files/<path>`) | Same image comes back |
| 7 | Railway → Deployments → Logs | No errors; exactly **1** replica |

## 4. Record and photo verification (optional, from your computer)
With the Railway MongoDB public URL (MongoDB service → Connect → Public network):
```bash
# compare staging with a second restore of itself: proves the tools work before the real move
mongodump --uri "<staging public MONGO_URL>" --db dashlocal_staging --out ./staging-dump
mongorestore --uri "<staging public MONGO_URL>" --nsFrom "dashlocal_staging.*" --nsTo "dashlocal_staging_copy.*" ./staging-dump
SRC_MONGO_URL="<url>" SRC_DB=dashlocal_staging DST_MONGO_URL="<url>" DST_DB=dashlocal_staging_copy \
  python deploy/railway/verify_migration.py        # expect: RESULT: PASS
```

## 5. Clean up
Delete the `dashlocal_staging_copy` database when you're done. You can remove the whole `dashlocal-staging` project at any time; nothing else depends on it.
