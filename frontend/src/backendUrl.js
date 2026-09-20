/**
 * Resolve the backend base URL for browser clients.
 *
 * Local development: honor an explicit REACT_APP_BACKEND_URL, including
 * http://127.0.0.1:8000, and fall back to that loopback URL when unset.
 *
 * Any non-local page (GitHub Codespaces, EC2 public IP, sleepingstock.in)
 * must use the hosted HTTPS API. A production bundle that baked
 * http://127.0.0.1:8000 would otherwise POST login to the visitor's machine.
 */
const HOSTED_API_BASE = 'https://api.sleepingstock.in';
const LOCAL_API_BASE = 'http://127.0.0.1:8000';

function trimBase(url) {
  return String(url || '').trim().replace(/\/$/, '');
}

function pageHostname() {
  if (typeof window === 'undefined' || !window.location?.hostname) return '';
  return String(window.location.hostname || '').toLowerCase();
}

function isGithubHostedFrontend(host) {
  return host.endsWith('.app.github.dev') || host.endsWith('.github.dev');
}

function isLocalPageHost(host) {
  return !host || host === 'localhost' || host === '127.0.0.1';
}

function isLoopbackUrl(url) {
  return /^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/i.test(trimBase(url));
}

export function resolveBackendUrl() {
  const host = pageHostname();

  if (isGithubHostedFrontend(host)) {
    return HOSTED_API_BASE;
  }

  const fromEnv = trimBase(process.env.REACT_APP_BACKEND_URL);

  if (!isLocalPageHost(host) && (!fromEnv || isLoopbackUrl(fromEnv))) {
    return HOSTED_API_BASE;
  }

  if (fromEnv) return fromEnv;
  return LOCAL_API_BASE;
}

export function resolveApiUrl() {
  return `${resolveBackendUrl()}/api`;
}
