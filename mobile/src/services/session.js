import * as SecureStore from 'expo-secure-store';

const SESSION_KEY = 'sleeping_stock_mobile_session_v1';

export async function saveSession(session) {
  try {
    await SecureStore.setItemAsync(SESSION_KEY, JSON.stringify(session));
    return true;
  } catch (error) {
    console.log('[session] Failed to save session', error);
    return false;
  }
}

export async function getSession() {
  try {
    const raw = await SecureStore.getItemAsync(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    console.log('[session] Failed to read session', error);
    return null;
  }
}

export async function getSessionToken() {
  const session = await getSession();
  return session?.sessionToken || null;
}

export async function clearSession() {
  try {
    await SecureStore.deleteItemAsync(SESSION_KEY);
    return true;
  } catch (error) {
    console.log('[session] Failed to clear session', error);
    return false;
  }
}


function localDateKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}${month}${day}`;
}

/**
 * One verification session per physical mobile/device per local calendar day.
 * Every upload batch on the same device and day receives this same ID.
 */
export async function getDailyVerificationSessionId(sessionOverride = null) {
  const session = sessionOverride || await getSession();
  const devicePart = String(session?.deviceId || session?.mobileUserId || 'DEVICE')
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, '')
    .slice(-12) || 'DEVICE';
  return `SV${localDateKey()}${devicePart}`;
}
