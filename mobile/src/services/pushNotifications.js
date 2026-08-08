// Push notification helper for Sleeping Stock Mobile (Part 13 / Part 22).
//
// Covers: permission request, Expo push token retrieval + registration with
// the backend, Android notification channel setup, foreground display
// behaviour, background/killed-app delivery (handled by the OS + Expo, we
// just need the channel + token registered), and tap-to-navigate.
//
// This module deliberately does NOT talk to Firebase/APNs directly — Expo's
// push service is the transport. `app.json` -> expo.notification /
// google-services config and EAS push credentials are what wire it to FCM
// for a standalone (non-Expo-Go) build; see docs/MOBILE_README.md.
import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import Constants from 'expo-constants';
import { registerPushToken } from '../api';

const ANDROID_CHANNEL_ID = 'sleeping-stock-requests';

let responseListenerSub = null;
let receivedListenerSub = null;

/**
 * Controls how a notification is shown while the app is in the foreground.
 * Must be called once, at module load / app startup, before any
 * notification could arrive.
 */
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

async function ensureAndroidChannel() {
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync(ANDROID_CHANNEL_ID, {
    name: 'Branch Stock Requests',
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: '#176b43',
    sound: 'default',
    lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
  });
}

/**
 * Requests notification permission (safe to call repeatedly — it's a no-op
 * once granted) and returns an Expo push token, or null if permission was
 * denied or this is a simulator/emulator without push capability.
 */
export async function registerForPushNotificationsAsync() {
  try {
    await ensureAndroidChannel();

    if (!Device.isDevice) {
      console.log('[push] Skipping push registration — running on a simulator/emulator.');
      return null;
    }

    const existing = await Notifications.getPermissionsAsync();
    let finalStatus = existing.status;
    if (finalStatus !== 'granted') {
      const requested = await Notifications.requestPermissionsAsync();
      finalStatus = requested.status;
    }

    if (finalStatus !== 'granted') {
      console.log('[push] Notification permission was not granted.');
      return null;
    }

    const projectId =
      Constants.expoConfig?.extra?.eas?.projectId || Constants.easConfig?.projectId;
    const tokenResponse = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined
    );
    return tokenResponse.data;
  } catch (error) {
    console.log('[push] Failed to register for push notifications', error);
    return null;
  }
}

/** Registers the given Expo push token with the backend for this device. */
export async function syncPushTokenWithBackend(token) {
  if (!token) return false;
  try {
    await registerPushToken(token);
    return true;
  } catch (error) {
    console.log('[push] Failed to register push token with backend', error);
    return false;
  }
}

/**
 * Full startup routine: request permission, get token, push it to the
 * backend, and wire foreground/tap listeners.
 *
 * @param {Object} handlers
 * @param {(data: any) => void} [handlers.onNotificationReceived] - fired
 *   while the app is in the foreground and a notification arrives.
 * @param {(data: any) => void} [handlers.onNotificationTapped] - fired when
 *   the user taps a notification (app was backgrounded or killed). Use this
 *   to navigate straight to the relevant request/notification screen.
 * @returns {() => void} teardown function — call on unmount.
 */
export async function initPushNotifications({ onNotificationReceived, onNotificationTapped } = {}) {
  const token = await registerForPushNotificationsAsync();
  if (token) {
    await syncPushTokenWithBackend(token);
  }

  // Foreground: app is open and visible.
  receivedListenerSub = Notifications.addNotificationReceivedListener((notification) => {
    try {
      onNotificationReceived?.(notification.request.content.data);
    } catch (error) {
      console.log('[push] onNotificationReceived handler error', error);
    }
  });

  // User tapped the notification — covers foreground, backgrounded, and
  // cold-start-from-killed (Expo replays the last response on launch).
  responseListenerSub = Notifications.addNotificationResponseReceivedListener((response) => {
    try {
      onNotificationTapped?.(response.notification.request.content.data);
    } catch (error) {
      console.log('[push] onNotificationTapped handler error', error);
    }
  });

  // If the app was launched by tapping a notification (cold start), handle
  // it once here too.
  Notifications.getLastNotificationResponseAsync()
    .then((response) => {
      if (response) {
        onNotificationTapped?.(response.notification.request.content.data);
      }
    })
    .catch((error) => console.log('[push] getLastNotificationResponseAsync failed', error));

  return function teardownPushNotifications() {
    receivedListenerSub?.remove();
    responseListenerSub?.remove();
    receivedListenerSub = null;
    responseListenerSub = null;
  };
}
