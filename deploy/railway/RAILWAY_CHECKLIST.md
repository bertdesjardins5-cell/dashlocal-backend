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
| `EMERGENT_EMAIL_KEY` | Emergent-only; see section 5 |

### Optional
`NOMINATIM_USER_AGENT` (address lookup, e.g. `dashlocal/1.0 (ops@dashlocal.ca)`), `SKIP_TWILIO_SEND`, `LAUNCH_CLEANUP_PHASE`, `LAUNCH_CLEANUP_DESTRUCTIVE`

### Not needed on Railway
`STRIPE_API_KEY` (unused), `EXPO_PACKAGER_*`, `EXPO_TUNNEL_SUBDOMAIN`, `METRO_CACHE_ROOT`. `PORT` is set by Railway automatically.

## 5. Not working on Railway until replaced (Emergent-only services)
| Feature | Today | Replace with | Variable today |
|---|---|---|---|
| Doorstep photos, signatures, uploads | Emergent storage (`core.py` put_object/get_object) | Cloudflare R2 / AWS S3 | `EMERGENT_LLM_KEY`, `INTEGRATION_PROXY_URL` |
| All emails (receipts, alerts, invites) | Emergent managed Resend (`routes/emails.py`) | Your own Resend account | `EMERGENT_EMAIL_KEY` |
| Push alerts to phones | Emergent push (`routes/push.py`) | Expo push service or Firebase | `EMERGENT_PUSH_KEY` |

Until these three are replaced, deliveries, tracking, logins, billing and texts work, but photo uploads, emails and push alerts fail.

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
