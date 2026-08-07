/** 30-minute inactivity session timeout (milliseconds). */
export const NMTS_SESSION_TIMEOUT_MS = 30 * 60 * 1000;

export const NMTS_LAST_ACTIVITY_KEY = 'nmtsLastActivity';

export function touchSessionActivity() {
  try {
    localStorage.setItem(NMTS_LAST_ACTIVITY_KEY, String(Date.now()));
  } catch {
    /* ignore */
  }
}

export function clearSessionActivity() {
  try {
    localStorage.removeItem(NMTS_LAST_ACTIVITY_KEY);
  } catch {
    /* ignore */
  }
}

export function isSessionExpiredByInactivity() {
  try {
    const token = localStorage.getItem('token');
    if (!token) return false;
    const raw = localStorage.getItem(NMTS_LAST_ACTIVITY_KEY);
    if (!raw) return false;
    const last = Number(raw);
    if (!Number.isFinite(last)) return false;
    return Date.now() - last > NMTS_SESSION_TIMEOUT_MS;
  } catch {
    return false;
  }
}

export function clearAuthStorage() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  localStorage.removeItem('permissions');
  localStorage.removeItem('openTabs');
  localStorage.removeItem('activeTab');
  clearSessionActivity();
}
