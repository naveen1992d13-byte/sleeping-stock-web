# Sleeping Stock Mobile — App

Internal companion app for NMTS. Not published on the Play Store — the APK
is distributed only through the authenticated NMTS Web "Sleeping Stock
Mobile" page.

## 0. Before your first build — replace these placeholders

| # | File | Field | Replace with |
|---|---|---|---|
| 1 | `app.json` | `expo.extra.apiBaseUrl` (line ~18) | Your real backend URL, e.g. `https://nmts.yourdomain.com/api` |
| 2 | `app.json` | `expo.extra.eas.projectId` (line ~20, currently `"REPLACE_WITH_YOUR_EAS_PROJECT_ID"`) | Run `eas init` in this folder — it fills this in automatically |
| 3 | `src/config/env.js` | `fallbackApiBaseUrl` (line 10) | Optional — only used if #1 and the `EXPO_PUBLIC_API_BASE_URL` env var are both unset; keep in sync with #1 |
| 4 | Signing key | *(no file — generated via CLI)* | Run `eas credentials` → Android → "Set Up a New Keystore" (see §8) before your first `production` build |

Everything else in this project (API routes, screens, offline queue, push
notification wiring) is complete — no other placeholders, mock data, or
stub code remain.

## 1. Quick start (copy/paste order)

```bash
cd "Sleeping Stock Mobile"          # this folder
npm install                          # 1. install dependencies
eas init                             # 2. fills in extra.eas.projectId in app.json
# 3. edit app.json -> extra.apiBaseUrl to your real backend URL (placeholder #1 above)
eas credentials                      # 4. set up your Android signing keystore, once
npx expo start --dev-client          # 5. run in development (needs a dev-client build installed first, see §6)
eas build --platform android --profile preview   # 6. build an installable APK
```

## 1. What's in this build

- `App.js` — pairing, session restore, home, Notifications, Stock
  Verification (manual + camera-OCR), Stock Search, mandatory-update block.
- `src/api.js` — client for every `/api/mobile/*` endpoint, with Bearer
  session-token auth and structured error handling (`ApiError` with
  `.kind` of `network` / `timeout` / `auth` / `client` / `server`).
- `src/services/session.js` — encrypted (expo-secure-store) session
  storage: token, device ID, Mobile User ID/name, and Brand/Dealer/Branch.
- `src/services/offlineQueue.js` — SQLite-backed offline queue for Stock
  Verification, with automatic sync on reconnect and idempotent retries.
- `src/services/pushNotifications.js` — Expo push permission/token/channel
  setup, foreground/background/tap handling.

## 2. Install

```bash
npm install
```

This installs everything, including the packages added for this
integration: `expo-secure-store`, `expo-sqlite`, `expo-notifications`,
`expo-device`, `expo-crypto`, `@react-native-community/netinfo`.

## 3. Configure the API URL

Edit `app.json` -> `expo.extra.apiBaseUrl`, or set an env var at build time
(takes priority over `app.json`):

```bash
EXPO_PUBLIC_API_BASE_URL=https://your-nmts-domain.example.com/api npx expo start
```

Also set `expo.extra.eas.projectId` in `app.json` to your real EAS project
ID (required for push tokens on a standalone/EAS build — `expo init`/`eas
init` will fill this in for you if it's still the placeholder).

## 4. Android permissions

Already declared in `app.json`: `CAMERA` (pairing QR + part-number OCR
scanning) and `POST_NOTIFICATIONS` (required at runtime on Android 13+;
`expo-notifications` requests it automatically the first time
`registerForPushNotificationsAsync()` runs).

## 5. Camera OCR setup

Uses `expo-camera` (`CameraView`) + `expo-text-extractor` for on-device
text recognition — no image is ever uploaded or stored; the frame is only
held in memory during `takePictureAsync` + `extractTextFromImage`, then
discarded. This requires a **development build or EAS build** (not Expo
Go) because `expo-text-extractor` uses native modules — see `eas.json`.

## 6. Run in development

```bash
npx expo start --dev-client
```

(Requires a dev-client build installed on the device/emulator first — see
`README-CLEAN-BUILD.txt` for the existing project's baseline build steps,
which are unchanged by this integration.)

## 7. Build the APK

```bash
eas build --platform android --profile preview   # or your existing profile
```

Increase `expo.android.versionCode` in `app.json` for every build you
intend to publish, and publish matching metadata from NMTS Web -> Sleeping
Stock Mobile -> App & Settings -> Publish App Version (version name,
version code, APK path, mandatory flag) so the in-app update check
(`getLatestAppVersion()` in `src/api.js`, checked at startup in `App.js`)
can detect it.

## 8. Signing key

Use the **same** Android package name (`in.sleepingstock.mobile`, already
set) and the **same** signing key/keystore for every build so a newer APK
can update the installed app in place. The production keystore is
intentionally **not** included in this ZIP. To set one up:

```bash
eas credentials
# Android -> select the app -> Keystore -> Set Up a New Keystore
# (or upload an existing .jks if you already generated one previously)
```

Keep the generated keystore file and its password somewhere safe outside
version control — losing it means future updates can never overwrite the
currently-installed app; users would have to uninstall and reinstall.

## 9. Version update process

1. Bump `expo.version` (semantic, e.g. `1.0.1`) and `expo.android.versionCode`
   (integer, always increasing) in `app.json`.
2. `eas build --platform android --profile production`.
3. Upload/host the resulting `.apk` wherever `apk_path` will point (private
   storage, not a public bucket).
4. In NMTS Web -> Sleeping Stock Mobile -> App & Settings -> Publish App
   Version, enter the same version name/code, the APK path, release notes,
   and whether this update is mandatory.
5. Devices below `min_supported_version_code` (if mandatory) are blocked
   from normal use until they update — enforced client-side in `App.js` at
   startup, checked against the backend on every launch when reachable.

## 10. Testing this build

- Pairing: create a Mobile User + generate a code in NMTS Web, then pair
  from the app's "Enter Mobile User ID" screen (scan the QR JSON or type
  the 6-digit code manually).
- Session persistence: force-close and reopen the app — it should land on
  Home without re-pairing (session token is read from `expo-secure-store`
  at startup and re-validated).
- Offline flow: turn on Airplane Mode, submit a Stock Verification (it
  should say "Saved locally..."), confirm it shows "Pending Sync" in the
  list, turn Airplane Mode off, and confirm it flips to "Synced" within a
  few seconds without creating a duplicate row.
- Device removal: remove the device from NMTS Web while the app is open —
  the next API call should force the app back to the pairing screen with
  an explanatory alert.

## 11. Common build errors and fixes

| Symptom | Likely cause / fix |
|---|---|
| `expo-text-extractor` native module not found | You're running in Expo Go — it needs a dev-client/EAS build, not Expo Go. |
| Push token registration silently returns `null` | Running on a simulator/emulator (`Device.isDevice` is `false`) — push tokens require a real device, or `expo.extra.eas.projectId` is still the placeholder. |
| "New update available" never appears | Confirm `expo.android.versionCode` in the running build is lower than the `version_code` published in NMTS Web, and that the device has network access at app startup (the check is silently skipped if the backend is unreachable). |
| Old APK won't install over a new one / "app not installed" | Signing key mismatch — you built with a different keystore than the currently-installed APK. There's no fix except uninstalling the old app first (which loses local data) or rebuilding with the original keystore. |
| Offline queue records seem stuck as "Retry pending" | Check `last_error` in the local SQLite `verification_queue` table (or the in-app row label) — usually a stale/removed device session (401), which requires re-pairing before sync can resume. |
