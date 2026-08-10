/**
 * Resolve the backend base URL for browser clients.
 * On GitHub Codespaces, always derive from the current *-3000.app.github.dev host
 * so a stale/local REACT_APP_BACKEND_URL cannot break the app.
 */
export function resolveBackendUrl() {
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

  const fromEnv = String(process.env.REACT_APP_BACKEND_URL || '').trim().replace(/\/$/, '');
  if (fromEnv) return fromEnv;
  return 'http://127.0.0.1:8000';
}

export function resolveApiUrl() {
  return `${resolveBackendUrl()}/api`;
}
