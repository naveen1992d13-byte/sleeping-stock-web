/**
 * Testing UI is enabled only when the frontend was built with
 * REACT_APP_APP_ENV=testing. Hostname is an extra safeguard so a mistaken
 * testing bundle can never show testing chrome on production domains.
 */
export function isStandaloneDisplay() {
  if (typeof window === 'undefined') return false;
  try {
    if (window.matchMedia('(display-mode: standalone)').matches) return true;
    if (window.matchMedia('(display-mode: window-controls-overlay)').matches) return true;
  } catch {
    /* ignore */
  }
  return window.navigator.standalone === true;
}

/** Install UI is production-only. Testing builds and the testing host never show it. */
export function isProductionInstallUi() {
  if (typeof window !== 'undefined') {
    const host = String(window.location.hostname || '').toLowerCase();
    if (host === 'testing.sleepingstock.in') return false;
  }
  if (isTestingUiEnabled()) return false;
  return true;
}

export function isTestingUiEnabled() {
  if (typeof window !== 'undefined') {
    const host = String(window.location?.hostname || '').toLowerCase();
    if (
      host === 'sleepingstock.in'
      || host === 'www.sleepingstock.in'
      || host === 'api.sleepingstock.in'
    ) {
      return false;
    }
  }
  return String(process.env.REACT_APP_APP_ENV || '').trim().toLowerCase() === 'testing';
}
