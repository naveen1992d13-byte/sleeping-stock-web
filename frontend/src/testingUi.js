/**
 * Testing UI is enabled only when the frontend was built with
 * REACT_APP_APP_ENV=testing. Hostname is an extra safeguard so a mistaken
 * testing bundle can never show testing chrome on production domains.
 */
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
