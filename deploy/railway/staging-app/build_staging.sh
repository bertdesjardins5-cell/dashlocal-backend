#!/usr/bin/env bash
# Build a DashLocal STAGING phone app pointing at your Railway staging server.
# Works on a separate copy of frontend/, so the live app and Emergent builds are never changed.
#
# Usage (from the repo root, on your computer, logged in with `npx eas login` to YOUR Expo account):
#   STAGING_URL=https://<your-staging>.up.railway.app ./deploy/railway/staging-app/build_staging.sh android   # or: ios
set -euo pipefail
PLATFORM="${1:-android}"
: "${STAGING_URL:?Set STAGING_URL to your https Railway staging address}"
case "$STAGING_URL" in https://*) ;; *) echo "STAGING_URL must start with https://"; exit 1 ;; esac
case "$STAGING_URL" in *emergent.host*|*emergentagent.com*) echo "Refusing: that is the live Emergent server"; exit 1 ;; esac
curl -fsS "$STAGING_URL/health" >/dev/null || { echo "Staging server not reachable at $STAGING_URL/health"; exit 1; }

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT="$ROOT/../dashlocal-staging-app"
echo "Copying frontend to $OUT (the original frontend/ is not touched)"
rm -rf "$OUT"
mkdir -p "$OUT"
(cd "$ROOT/frontend" && tar --exclude=node_modules --exclude=.expo --exclude=.metro-cache --exclude=.env -cf - .) | (cd "$OUT" && tar -xf -)
cp "$ROOT/deploy/railway/staging-app/app.config.js" "$ROOT/deploy/railway/staging-app/eas.json" "$OUT/"
rm -f "$OUT/google-services.json"
printf 'EXPO_PUBLIC_BACKEND_URL=%s\n' "$STAGING_URL" > "$OUT/.env"

cd "$OUT"
yarn install --frozen-lockfile
npx eas init --non-interactive --force >/dev/null 2>&1 || npx eas init   # links the copy to a "dashlocal-staging" project on your account
EXPO_PUBLIC_BACKEND_URL="$STAGING_URL" npx eas build --profile staging --platform "$PLATFORM"
echo "Done. Install the build from the link above. It shows up as 'DashLocal Staging' next to the live app."
