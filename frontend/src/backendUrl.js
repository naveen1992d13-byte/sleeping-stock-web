/**
 * Resolve the backend base URL for browser clients.
 *
 * An explicit non-loopback REACT_APP_BACKEND_URL always wins — including on
 * GitHub Codespaces — so the SPA can target a remote EC2/HTTPS API instead of
 * the Codespace's own :8000 process (Atlas/local).
 *
 * Codespace host derivation is used only when env is empty or loopback, so a
 * stale localhost REACT_APP_BACKEND_URL cannot break in-Codespace routing.
 */
function trimBase(url) {
  return String(url || '').trim().replace(/\/$/, '');
}

function isLoopbackBackend(url) {
  const text = trimBase(url);
  if (!text) return true;
  try {
    const host = new URL(text).hostname.toLowerCase();
    return host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]';
  } catch {
    return /localhost|127\.0\.0\.1/i.test(text);
  }
}

export function resolveBackendUrl() {
  const fromEnv = trimBase(process.env.REACT_APP_BACKEND_URL);

  if (fromEnv && !isLoopbackBackend(fromEnv)) {
    return fromEnv;
  }

  if (typeof window !== 'undefined' && window.location?.hostname) {
    const host = String(window.location.hostname || '');
    const codespace = host.match(/^(.+)-(\d+)\.app\.github\.dev$/i);
    if (codespace) {
      const base = codespace[1];
      const frontPort = codespace[2];
      if (frontPort === '3000') {
        return `https://${base}-8000.app.github.dev`;
      }
    }
  }

  if (fromEnv) return fromEnv;
  return 'http://127.0.0.1:8000';
}

export function resolveApiUrl() {
  return `${resolveBackendUrl()}/api`;
}
