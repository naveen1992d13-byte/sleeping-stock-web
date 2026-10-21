/** 30-minute inactivity session timeout (milliseconds). */
export const NMTS_SESSION_TIMEOUT_MS = 30 * 60 * 1000;

export const NMTS_LAST_ACTIVITY_KEY = 'nmtsLastActivity';

/** Date of "Not now" on the install prompt. This is the only auth-adjacent value kept in localStorage. */
export const NMTS_INSTALL_NOT_NOW_KEY = 'nmtsInstallNotNow';

const SESSION_KEYS = [
  'token',
  'user',
  'permissions',
  'openTabs',
  'activeTab',
  'access_token',
  'accessToken',
  'jwt',
  NMTS_LAST_ACTIVITY_KEY,
];

function browserSession() {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function browserLocal() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Drop tokens left in localStorage by older builds so those browsers log out once. */
export function purgeLegacyLocalAuth() {
  const local = browserLocal();
  if (!local) return;
  SESSION_KEYS.forEach((key) => {
    try {
      local.removeItem(key);
    } catch {
      /* ignore */
    }
  });
}

purgeLegacyLocalAuth();

export function readAuthItem(key) {
  const store = browserSession();
  if (!store) return null;
  try {
    return store.getItem(key);
  } catch {
    return null;
  }
}

export function writeAuthItem(key, value) {
  const store = browserSession();
  if (!store) return;
  store.setItem(key, value);
}

export function removeAuthItem(key) {
  const store = browserSession();
  if (!store) return;
  try {
    store.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function getAuthToken() {
  return readAuthItem('token')
    || readAuthItem('access_token')
    || readAuthItem('accessToken')
    || readAuthItem('jwt')
    || '';
}

export function touchSessionActivity() {
  writeAuthItem(NMTS_LAST_ACTIVITY_KEY, String(Date.now()));
}

export function clearSessionActivity() {
  removeAuthItem(NMTS_LAST_ACTIVITY_KEY);
}

export function isSessionExpiredByInactivity() {
  try {
    const token = getAuthToken();
    if (!token) return false;
    const raw = readAuthItem(NMTS_LAST_ACTIVITY_KEY);
    if (!raw) return false;
    const last = Number(raw);
    if (!Number.isFinite(last)) return false;
    return Date.now() - last > NMTS_SESSION_TIMEOUT_MS;
  } catch {
    return false;
  }
}

export function clearAuthStorage() {
  SESSION_KEYS.forEach((key) => removeAuthItem(key));
  purgeLegacyLocalAuth();
}

export function snoozeInstallPrompt() {
  const local = browserLocal();
  if (!local) return;
  local.setItem(NMTS_INSTALL_NOT_NOW_KEY, new Date().toISOString().slice(0, 10));
}

export function isInstallPromptSnoozed() {
  const local = browserLocal();
  if (!local) return false;
  const raw = local.getItem(NMTS_INSTALL_NOT_NOW_KEY);
  if (!raw) return false;
  const dismissed = new Date(`${raw}T00:00:00`);
  if (Number.isNaN(dismissed.getTime())) return false;
  return Date.now() - dismissed.getTime() < 7 * 24 * 60 * 60 * 1000;
}
