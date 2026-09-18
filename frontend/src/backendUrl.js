/**
 * Resolve the backend base URL for browser clients.
 *
 * Local development: honor an explicit REACT_APP_BACKEND_URL, including
 * http://127.0.0.1:8000, and fall back to that loopback URL when unset.
 *
 * GitHub Codespaces / github.dev hosted frontend: always use the EC2 HTTPS
 * API. Stale localhost values in Codespace .env files must not route the SPA
 * to the Codespace's own :8000 process (Atlas/local).
 */
const HOSTED_API_BASE = 'https://api.sleepingstock.in';

function trimBase(url) {
  return String(url || '').trim().replace(/\/$/, '');
}

function isGithubHostedFrontend() {
  if (typeof window === 'undefined' || !window.location?.hostname) return false;
  const host = String(window.location.hostname || '').toLowerCase();
  return host.endsWith('.app.github.dev') || host.endsWith('.github.dev');
}

export function resolveBackendUrl() {
  if (isGithubHostedFrontend()) {
    return HOSTED_API_BASE;
  }

  const fromEnv = trimBase(process.env.REACT_APP_BACKEND_URL);
  if (fromEnv) return fromEnv;
  return 'http://127.0.0.1:8000';
}

export function resolveApiUrl() {
  return `${resolveBackendUrl()}/api`;
}
