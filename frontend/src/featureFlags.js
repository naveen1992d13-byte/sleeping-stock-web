/**
 * Backend-resolved feature flags (GET /api/features). Fail closed: any error,
 * non-200 response, malformed body or a response slower than the timeout
 * yields every flag OFF.
 */
export const FEATURE_FLAGS_TIMEOUT_MS = 3000;

export const DEFAULT_FEATURE_FLAGS = Object.freeze({
  sequential_auto_sourcing: false,
});

export async function fetchFeatureFlags(apiBase, {
  fetchImpl = (typeof fetch !== 'undefined' ? fetch : null),
  timeoutMs = FEATURE_FLAGS_TIMEOUT_MS,
} = {}) {
  if (!fetchImpl) return { ...DEFAULT_FEATURE_FLAGS };
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  let timer = null;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      if (controller) controller.abort();
      resolve(null);
    }, timeoutMs);
  });
  try {
    const request = (async () => {
      const res = await fetchImpl(`${apiBase}/features`, {
        cache: 'no-store',
        ...(controller ? { signal: controller.signal } : {}),
      });
      if (!res || res.status !== 200) return null;
      return res.json();
    })();
    const body = await Promise.race([request, timeout]);
    if (!body || typeof body !== 'object') return { ...DEFAULT_FEATURE_FLAGS };
    return {
      ...DEFAULT_FEATURE_FLAGS,
      sequential_auto_sourcing: body.sequential_auto_sourcing === true,
    };
  } catch {
    return { ...DEFAULT_FEATURE_FLAGS };
  } finally {
    if (timer) clearTimeout(timer);
  }
}
