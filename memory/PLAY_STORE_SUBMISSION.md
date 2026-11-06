# DashLocal — Google Play Submission Pack

Package: `com.emergent.arrivewise.ue3on3` · App name: DashLocal · Version 1.0.0

## 1. Links to fill in on Play Console
- Privacy policy URL: `https://arrivewise.emergent.host/api/privacy-policy`
- Terms of Service URL: `https://arrivewise.emergent.host/api/terms`
- Account deletion: users ask from the in-app Privacy screen or email your support address. Play requires a web link, so use the privacy page URL above.

## 2. Data Safety form (answers)
Data is encrypted in transit (HTTPS): **Yes**. Users can ask for their data to be deleted: **Yes**. Data is sold: **No**.

| Data type | Collected | Shared | Purpose | Optional? |
|---|---|---|---|---|
| Precise location (drivers) | Yes | Yes, with the customer tracking their own order (live ETA) | App functionality (route tracking, auto-arrival) | Required for drivers |
| Approximate location | Yes | Same as above | App functionality | Required for drivers |
| Name | Yes | Driver's first name shown to the customer | App functionality, account management | Required |
| Email address (stores/admin) | Yes | No | Account management, communications | Required |
| Phone number | Yes | Shared with Twilio only to send SMS; calls and texts are masked | App functionality, communications | Required |
| Physical address (delivery address) | Yes | Shared with the assigned driver | App functionality | Required |
| Photos (proof of delivery) | Yes | Shown to the merchant and customer for that order | App functionality | Required for drivers |
| Payment info (card) | Yes, entered into Square's secure form. DashLocal never stores card numbers | Shared with Square (payment processor) | Payments | Required to pay |
| Purchase history | Yes | No | App functionality, billing | Required |
| Crash logs / diagnostics | Yes | No | Analytics (app stability) | Collected automatically |
| Device IDs (push token) | Yes | Shared with Firebase/Expo to send notifications | App functionality | Optional |

Third parties to declare as processors: Square (payments), Twilio (SMS), Firebase/Expo (push), Resend (email).

## 3. Location permissions (Android: no background location)
The Android app only uses location **while it is open on screen** (ACCESS_FINE_LOCATION / ACCESS_COARSE_LOCATION).
ACCESS_BACKGROUND_LOCATION and FOREGROUND_SERVICE_LOCATION are blocked in `app.json`, so **no background location declaration or video is needed** in Play Console.
If Play Console still shows an old background location declaration from an earlier draft, answer "No, the app does not access location in the background".

Location use (for the Data safety form): precise location, collected only from drivers during an active route while the app is open, used for live delivery tracking and arrival times. Customers and stores are never located.

## 4. Reviewer access (App access section)
Choose "All or some functionality is restricted" and give:
- **One tap:** Admin → PLAY STORE REVIEWER → "Create reviewer accounts" (reset it on submission day). It shows the store login, driver phone + PIN and 3 customer tracking codes to paste here.
- Note for the reviewer: "Card payments are live. Please don't complete a real payment; all other screens can be tested freely."

Don't give the reviewer the main admin password.

## 5. Other Play Console sections
- **Content rating:** Utility/Productivity, no user-generated public content, no violence. Expect "Everyone".
- **Target audience:** 18+ (drivers and businesses). Not for children.
- **Ads:** No ads.
- **Financial features:** "Payments processed by a third party (Square)". No lending or crypto.
- **Health:** none. **Government:** no. **News:** no.
- **Foreground service type:** Location. Justification: same as section 3.
- **Camera:** used for proof-of-delivery photos and label scanning only. No audio is recorded.

## 6. Before you submit
- Square is already on live keys (real card charges).
- Make sure https://arrivewise.emergent.host/api/privacy-policy, /api/terms and /api/delete-account open in a browser.
- Publish, then build a new Android App Bundle (AAB) from the Publish button.
- Upload to Internal testing first and run one real delivery end to end.

## 7. Account deletion (Play + App Store)
- In app: Merchant Portal → bottom "DELETE MY STORE ACCOUNT"; Driver screen → bottom "DELETE MY ACCOUNT". Confirms with password / PIN.
- Immediate: login stops working, contact details + saved card removed. Billing & delivery records kept without personal details (tax law).
- Blocked (with a clear message) while a store has unpaid bills or open deliveries, or a driver has an assigned route.
- Play Console "Data deletion" URL: https://arrivewise.emergent.host/api/delete-account (deletion is also available in the app).

## 8. iPhone / TestFlight (App Store Connect)
- app.json: ITSAppUsesNonExemptEncryption=false (no export-compliance prompt), iPhone-only (supportsTablet=false → no iPad screenshots needed), no microphone permission requested.
- Needs: Apple Developer account, then Publish → iOS build → TestFlight.
- Review notes: give the reviewer kit logins (Admin → PLAY STORE REVIEWER) and say "Location is only used for drivers during an active route."
- Privacy policy URL: https://arrivewise.emergent.host/api/privacy-policy
