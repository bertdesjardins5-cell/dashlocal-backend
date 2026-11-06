# DashLocal backend on Railway: setup checklist

Files in this folder:
- `Dockerfile`: builds the backend (Python 3.11, FastAPI) the same way it runs today.
- `railway.json`: Railway settings: 1 copy, health check `/health`, restart on failure.
- `requirements.railway.txt`: the backend packages without the two Emergent-only ones.

Nothing here changes how the app runs on Emergent.

## 1. Before you start
- [ ] Push the code to GitHub (Emergent → "Save to GitHub").
- [ ] Get the production database: ask Emergent support (support@emergent.sh) for an export of the live MongoDB. You can also ask them for the uploaded photos and signatures.
- [ ] Copy these **live** values somewhere safe. They must stay **exactly the same**:
  - `ENCRYPTION_KEY`: delivery notes, signatures and photo links can't be read without it.
  - `JWT_SECRET`: if it changes, everyone is signed out once (their passwords still work).

## 2. Database
- [ ] Create a MongoDB database: MongoDB Atlas (free or low-cost tier) or Railway → New → Database → MongoDB.
- [ ] Load the export: `mongorestore --uri "<new connection string>" <export folder>`.
- [ ] Note the database name; it goes in `DB_NAME`.

## 3. Create the Railway service
1. Railway → New Project → Deploy from GitHub repo → pick your repo.
2. Service → Settings:
   - **Root Directory:** leave empty (repository root).
   - **Config-as-code / Railway config file:** `deploy/railway/railway.json`
3. Add the Variables (section 4), then Deploy.
4. Settings → Networking → **Generate Domain**, or add your own (e.g. `api.dashlocal.ca`).
5. Open `https://<your-domain>/health`. It should show `{"status":"ok"}`.

Do **not** raise the replica count above 1. The billing, email and alert jobs run inside the server, so 2 copies means double charges and double emails.

## 4. Variables (Railway → Service → Variables)

### Required
| Name | What it is |
|---|---|
| `MONGO_URL` | Connection string of the new MongoDB |
| `DB_NAME` | Database name |
| `JWT_SECRET` | Same value as production today |
| `ENCRYPTION_KEY` | Same value as production today (critical) |
| `ADMIN_EMAIL` | Admin login email (`admin@dashlocal.app`) |
| `ADMIN_PASSWORD` | Admin login password |
| `CORS_ORIGINS` | Allowed web addresses, comma separated (your Railway/API domain and any web frontend) |

### Payments (Square)
| `SQUARE_ACCESS_TOKEN` | `SQUARE_APPLICATION_ID` | `SQUARE_LOCATION_ID` |
|---|---|---|

### Texts and masked calls (Twilio)
| `TWILIO_ACCOUNT_SID` | `TWILIO_AUTH_TOKEN` | `TWILIO_PHONE_NUMBER` |
|---|---|---|
- Nothing to change in the Twilio console: the app starts calls and texts itself and has no Twilio webhook addresses.

### Email
| Name | Note |
|---|---|
| `EMAIL_FROM_NAME` | `DashLocal` |
| `EMAIL_REPLY_TO` | Optional reply-to address |
| `ADMIN_NOTIFY_EMAIL` | Where admin alerts go |
| `EMERGENT_EMAIL_KEY` | Not needed once `RESEND_API_KEY` is set (section 5) |

### Optional
`NOMINATIM_USER_AGENT` (address lookup, e.g. `dashlocal/1.0 (ops@dashlocal.ca)`), `SKIP_TWILIO_SEND`, `LAUNCH_CLEANUP_PHASE`, `LAUNCH_CLEANUP_DESTRUCTIVE`

### Not needed on Railway
`STRIPE_API_KEY` (unused), `EXPO_PACKAGER_*`, `EXPO_TUNNEL_SUBDOMAIN`, `METRO_CACHE_ROOT`. `PORT` is set by Railway automatically.

## 5. Your own photo storage, email and push alerts
The app switches to these automatically when you set their settings. If they aren't set, it keeps using Emergent's services, so the live Emergent app is unchanged.

### Photo and signature storage: Cloudflare R2 or AWS S3
| Name | What it is |
|---|---|
| `S3_BUCKET` | Bucket name (setting this turns on your own storage) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Access keys with read and write rights on that bucket only |
| `S3_ENDPOINT_URL` | R2 only: `https://<account-id>.r2.cloudflarestorage.com` (leave empty for AWS) |
| `S3_REGION` | AWS region, e.g. `ca-central-1` (R2 uses `auto` automatically) |
- [ ] Keep the bucket **private** (no public access); the app serves files to signed-in users itself.
- [ ] Copy existing files from the Emergent export into the bucket **with the same paths** (they start with `multidrop-delivery/uploads/...`), or old photos won't open.

### Email: your own Resend account
| Name | What it is |
|---|---|
| `RESEND_API_KEY` | Resend "sending access" API key (setting this turns on Resend) |
| `EMAIL_FROM_ADDRESS` | Sender address on your verified Resend domain, e.g. `notifications@dashlocal.ca` |
| `EMAIL_FROM_NAME` / `EMAIL_REPLY_TO` | Already in use: sender name and reply-to |
- [ ] In Resend → Domains, add `dashlocal.ca` and its DNS records (SPF/DKIM); wait until it shows Verified.
- [ ] Free plan: 100 emails/day, 3,000/month. Pick a paid plan if you send more.

### Push alerts: Expo push service (free)
| Name | What it is |
|---|---|
| `PUSH_PROVIDER` | `expo` (turns on Expo push) |
- [ ] The phone apps must be built from **your own Expo project** (`npx eas init`, section 6), so each phone gets an Expo push token.
- [ ] In expo.dev → your project → Credentials, upload the Firebase key (Android, FCM V1 service account) and let EAS create the Apple push key (iOS).
- [ ] Phones register again the next time someone opens the new app version; alerts start reaching them from then on.

`EMERGENT_LLM_KEY`, `EMERGENT_EMAIL_KEY`, `EMERGENT_PUSH_KEY` and `INTEGRATION_PROXY_URL` aren't needed on Railway once these are set.

## 6. Point the phone apps at Railway
- [ ] Create your own Expo account, then in `frontend/` run `npx eas init` (keep the app ID `com.emergent.arrivewise.ue3on3`).
- [ ] Set `EXPO_PUBLIC_BACKEND_URL=https://<your-railway-domain>` for the build (EAS → Environment variables). Keep `EXPO_PUBLIC_FIREBASE_ANDROID_API_KEY`.
- [ ] `npx eas build -p android` and `npx eas build -p ios`, then upload them as a new version in Play Console and App Store Connect.
- [ ] Phones on older versions keep using `arrivewise.emergent.host` until they update, so keep Emergent running until most users have updated.

## 7. Go-live checks
- [ ] `/health` returns ok.
- [ ] Admin, store and driver logins work with the existing passwords.
- [ ] An old delivery's notes and signature open (this proves `ENCRYPTION_KEY` is right).
- [ ] A test order: dispatch → live tracking map moves → delivered.
- [ ] Twilio text and masked call reach a test phone.
- [ ] Square: open a store's billing page and confirm the saved card shows (don't charge).
- [ ] Railway logs show no errors for 24 hours, and only one instance is running.
