# DashLocal — Multidrop Delivery App (PRD)

## Original problem statement
Build a multidrop delivery app: route optimization, 50m geofencing auto-arrive, real-time GPS sync (WebSockets) to customer tracking. Three tiers — Driver (manifest w/ Next Stop, PoD photo/e-signature/QR, one-tap masked call/text via Twilio), Customer (dynamic ETA, live map, "2 stops away"/"arrived" alerts), Admin (batch/zone by neighborhood/postal, drag-and-drop re-sequencing, analytics drops/hr & route deviation).

## User choices
- Route optimizer: built-in (nearest-neighbor + 2-opt), no API key
- Defaults: role picker (no login), PoD photo + signature, in-app live banners via WebSocket
- Twilio: REAL integration (added 2026-10-24)

## Architecture
- Backend: FastAPI + MongoDB, split into modules (June 2026 refactor): `server.py` (app, startup, middleware, router includes), `core.py` (config, DB + encryption, auth deps/JWT, storage, geo, constants), `models.py` (Pydantic models + route helpers), `routes/*.py` (pickups, dispatch, stops, feedback, scorecards, auth, merchants, emails, alerts, billing, invoices, payments, onboarding, privacy — each with its own `router`), `services/` (realtime hub, seed). Cross-module calls use module attributes (e.g. `billing.compute_fee`) to avoid circular imports. Route table verified identical to the old single file; WebSocket hub (`/api/ws/track/{code}`, `/api/ws/route/{id}`), Emergent Object Storage for PoD photos, Twilio REST (calls bridged with `<Dial callerId>` + SMS from Twilio number)
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
- 2026-06: Per-store rates — merchants.pricing_override (any subset of pricing fields; empty = shared). get_pricing(merchant_id) layers it. GET/PUT /api/admin/merchants/{id}/pricing. Admin Pricing screen: RATES / CUSTOM RATES button per store opens StoreRatesSheet.
- 2026-06: Bills paid/unpaid — db.bill_payments {merchant_id, week_start, amount, paid_at, by, note}; PUT /api/admin/merchants/{id}/bills/{week}/paid {paid, note} (finished weeks only, 400 for current week). Outstanding = unpaid finished weeks (≤26 back) with total > 0; shown per store + all stores on admin, and TOTAL DUE banner on merchant BillCard.
- 2026-06: Weekly statement email — the Monday 08:00 report email now includes last week's statement (fees, distance, priority, tax, total, paid flag, total due across unpaid weeks) + 7-day statement CSV link (purpose "statement"); subject shows amount due.
- 2026-06: Zone auto dispatch — compass_of(stop) splits by N/S/E/W wedges around the DEPOT (fixed city centre). drivers.compass_zone (demo: drv-1 North, drv-2 South, drv-3 East; West uncovered). GET /api/dispatch/preview?date=, PUT /api/admin/drivers/{id}/compass-zone {zone|null}, POST /api/routes/auto-dispatch {service_date} → one route per driver; uncovered zone → driver with fewest open stops; driver with an open route that day gets stops appended + re-optimised (active → ROUTE UPDATED banner). Admin dashboard AUTO DISPATCH block (zone counts, coverage chips, one button); manual neighbourhood batching kept.
- 2026-06: Balance busy zones — POST /routes/auto-dispatch {balance: true default}: balance_plan moves the far side (by bearing) of the heaviest driver's largest zone to the lightest driver when heavy >= 2x lightest and zone >= 6 stops (repeat ≤4). Response: splits [{zone, from_id, helper_id, moved}], routes[].helping. Admin checkbox "BALANCE BUSY ZONES".
- 2026-06: Bill payment confirmation — PaymentConfirmSheet: shows store/week/amount; marking paid requires a payment note (backend 400 if blank); marking unpaid asks to confirm.

## Test suite note (Jun 2026)
- Fixed time-of-day flakiness in tests/test_qr_windows.py::test_optimize_moves_urgent_stop_earlier (tight window now rolls over the hour correctly).
- Full backend suite: 216/216 pass when run serially (`pytest tests -n0`). Parallel runs (-n 2) cause cross-test failures because suites share one backend and call /api/admin/reset.

## Security audit fixes (Jun 2026)
- Public tracking (/api/track, /ws/track) now returns only status, ETA, stops away and driver first name; no customer name, phone, address, notes, location or proof files. /track-codes no longer lists names.
- Customer unlock: POST /api/track/{code}/verify {last4} (lockout 5/code+IP, 10/code, 30/IP) returns an order token (2h) plus private details; GET /api/track/{code}/private refreshes them.
- Window, rating and share need the X-Order-Token for that stop, or the admin / the stop's driver. Customer responses are trimmed.
- /api/files needs a signed per-file link (?t=) or a staff session (header or ?t=). Proof pages use signed links.
- Not done (user's choice): CORS allowlist, removing the demo code list.

## Customer notes, remembered unlock, launch settings (Jun 2026)
- PUT /api/stops/{id}/customer-note (order token, admin or the stop's driver; pending only, max 200 chars). The driver manifest shows a banner for unseen notes; POST /api/stops/{id}/customer-note/seen clears it. The note is highlighted on the stop screen and the next-stop card.
- The order unlock is stored on the device; the server rejects the order token 7 days after delivery.
- CORS is limited to the CORS_ORIGINS list in backend/.env (dashlocal.app, www, and the preview URL). /api/track-codes and the demo list are removed.

## Instant note alerts, read receipts, saved notes (Jun 2026)
- The driver manifest listens to /ws/route/{id}?token= and refetches on every push, so new notes show instantly with a haptic buzz; polling is kept as a fallback.
- Read receipt: the driver tapping GOT IT or opening the stop sets customer_note_read_at; the public tracking stop has a customer_note_read boolean; the customer sees "DRIVER HAS READ YOUR NOTE".
- Saved notes: GET/PUT/DELETE /api/merchants/{id}/saved-notes (by phone). A customer's last note is kept in db.last_customer_notes. CSV imports prefill customer_note (the store's saved note wins over the last note; the customer can edit it).

## Landing page + brand sign-in + CSV notes + doorstep hint (Jun 2026)
- `/` is now the public DashLocal landing page: hero, how it works, perks, two sign-in cards and a track-order box. Brand colours are new theme keys (brand*), with Poppins headings and PT Serif body.
- `/sign-in/merchant` sends the merchant to /merchant/{id}. `/sign-in/staff` has an Admin/Driver toggle: admin goes to /admin, driver to /driver/{id}. SignInForm and DriverSignInForm are restyled to the brand (also used by the gated layouts).
- The CSV column delivery_note (aliases gate_code, customer_note, dropoff_note, door_note) prefills the order's customer note and upserts the store's saved note.
- GET /api/stops/{id} (driver/admin) returns doorstep_hint {photo_url signed, delivered_at}: the latest earlier delivered proof photo for the same phone and address.

## Shop reviews (Jun 2026)
- Public GET /api/testimonials returns the newest 6 published. Admin CRUD is at /api/admin/testimonials, with the /admin/reviews screen linked from the control centre (delete needs a second tap to confirm). The landing section is hidden until a review is published. No quotes are seeded.

## Review requests + merchant sign-up (Jun 2026)
- Admin: POST /api/admin/review-requests {merchant_id} emails the store logins a one-time /review/{token} link (14 days); requests are listed at GET /api/admin/review-requests. The public GET/POST /api/review-requests/{token} creates a hidden testimonial (from_shop=true, "needs approval" in the admin reviews screen) and emails the admin.
- Public POST /api/signup-requests (5 per IP per hour; same answer for emails that already exist) emails the admin. Admin GET /api/admin/signup-requests, POST /{id}/approve (creates the store and a login, emails a one-time /set-password/{token} link valid 72h), POST /{id}/decline. Screen: /admin/signups.
- GET/POST /api/auth/set-password[/token] sets the password, signs the user in and opens the portal. Admin notices go to ADMIN_NOTIFY_EMAIL or ADMIN_EMAIL. Demo store emails go to the delivered@resend.dev test inbox.
- The landing hero CTA now goes to /sign-up; the merchant card has a "Request an account" button.
- ADMIN_NOTIFY_EMAIL=bertdesjardins@hotmail.com (backend/.env). The delivery of this notice is unverified: the email provider returned 429 rate_limited after a test burst.

## Welcome checklist + review reminders (Jun 2026)
- Stores created by sign-up approval get merchant.onboarding=true. GET /api/merchants/{id}/checklist returns {show, steps[password, upload, note, delivered], done, total}; the steps tick themselves from real data. POST /checklist/dismiss hides it. Shown at the top of the merchant portal (WelcomeChecklist).
- review_reminder_watcher runs hourly: links unused for 7+ days get exactly one reminder email (claimed atomically through reminded_at, skipped if under 12h remain). The fresh token keeps the original expiry. Manual trigger: POST /api/admin/review-requests/run-reminders. The admin screen shows "REMINDER SENT".

## First-delivery growth email (Jun 2026)
- Stores approved from sign-up get approved_via_signup=true and first_delivery_email_at=null. On the first POST /stops/{id}/deliver for that merchant, first_delivery_email_at is claimed atomically and send_first_delivery_email runs in the background: 5 GROWTH_TIPS, CTA to /sign-in/merchant, result in merchant.first_delivery_email_status. Demo/seeded stores never get it.

## Invoices + HST + rate card (Jun 2026)
- Each delivered merchant stop gets an invoice (create_invoice is idempotent with a unique stop_id index; numbers DL-000001 come from db.counters). It snapshots the business (settings.business: name, address, hst_number, hst_pct=13) and the merchant name and address, with lines for base, distance and priority, plus subtotal, HST and total. Existing deliveries are backfilled at startup. Admin reset clears invoices and the counter.
- Status flow: unpaid -> reported (the shop says it paid, POST /merchants/{id}/invoices/report-paid {ids, note}, admin is emailed) -> paid (admin POST /admin/invoices/mark-paid {ids, paid, note}; if no note is given the shop's note is kept; paid=false rejects back to unpaid).
- Merchant: GET /merchants/{id}/invoices?status= returns balances; PUT /billing-address; POST /invoices/{iid}/link returns a 5-min signed /api/invoices/{id}.pdf (reportlab).
- Admin: /admin/invoices screen (To confirm / Unpaid / Paid tabs plus a business details form), GET/PUT /admin/business.
- The merchant portal's BillCard is replaced by InvoicesCard. The weekly email now summarises the week's invoices and the outstanding balance. The admin weekly billing endpoints are still there but the merchant no longer uses them.
- Public GET /api/rate-card (shared pricing plus hst_pct) feeds the landing "Pricing" section.
- HST number and business address are still placeholders (the user skipped them); they can be edited in Admin › Invoices.

## Monthly statement PDF + card payments (Jun 2026)
- POST /merchants/{id}/statements/link {month:YYYY-MM} returns a 5-min signed /api/merchants/{id}/statements/{month}.pdf listing every invoice that month with totals, paid, still owing and account outstanding. Month chips (last 6) and a download button sit in InvoicesCard.
- Card payments: POST /merchants/{id}/invoices/checkout {ids} creates a Stripe Checkout session (CAD, amount computed server-side) and stores it in db.payment_sessions. The success URL is /merchant/{id}?paid={SESSION_ID}. GET /merchants/{id}/payments/{sid} has the server ask Stripe and, when paid with a matching amount, marks the invoices paid once (paid_by=card). The optional webhook /api/webhook/stripe needs STRIPE_WEBHOOK_SECRET. stripe_client() routes the pod test key sk_test_emergent through the Emergent proxy. STRIPE_API_KEY is in backend/.env.
- Double-charge guard: the Emergent Stripe proxy can't expire sessions, so a new checkout first re-checks earlier open sessions with Stripe. The same invoice set reuses the open session's URL (reused=true). An overlapping different set gets 409 until the old one is paid or expires; sessions expire after 31 min. A session Stripe doesn't know is marked missing; other lookup errors give 424. If a session is paid but its invoices were already paid, credit and credit_invoices are recorded and the admin is emailed to refund. A paid session with a different amount gets status amount_mismatch and an admin email.

## 2026-06 — Pricing tiers, KW switch, Pharmacy Addendum A
- Priority rush $16. Standard per-drop $8.99.
- Daily volume tiers (per shop, delivered drops that day): 1-4 $8.99, 5-9 $7.49, 10-14 $6.49, 15+ $5.99; unpaid same-day invoices re-rated; distance + priority on top; custom base_fee stores keep flat fee.
- Depot/clock: Kitchener-Waterloo, America/Toronto; geocoding bounded to KW; seed zones Downtown/Uptown/Westmount/Stanley Park.
- Pharmacy partners (admin toggle + pickup address in Store Rates sheet): $6.25/drop if pickup manifest has 10+ drops else $8.99; 8 km from pharmacy included then $1.25/km; must pick Afternoon (cut-off 1:30, window 2:30-6) or Evening (cut-off 4:30, window 5:30-8:30) run on upload; after cut-off books tomorrow's run; $25/month platform fee invoice (+HST) for pharmacy partners only.
- Backlog: geofence auto-arrival, West compass zone has no default driver in KW seed.

## 2026-06 — Driver run labels, auto arrival, privacy & compliance (PIPEDA/PHIPA)
- Drivers see pharmacy pickup run labels (manifest + stop screen). GPS auto-starts on active route; background (locked phone) tracking via expo-task-manager (native build only).
- Encryption at rest: stops.notes/customer_note/pod_signature + saved_notes.note (Fernet, ENCRYPTION_KEY) and proof photos; HSTS header.
- Driver API views hide phone/fee/consent; drivers can't edit customer phone; drivers must accept consent modal (server-enforced).
- Audit log middleware + /admin/privacy screen; retention (default 730 days, erase PII keep invoice totals, every 6h).
- /privacy policy page (placeholder Privacy Officer privacy@dashlocal.app). Consent modal on sign-up; consent checkbox on CSV upload (stored per order).
- Prescription hand-off: pharmacy drops require uploaded photo AND drawn signature (server-validated).
- Batch progress email: 30 min before each run cut-off, pharmacy pickups with 7–9 drops get one email (db.batch_alerts). Test hook POST /api/admin/batch-alerts/run?at=.
- Customer data requests (admin › Privacy): lookup by phone, JSON export (token link), erase (type ERASE; 409 if open orders); logged in db.data_requests.
- Pharmacy pickup check-in: driver PICKED UP + parcel count per manifest per route (db.pickups); merchant batch-rate tracker shows progress, cut-off countdown and today's pickup status.
- Known gap: erased/retained photos' stored files are unlinked but not deleted from object storage.
- Photo shred on erase/retention (object overwritten empty + db.files.is_deleted; failures flagged shred_pending and retried by retention).
- Missed pickup alert (admin + pharmacy, once per manifest, within 3h after pickup window end). Test hook POST /api/admin/missed-pickups/run?at=.
- Left-store patient SMS on pickup check-in (no medication/pharmacy words; store name sanitized).
- Rating tags stored separately (rating_tags, allowed set incl. DISCREET). Pharmacy weekly summary card + Monday email (GET/POST /api/merchants/{id}/pharmacy-summary[/email]). Driver map: pending pharmacy pickups shown as "P" markers, Pickup First banner, NAVIGATE goes to pickup until checked in.
- Privacy Officer editable in Admin › Privacy (GET /api/privacy/officer, PUT /api/admin/privacy/officer); policy page reads it.
- Driver feedback card on driver summary (GET /api/drivers/{id}/feedback). Pharmacy batch savings badge on invoices card (batch_savings in invoices response).
- Compliment shout-outs: 5★ + positive tag → db.driver_shoutouts; driver manifest banner (GET /shoutouts/new, POST /shoutouts/seen {ids}).

## Security audit fixes (June 2026)
- Demo merchant passwords / driver PINs rejected unless the request Host is a preview host (*.preview.emergentagent.com / *.preview.emergentcf.cloud); changed secrets work everywhere.
- public_base (used in emailed links, Stripe return URLs) only accepts hosts listed in CORS_ORIGINS, forced https.
- /api/files: session access limited to admin, uploader, the stop's route driver, or the stop's merchant. Upload records uploaded_by.
- Deliver: drivers may only attach photos they uploaded that aren't used on another stop (admin exempt).
- /api/coaching/{id}/read requires the note's driver or admin; order tokens now last 30 days.
- Audit #2 (June 2026): delivery signatures must be SVG path data (SIG_RE, ≤200k chars) for every drop; proof/labels pages escape quotes; strict Content-Security-Policy on server-rendered /api HTML (labels print script allowed by sha256 hash); report CSV proof links use trusted base only.
- Launch cleanup (June 2026): /admin/launch screen + GET/POST /api/admin/launch-cleanup (routes/launch.py). Live app only (403 on preview hosts), needs confirm "DELETE". Wipes all stores, drivers, non-admin users, orders, routes, invoices, ratings, photos (shredded), etc.; keeps admin, settings, audit_log, consent_log, data_requests, geocodes. Sets settings.demo_removed → startup never re-seeds demo data and /admin/reset is blocked.
- First-store setup guide (June 2026): /admin/setup + GET /api/admin/setup, POST /api/admin/setup/pricing-reviewed|finish (routes/setup.py). Auto-opens once per session on the admin dashboard (plus banner) when demo_removed is set and steps (pricing reviewed, a merchant login, a driver with PIN) aren't all done. New POST /api/admin/drivers + "Add driver" form on /admin/drivers.
- Phone alerts (Emergent managed push, routes/push.py): POST /api/register-push (auth; own user id only); drivers register on sign-in (src/push.ts via app/driver/_layout.tsx); push "New run assigned"/"Stops added" sent in background from /routes/batch and /routes/auto-dispatch. Tap opens /driver/{id}. EMERGENT_PUSH_KEY=placeholder (set at deploy). Needs frontend/google-services.json + app.json android.googleServicesFile (pending from user). Native build only.
- Privacy Officer set to Albert Desjardins / bertdesjardins@hotmail.com (default in routes/privacy.py + saved setting).
- Sign-up business type (June 2026): Store / Law office / Flower shop / Pharmacy / Other. Pharmacy → PIPEDA/PHIPA ConsentModal (privacy_consent); others → TermsModal + /terms page (terms_consent, TERMS_VERSION). business_type stored on signup_requests and shown in admin Sign-up requests.
- June 2026: Driver welcome SMS on POST /api/admin/drivers (sign-in link + phone + PIN, per owner's choice; result in `invite`). Merchant `business_type` + `signature_required` (default on for law_office at creation/approval); PUT /api/admin/merchants/{id}/settings; deliver refuses without signature when required; driver stop shows SIGNATURE REQUIRED. Admin Stores list: type filter chips, per-store type picker and signature switch.
- June 2026: Signed delivery receipts (routes/receipts.py): after a delivered drop with a signature for a law_office store, every store login gets an email with a 30-day signed link to GET /api/stops/{id}/receipt.pdf?token= (ReportLab PDF incl. drawn signature). stop.receipt_email_at/receipt_email_status. Driver "New PIN by text": POST /api/admin/drivers/{id}/pin/text generates a random PIN, saves it, texts it (returns pin only if the SMS failed).
- June 2026: Receipt archive (law offices): GET /api/merchants/{id}/receipts, POST /receipts/link {stop_id|null} (5-min token), GET /merchants/{id}/receipts.zip; portal card src/components/ReceiptArchive.tsx. Friendly link pages: routes/linkpages.py LinkPageError + exception handler → branded HTML (expired/invalid/wrong/missing + how to get a new one) for receipt.pdf, receipts.zip, statement PDF, invoice PDF, report CSV and proof pages.
- June 2026: Receipt search in ReceiptArchive (client-side: client name, tracking code, dates as 'Oct 27', 2026-10-27, 27/10/2026, month name).
- June 2026: Matter reference (law offices): Stop.matter_ref (CSV column matter_ref/file_number/…, FixRowSheet field, PUT /api/merchants/{mid}/stops/{sid}/matter, MatterTag on portal order rows). Shown on receipt PDF, email subject/body, archive rows + search, ZIP filenames.
- June 2026: Expo Go crash fix — expo-notifications loaded lazily via src/notifications.ts (null on web/Expo Go). matter_ref hidden from driver views (DRIVER_HIDDEN). Security audit #4: PASS.
- Expo Go crash fix v2: src/notifications.ts also checks native modules via requireOptionalNativeModule + try/catch.

## Regression check (post-fork)
- Frontend: all 9 flows pass (iteration_56). Backend: 680 pass; the 28 failures are not real bugs (email provider rate limit, time-of-day window, outdated link-page tests).

## Square payments (Oct 2026) — replaces Stripe
- routes/square.py: hosted card page (/api/square/pay, Web Payments SDK) + /api/square/complete; sandbox/live picked from SQUARE_APPLICATION_ID prefix.
- Merchant autopay: card on file (Square Customers+Cards). Monthly platform fee (Admin › Pricing, same for every store, default $25 + 13% HST) and every Monday all unpaid delivery invoices from before this week, one charge. Retries daily up to 3x; receipt + failure emails.
- Manual invoice pay by card (PAY BY CARD) and customer order pay (store sets amount_due per order or CSV column amount_due; customer pays + HST after the 4-digit unlock; /customer/paid confirmation + email receipt).
- All money goes to DashLocal's single Square account. Before launch: put live keys in the live slot and publish.

## Driver sign-in helpers (Oct 2026)
- Driver phone remembered on the device after sign-in ("Welcome back", PIN only; "Not you?" clears).
- Forgot PIN: POST /api/auth/driver-forgot-pin (generic reply, 10/IP limit, 1 per driver per 10 min). Texts the dispatch mobile (Admin › Drivers setting, GET/PUT /api/admin/dispatch-phone) and emails the admin; Admin › Drivers shows a FORGOT PIN banner with one-tap TEXT NEW PIN.

## Terms re-acceptance (Oct 2026)
- Terms section 3 rewritten: customers pay per order by card; stores' card on file is charged monthly fee + weekly deliveries (CAD incl. 13% HST).
- TERMS_VERSION = "2026-10" (routes/onboarding.py). GET /api/auth/terms-status, POST /api/auth/accept-terms (merchant only; stores version, time, IP, user agent in users.terms_history).
- Merchant portal shows TermsGate modal until accepted; "Sign out" declines. Bump TERMS_VERSION to ask everyone again after future changes.
- Admin › Terms Acceptance (/admin/terms): GET /api/admin/terms-report (stores + drivers), POST /api/admin/terms-report/{merchant_id}/remind (email, 1 per 20h).
- Drivers: DRIVER_TERMS_VERSION "2026-10" (routes/auth.py); TermsGate kind="driver" in app/driver/_layout.tsx blocks the app until accepted (users.driver_terms_*).
- Server-side terms enforcement (core.py): TERMS_VERSION / DRIVER_TERMS_VERSION live in core. driver_consented() (all driver manifest/route/stop/upload deps) 403s until courier terms accepted. merchant_access()/import-row edits 403 non-GET store requests until store terms accepted (exempt: /invoices, /square, /statements, /receipts). tests/conftest.py accepts terms for the suite and restores after; tests/test_terms_enforcement.py.

## Deployment health check (Oct 2026) — PASS (warnings only)
- Fixed blockers: removed startup user rename/delete; seed() inserts only (wipe_demo only via admin reset force=True); retention anonymises saved_notes/contact_logs, keeps audit_log; fee backfill moved to background.
- Remaining store-review advice: in-app account deletion (Apple 5.1.1(v)), reviewer demo account, background-location disclosure/declarations.

## Rebrand DashLocal -> ArriveWise (Oct 2026)
- All customer-facing text, logo wordmark (ARRIVE / WISE, same arrow+D mark), emails, PDFs, link pages, app.json name -> ArriveWise.
- Logins moved to @arrivewise.app (admin@, bakery@, books@); old @dashlocal.app users disabled in preview; seed_users renames @dashlocal.app -> @arrivewise.app on startup (update only).
- ADMIN_EMAIL=admin@arrivewise.app, EMAIL_FROM_NAME=ArriveWise (secrets). Internal keys kept: dashlocal_order_*, dashlocal_driver_phone, dashlocal-driver-location task, dashlocal-square message, DashLocalAdmin2026 password. Scanner accepts ARRIVEWISE:/DASHLOCAL:/MULTIDROP: labels.

## Background location disclosure (Oct 2026)
- src/components/BackgroundLocationDisclosure.tsx: prominent disclosure (what/background/why/who/when it stops) shown before the system "Always"/"Allow all the time" prompt. driverTracking.backgroundStatus() + startBackgroundTracking(ask) only prompts after CONTINUE. Decline = foreground only; status line offers "TURN ON LOCKED-PHONE ARRIVAL" or "OPEN SETTINGS" when blocked. Native builds only (web/Expo Go never show it).

## Android launch crash (Samsung S23) + crash reporting (Oct 2026)
- Guarded BACKEND_URL in src/api.ts (module-level .replace on undefined crashed native launch if the env var was missing). Notification channel promise now caught.
- src/crashReporter.ts (imported first in app/_layout.tsx): global JS error handler saves + POSTs to /api/crash-reports (public, 30/IP/hour); resends pending report next launch. Admin › Crash reports (/admin/crashes, GET /api/admin/crash-reports).

- APK "won't open" fix: Expo SDK 58 + AGP 9 strict R8 strips Room/WorkManager constructors (async-storage uses Room, react-native-maps uses WorkManager) -> native crash before JS. Added expo-build-properties: enableMinifyInReleaseBuilds=false, shrinkResources=false, extraProguardRules keep RoomDatabase/InputMerger/WorkDatabase_Impl <init>. Aligned patch versions via expo install --fix (expo ~58.0.21, RN 0.88.1). expo-camera recordAudioAndroid=false.

## 2026-06 (fork)
- Created /app/memory/PLAY_STORE_SUBMISSION.md (Data Safety answers, background-location justification, reviewer access, console sections).
- Square live switch: no code change needed; square.py auto-detects sandbox by "sandbox-" APPLICATION_ID prefix. Requested live keys via secrets card (keys are shared preview+prod, so preview will also charge real cards).
- Added Play Store reviewer kit (routes/launch.py /api/admin/reviewer-kit, app/admin/reviewer.tsx, link in admin index).
- Added store email when a customer pays an order by card (square.email_store_order_paid). Customer receipts + store charge receipts already existed.
- Driver tips: $2/$3/$5/custom (no HST, max $100) on the Square order page + separate "Tip your driver" after delivery (POST /api/track/{code}/tip-link, kind "tip"). Stored in customer_payment.tip and stops.tips[].
- Refunds: admin /admin/payments screen; GET /api/admin/customer-payments (incl. tips_by_driver), POST /api/admin/stops/{id}/refund {payment_id, amount, reason} full/partial via Square /v2/refunds; customer refund email.
- NOTE: preview Square keys are mismatched (live token + sandbox app/location IDs), so real Square calls fail until user saves Production App ID + Kwik Delivery location ID.
- 2026-10-29: Square LIVE keys verified (Production App ID sq0idp-, location "Kwik Delivery" ACTIVE CAD). Preview + production both charge real cards now.
- Added card_usable(): cards saved in test mode (no "live" flag) are skipped by autopay in live mode; merchant UI shows "add your card again" (card_needs_update).
- Card re-add emails: GET/POST /api/admin/square/card-reminders (stores without a usable card, excl. mer-review; 7-day one-tap card link; 20h cooldown per store). UI: CardReminderCard on /admin/payments.
- Tips REMOVED at user request (tip buttons, tip-link endpoint, TipDriver, tips_by_driver). Any tips already paid stay listed on /admin/payments and remain refundable.
- Manual delivery entry: POST /api/merchants/{id}/orders (create_order, same validation as CSV rows, nothing stored on error) + NewDeliverySheet 'ENTER A DELIVERY' in merchant portal ADD ORDERS section.
- Android crash fix: react-native-maps MapView crashed Android release (no Google Maps API key). Added src/components/RouteMap.android.tsx (Leaflet + OpenStreetMap in WebView, no key). iOS keeps Apple Maps (RouteMap.tsx), web keeps RouteMap.web.tsx.
- Payment locks: no TTL index / no deletes; acquire_lock takes over released or >15-min-old locks, release_locks soft-flags. Auto-dispatch excludes drv-review.
- Account deletion: routes/account.py GET/POST /api/me/delete-account (store: password; driver: PIN). Blocks on unpaid/reported invoices, open stops, driver planned/active route. Soft: users disabled+anonymised, merchant deleted/disabled + contact wiped + square card disabled, driver deleted+anonymised (filtered from dispatch & driver lists). UI: DeleteAccountSheet in merchant portal + driver screen.
- Map fullscreen: ExpandableRouteMap (admin route + driver screen), Modal fullscreen, pinch zoom native/Leaflet.
- iOS prep: app.json ITSAppUsesNonExemptEncryption false, usesNonExemptEncryption false, supportsTablet false, camera/image-picker microphonePermission false.
- Delete lockout: 5 wrong secrets per 15-min window (delete_fails {n, since}, atomic $inc) → 429. Wrong secret = 403 (not 401, so api.ts doesn't sign out). Driver deletion forgets remembered phone.
- Deletion confirmations: store → branded email to its inboxes (read before wipe); driver → Twilio SMS (skips fictional 555 numbers). Map stop details: onMarkerPress on all RouteMap variants (native Marker, web Pressable map-marker-{id}, Android Leaflet postMessage); ExpandableRouteMap 'details' shows bottom card (map-stop-details) in fullscreen; admin route passes customer/address/status/code/window.
- 2026-11 Rebrand ArriveWise -> DashLocal (all text, logo LOCAL/DASH, emails @dashlocal.app logins, contact @dashlocal.ca, domain dashlocal.ca). Package/bundle ID kept com.emergent.arrivewise.ue3on3. auth.py migration renames @dashlocal/@arrivewise.app logins to @dashlocal.app (parks disabled holders as retired+id@retired.dashlocal.invalid). ADMIN_EMAIL + EMAIL_FROM_NAME secrets need updating to admin@dashlocal.app / DashLocal.
- Call From Map: POST /api/stops/{id}/dispatch-call (ADMIN_ONLY) rings settings.dispatch_phone via Twilio and bridges to customer (masked). ExpandableRouteMap onCall prop → 'CALL CUSTOMER (MASKED)' in stop card (admin route only).
- Text From Map: POST /api/stops/{id}/dispatch-text {message} (ADMIN_ONLY, ≤480 chars, 'DashLocal: ' prefix, 555-01xx skipped_fictional, logged in contact_logs). Card: CALL + TEXT buttons, quick replies, KeyboardStickyView.

## Update — Store submission kit + Delivery ratings (Nov 2026)
- PLAY_STORE_SUBMISSION.md rebranded to DashLocal, links set to https://dashlocal.ca/privacy and /terms.
- STORE_LISTING.md: app name, short/full descriptions, subtitle, keywords, category, what's new.
- Store screenshots: scripts/store_screenshots.py → /app/store_screenshots (android 1080x1920, iphone 1290x2796), zip at GET /api/store-screenshots.zip.
- Ratings: customer comment box (300 chars) on RatingPrompt; RatingsCard (store via GET /api/merchants/{id}/ratings, admin via GET /api/admin/ratings, latest 5 + show all, date+time); drivers see comments in their feedback card. Trends reasons now from rating_tags only.
- Captioned screenshots: scripts/caption_screenshots.py → /app/store_screenshots/captioned/{android,iphone}; zip (plain/ + captioned/) at GET /api/store-screenshots.zip.
- Driver badge "5-STAR MONTH" (scorecards.five_star_month): ≥4.8★ avg over last 30 days, min 5 ratings; shows in driver scorecard, admin scorecards and triggers the badge celebration.
- Driver of the Month (routes/awards.py): on the 1st at 08:00 Eastern, emails ADMIN_NOTIFY_EMAIL/ADMIN_EMAIL last month's top-rated driver (min 5 ratings), best 4-5★ comments, top compliments, runners-up. Admin card DriverAwardCard with "Email it now" (5-min cooldown). GET /api/admin/driver-award, POST /api/admin/driver-award/send-now.
- Fixed leftover ARRIVEWISE logos in emails, PDFs (invoices/receipts/payments) and link error pages → DASHLOCAL.
- Public legal pages (routes/legal.py): GET /api/privacy-policy and GET /api/delete-account (HTML, no sign-in) for Play/App Store links. Prod: https://arrivewise.emergent.host/api/privacy-policy and /api/delete-account.
- SIMULATE tracking mode now only in dev builds (__DEV__); customer tracking header says "● LIVE" instead of "LIVE · WEBSOCKET".
- Renamed LocalDash → DashLocal (Nov 2026): app name, logos (DASH red + LOCAL black), emails, PDFs, web pages, store text; logins @dashlocal.app (auth.seed_users migrates @localdash/@arrivewise.app); domain dashlocal.ca. App ID unchanged. QR labels accept DASHLOCAL:/LOCALDASH:/MULTIDROP:.
- Public Terms page GET /api/terms (legal.py, mirrors app/terms.tsx). Play feature graphic 1024x500 via scripts/play_feature_graphic.py → store_screenshots/feature/, included in DashLocal_store_screenshots.zip (feature_graphic/).
