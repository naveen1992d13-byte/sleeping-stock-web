SLEEPING STOCK MOBILE — CLEAN OFFLINE OCR BUILD

This version uses expo-text-extractor 2.0.0:
- Android: Google ML Kit on-device OCR
- Supported target: English letters, numbers, hyphen and slash
- No OCR API key or OCR backend
- Internet is needed only to install packages and build/download the APK

DO NOT OPEN THIS PROJECT IN SNACK OR EXPO GO.

CLEAN GITHUB CODESPACE COMMANDS

npm install
npx expo export --platform android
eas build --platform android --profile preview --clear-cache

If the export succeeds, start the EAS build.
Install the resulting APK on Android.

App flow:
Stock Verification > Scan > place one printed part number inside the box >
tap capture > detected value auto-fills Part Number.

Examples:
86510-A0010
92101B4000
ABC-1234-X
97133/C5000
