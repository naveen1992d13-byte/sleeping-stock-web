/**
 * Alarm-style incoming request alert helpers.
 *
 * This repo does not ship a native full-screen-intent module
 * (modules/native-request-alert is not present). The safe baseline is:
 *   - Android HIGH / MAX heads-up channel
 *   - lock-screen public visibility
 *   - in-app popup (NotificationsScreen + RequestScreen)
 *   - Snooze and Start Picking notification actions
 * Full-screen intent is not forced: Expo's notification API does not
 * reliably expose FSI across the OS versions this app supports.
 */
import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';

export const REQUEST_ALERT_CATEGORY = 'nmts-request-alert';
export const ACTION_SNOOZE = 'SNOOZE';
export const ACTION_START_PICKING = 'START_PICKING';
export const ANDROID_CHANNEL_ID = 'sleeping-stock-requests';

export async function ensureRequestAlertCategory() {
  await Notifications.setNotificationCategoryAsync(REQUEST_ALERT_CATEGORY, [
    {
      identifier: ACTION_SNOOZE,
      buttonTitle: 'Snooze',
      options: { opensAppToForeground: true },
    },
    {
      identifier: ACTION_START_PICKING,
      buttonTitle: 'Start Picking',
      options: { isDestructive: false, opensAppToForeground: true },
    },
  ]);
}

export async function ensureRequestAlertChannel() {
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync(ANDROID_CHANNEL_ID, {
    name: 'Branch Stock Requests',
    importance: Notifications.AndroidImportance.MAX,
    vibrationPattern: [0, 400, 200, 400, 200, 400],
    lightColor: '#176b43',
    sound: 'default',
    lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
    bypassDnd: false,
    enableVibrate: true,
    showBadge: true,
  });
}

export function requestAlertContent(title, body, data = {}) {
  return {
    title,
    body,
    data,
    sound: 'default',
    categoryIdentifier: REQUEST_ALERT_CATEGORY,
    interruptionLevel: 'timeSensitive',
  };
}
