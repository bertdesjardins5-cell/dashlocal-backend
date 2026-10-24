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

## Backlog
- P1: auth/roles; push notifications (on request only)
- P1: SMS delivery-status webhook; auto-SMS customer when driver is 2 stops away or window changes
- P2: dated windows (multi-day); multi-depot; Google/Mapbox routing swap; CSV order import; printable label sheet
