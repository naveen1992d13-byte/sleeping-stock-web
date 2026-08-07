# AGENTS.md

## Cursor Cloud specific instructions

This repo is the **Sleeping Stock Mobile** app — an Expo / React Native
(SDK 54, React Native 0.81, new architecture) companion app for the NMTS web
backend (see the `sleeping-stock-web` repo). Entry point is `App.js`; API client
and services live under `src/`.

`node_modules` are installed by the Cursor Cloud update script (`npm install`;
note `.npmrc` sets `legacy-peer-deps=true`).

### What can and cannot run headless

- The JS bundle builds cleanly headless. Quick validation without a device:
  - `npx expo config --type public` (validates the project config), and
  - `npx expo export --platform android --output-dir /tmp/expo-export` (bundles
    all JS/assets; ~760 modules).
- **Full end-to-end run needs a real Android device or emulator + a dev-client /
  EAS build.** This app uses native modules (`expo-camera`, `expo-text-extractor`
  OCR, `expo-sqlite`, `expo-secure-store`, `expo-notifications`) and cannot run in
  Expo Go or as a plain web export. There is no Android emulator in the cloud VM,
  so device flows (pairing, camera OCR, offline SQLite queue, push) can't be
  exercised here — validate via config + JS bundle instead.

### Config to run against a real backend

- Set the backend URL via `app.json` -> `expo.extra.apiBaseUrl` or the
  `EXPO_PUBLIC_API_BASE_URL` env var (env var wins). See `MOBILE_APP_README.md`
  for the full build/run, EAS, signing, and pairing/testing instructions — it is
  the authoritative guide; don't duplicate it here.
