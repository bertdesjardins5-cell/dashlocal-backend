# MultiDrop — Multidrop Delivery App (PRD)

## Original problem statement
Build a multidrop delivery app: route optimization, 50m geofencing auto-arrive, real-time GPS sync (WebSockets) to customer tracking. Three tiers — Driver (manifest w/ Next Stop, PoD photo/e-signature/QR, one-tap masked call/text via Twilio), Customer (dynamic ETA, live map, "2 stops away"/"arrived" alerts), Admin (batch/zone by neighborhood/postal, drag-and-drop re-sequencing, analytics drops/hr & route deviation).

## User choices
- Route optimizer: built-in (nearest-neighbor + 2-opt), no API key
- Defaults: role picker (no login), PoD photo + signature, in-app live banners via WebSocket
- Twilio: REAL integration (added 2026-10-24)

## Architecture
- Backend: FastAPI + MongoDB (`/app/backend/server.py`), WebSocket hub (`/api/ws/track/{code}`, `/api/ws/route/{id}`), Emergent Object Storage for PoD photos, Twilio REST (calls bridged with `<Dial callerId>` + SMS from Twilio number)
- Frontend: Expo Router, brutalist high-contrast theme (Space Grotesk + JetBrains Mono), react-native-maps (native) / SVG schematic map (web), draggable-flatlist, expo-location, expo-image-picker

## Implemented
- 2026-10-24: MVP — role picker; driver manifest (Next Stop hero, LIVE GPS / SIMULATE modes, geofence auto-arrive toast); stop detail (photo upload, signature pad, mark arrived, complete drop, failed attempt); customer tracking (WS live ETA, stops-away, alert banner, polling fallback); admin (KPIs, zone batching to driver, route detail drag/arrows resequence, re-optimize, dispatch); analytics (drops/hr, deviation vs planned-so-far, success, geofence %); demo reset.
- 2026-10-24: Twilio masked call/SMS — call rings driver then bridges to customer; SMS quick-reply presets; edit driver/customer phone numbers (E.164 validation); double-tap guard; Twilio logging quieted.
- 2026-10-24: Parcel QR proof of delivery — admin stop sheet shows QR parcel label (MULTIDROP:<code>); driver scan screen (expo-camera, permission flow, manual code fallback); /verify-qr; QR alone completes a drop.
- 2026-10-24: Delivery time windows — 2-hour slots (SF time) set by customer (tracking screen) or admin; past slots blocked; time-window-aware optimizer (lateness → finish time → distance, 10-min buffer, 2-opt + relocate); per-stop planned ETA, LATE badges, late banner; customer ETA includes waiting for window.
- 2026-10-24: Future-day booking — window_date (today..+6 in UI, +13 API); booking another day moves a pending stop off its route; routes have service_date; admin batches by zone + day; future routes plan from 08:00 that day and can't be dispatched early.
- 2026-10-24: Driver day summary — /api/drivers/{id}/summary (drops, failed, on-time rate vs windows, km driven vs planned, drops/hr, PoD mix); summary screen from manifest header / empty / complete states.
- 2026-10-24: Printable label sheet — /api/routes/{id}/labels.html (A4, 8 QR labels per page, server-rendered SVG QR); LABELS button prints via expo-print (native) or new tab auto-print (web).
- 2026-10-24: Weekly scorecard — /api/drivers/{id}/scorecard (7 days: drops, windowed on-time %, drops/hr, km, rating) + /api/scorecards (ranked drivers); week section with bar chart on driver summary; admin "Driver scorecards" list with sparkline; seed adds 6 days of completed history routes (H-MMDD); today views (analytics, routes, track-codes) exclude history.
- 2026-10-24: Labels in loading order — reverse drop order with LOAD nn tags and loading hint.
- 2026-10-24: Customer rating — POST /api/stops/{id}/rating (1–5, once, delivered only, tags as comment); prompt appears on tracking screen as soon as the drop is delivered (WebSocket); avg rating in analytics, summary, scorecards.
- 2026-10-24: Booking confirmation texts — Twilio SMS on window booked/changed/cleared (non-blocking, logged); sms_status returned; seeded fictional 555-01xx numbers are skipped.
- 2026-10-24: Low rating alerts — 1–2★ ratings open a follow-up (follow_up_status/note); GET /api/alerts/low-ratings, POST /api/stops/{id}/follow-up; live push over /api/ws/admin; admin dashboard alert block + follow-up sheet (note, save, resolve).
- 2026-10-24: Delivered proof share — customer tracking shows photo (tap to enlarge), signature and QR-scanned tile after drop-off.
- 2026-10-24: Weekly badges — ON-TIME CHAMP, TOP RATED, FASTEST PACE, MOST DROPS (min sample, ties share) on /scorecards and driver scorecard (+ rank); shown on admin scorecard rows and driver summary.
- 2026-10-24: Badge celebrations — /api/drivers/{id}/badges/new + /badges/seen (lost badges are forgotten so re-earning celebrates again); manifest polls (15s + on focus) and shows full-screen confetti celebration.
- 2026-10-24: Proof share link — POST /api/stops/{id}/share (delivered only, unguessable token) → public HTML page /api/proof/{token} with photo, signature, QR check; customer SHARE PROOF LINK button (native Share / web share or new tab).
- 2026-10-24: Alert trends — GET /api/alerts/trends (7 days, by driver / by zone, low %, top reasons, daily counts); admin dashboard trends block.
- 2026-10-24: Apology texts — 1–2★ rating generates SORRY-XXXXX 15% code and Twilio apology SMS (non-blocking; fictional numbers skipped); code shown to customer and in admin follow-up sheet with SMS status.
- 2026-10-24: Apology code tracking — POST /api/codes/redeem (single use, order value, ref), GET /api/codes/apology (issued/used/rate/revenue/discount/net/avg + rows); admin section with RECORD USE sheet; seed history issues codes, ~half redeemed.
- 2026-06: Admin polish — 44pt coaching chips, long codes/names wrap on small screens, whole-dollar code totals, wider trend bars, coach rows no longer squeeze.
- 2026-06: Daily ops digest — GET /api/digest?date= (drops/hr, deliveries by hour, late drops with minutes late, failed drops, 1–2★ complaints, per-driver); admin DAILY OPS DIGEST block with 7-day picker.
- 2026-06: Reorder alerts — PUT /routes/{id}/sequence keeps finished stops fixed (400 otherwise), sets resequenced_at/resequence_seen=false; driver manifest shows ROUTE UPDATED banner; POST /routes/{id}/update-seen.
- 2026-06: Nearby alerts — when an active route's driver is ≤2 stops away, the customer gets one Twilio SMS (stop.nearby_sms_status; fictional 555-01xx numbers skipped); tracking banner shows "WE'VE TEXTED YOU TOO".
- 2026-06: Merchant CSV import — 4th console MERCHANT (mer-1 Mission Bakery Co., mer-2 Bay Books). POST /merchants/{id}/import (CSV ≤200 rows, flexible headers) → background job geocoding via OpenStreetMap Nominatim (cached in db.geocodes, 1 req/s, SF-bounded) → stops with merchant_id ready for batching. Flagged rows (invalid/not_found/lookup_failed) fixable via PUT /import-rows/{id} or removed via DELETE. GET /merchant-template.csv, GET /imports/{job_id}.
- 2026-06: Merchant login — JWT email+password (bcrypt, 12h session, 5 fails → 15 min lock). Admin seeded from .env; demo merchant logins for mer-1/mer-2. Merchant endpoints require a token for that store (admin sees all). Admin /admin/merchants: create store + login, reset password. Merchant console = sign-in → own store only, sign out.
- 2026-06: Merchant delivery report — POST /merchants/{id}/report-link {range: today|7d|30d|all} → 5-min signed URL GET /merchants/{id}/report.csv (status, times SF, driver, on-time, proof types + 30-day proof link, rating/comment).
- 2026-10-24: Driver coaching notes — POST/GET /api/drivers/{id}/coaching, POST /api/coaching/{id}/read; COACH button on alert-trends driver rows opens sheet with complaint-reason chips + templates; driver manifest shows yellow "Note from dispatch" banner with GOT IT.
- 2026-10-24: Proof link expiry — share_created_at; links valid 30 days (expires_at returned), expired links return 410 page; sharing again after expiry issues a new token.

## Backlog
- P1: auth/roles; push notifications (on request only)
- P1: SMS delivery-status webhook; auto-SMS customer when driver is 2 stops away or window changes
- P2: dated windows (multi-day); multi-depot; Google/Mapbox routing swap; CSV order import; printable label sheet
- 2026-06: Driver login — phone + 6-digit PIN (POST /auth/driver-login; limits 5/phone+IP, 10/phone, 30/IP → 15 min lock). Admin /admin/drivers sets PINs (weak PINs rejected). Driver endpoints scoped (DRIVER_SELF / ROUTE_DRIVER / STOP_DRIVER / ANY_DRIVER); app/driver/_layout.tsx gates driver screens + redirects to own manifest.
- 2026-06: Merchant problem alerts — one email per drop for failed / running late (ETA > window end + 5 min, checked on every route broadcast + 1-min watcher) / delivered late; GET/PUT /merchants/{id}/alerts; db.merchant_alerts.
- 2026-06: Pricing & weekly bills (CAD, payment collected outside the app, no subscriptions) — admin /admin/pricing edits per-delivery fee, km included, per extra km (est. road km from depot = straight line × 1.3), priority surcharge, charge failed drops, sales tax %. Fee snapshot stored on each merchant order (stop.fee) at creation / priority change. Priority drops served first by optimizer. Merchant BILLING card: running bill this week + past 8 weekly statements (Mon–Sun SF, by completion date) + statement CSV. Admin weekly billing per store. CSV column `priority`.
