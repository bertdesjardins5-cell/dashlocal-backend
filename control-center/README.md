# DashLocal Control Center — Phase 1

Separate, **read-only** desktop-oriented React web dashboard backed by the existing DashLocal FastAPI API.

## Safety
- This is a separate application in `control-center/`; it changes **no existing mobile app or backend code**.
- No dispatch, route creation, reset, editing, payment, or messaging API calls are implemented.
- Run against a **non-production backend and test data** first. This branch does not change any deployment.
- The dashboard needs an existing **admin** account. Do not paste login credentials into chat or commit them.
- Auth token is held **in memory only** (it is lost on page reload) and is sent with `Authorization: Bearer ...`.
- Do not host over plain HTTP on the public internet. Production deployment needs HTTPS, safe origin/CORS configuration, and tested server-side role permissions.
- The existing REST endpoints provide current snapshots. Real-time admin WebSocket integration is reserved for Phase 2.

## Run locally
Install Node.js and run in a terminal:

```bash
cd control-center
npm install
cp .env.example .env
npm run dev
```

On Windows PowerShell, use `Copy-Item .env.example .env` in place of `cp`.
Edit `.env` to point to your own **test** backend URL. Use the local URL and port where FastAPI is actually running. Your backend must be reachable from the browser and allow the dev origin via CORS.

Open the Vite local URL, sign in with a test admin account, and view the dashboard. Never use real customer data during initial testing.

## Current API calls
- `POST /api/auth/login` — existing admin login
- `GET /api/auth/me` — verify session role
- `GET /api/analytics` — overall delivery performance
- `GET /api/drivers` — drivers and assigned routes
- `GET /api/routes` — active and upcoming routes
- `GET /api/dispatch/preview?date=YYYY-MM-DD` — dispatch preview
- `GET /api/zones?date=YYYY-MM-DD` — area backlog

The web app refreshes these data automatically every 15 seconds when visible and authenticated. No new backend endpoints are needed for this read-only version.

## Next stages
Confirm the GitHub version matches your currently deployed Emergent backend. Test role permissions and schema response shapes in staging, then add guarded dispatch actions with explicit confirmation and audit logging. Integrate WebSocket updates and map views after the initial dashboard has been verified.
