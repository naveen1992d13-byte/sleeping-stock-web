Sleeping Stock Mobile – Final Pairing Update

Implemented:
1. Transparent Sleeping Stock logo used on onboarding.
2. Pairing requires Mobile User ID + Device User Name + Device User Mobile Number.
3. QR scan and manual pairing code both pass exact device-user identity.
4. Manual code supports the formatted one-time code shown by NMTS Web.
5. Request response import issue fixed (submitPartResponse).
6. Session stores exact device-user name/mobile returned by backend.

Required before APK build:
1. app.json -> extra.apiBaseUrl: replace with the real HTTPS backend /api URL.
2. app.json -> extra.eas.projectId: replace with the real Expo EAS project ID.
3. Run npm install.
4. Run eas build --platform android --profile preview to create an APK.

Package name remains: in.sleepingstock.mobile
