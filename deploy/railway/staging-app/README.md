# DashLocal Staging phone app

A separate test app, **"DashLocal Staging"**, that talks only to your Railway staging server.

- It installs **next to** the live app: different app ID (`com.emergent.arrivewise.ue3on3.staging`), so it never replaces or touches the store version.
- The build script works on a **copy** of `frontend/`, so the live app and Emergent's Publish builds are never changed.
- It refuses to build if the address points at the live Emergent server, or if the staging server's `/health` doesn't answer.
- Push alerts are **off** in staging, because the live Firebase file only matches the live app ID. Everything else works: sign-in, routes, live map tracking, photos, signatures, ratings and store ordering.

## Build it (on your computer)
1. Install Node 20+ and yarn, then sign in to **your** Expo account: `npx eas login`.
2. From the repo root (after "Save to GitHub" and cloning):
   ```bash
   STAGING_URL=https://<your-staging>.up.railway.app ./deploy/railway/staging-app/build_staging.sh android
   ```
   - The first run asks to create an Expo project called `dashlocal-staging`. Say yes.
   - Android gives you an **APK link**: open it on your phone and install it.
   - iPhone: run the same command with `ios`. It needs your Apple developer account and registers your test iPhone (EAS walks you through it). It's internal-only and never goes to the App Store.
3. Free Expo accounts include a limited number of builds per month, and builds can wait in a queue.

## Test with sample data
- Admin: the staging `ADMIN_EMAIL` / `ADMIN_PASSWORD` you set in Railway.
- Store: `bakery@dashlocal.app` / `Bakery2026`.
- Driver: `(519) 555-0101`, PIN `482913`.
- Customer tracking: open `https://<your-staging>/customer/MD1001`, or use the app's "Track" screen.

Staging has no Square, Twilio or Resend keys, so no real charges, texts or emails go out. Paying with a card shows "payments unavailable"; that's expected.
