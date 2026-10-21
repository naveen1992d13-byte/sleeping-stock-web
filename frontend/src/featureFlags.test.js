import { fetchFeatureFlags, DEFAULT_FEATURE_FLAGS } from './featureFlags';

const ok = (body) => () => Promise.resolve({ status: 200, json: () => Promise.resolve(body) });

describe('fetchFeatureFlags (fail closed)', () => {
  test('default is OFF', () => {
    expect(DEFAULT_FEATURE_FLAGS.sequential_auto_sourcing).toBe(false);
  });

  test('ON only when the backend says exactly true', async () => {
    await expect(fetchFeatureFlags('/api', { fetchImpl: ok({ sequential_auto_sourcing: true }) }))
      .resolves.toEqual({ sequential_auto_sourcing: true });
    await expect(fetchFeatureFlags('/api', { fetchImpl: ok({ sequential_auto_sourcing: 'true' }) }))
      .resolves.toEqual({ sequential_auto_sourcing: false });
    await expect(fetchFeatureFlags('/api', { fetchImpl: ok({ sequential_auto_sourcing: false }) }))
      .resolves.toEqual({ sequential_auto_sourcing: false });
  });

  test('network error => OFF', async () => {
    const fetchImpl = () => Promise.reject(new Error('down'));
    await expect(fetchFeatureFlags('/api', { fetchImpl })).resolves.toEqual({ sequential_auto_sourcing: false });
  });

  test('non-200 => OFF', async () => {
    const fetchImpl = () => Promise.resolve({ status: 404, json: () => Promise.resolve({ sequential_auto_sourcing: true }) });
    await expect(fetchFeatureFlags('/api', { fetchImpl })).resolves.toEqual({ sequential_auto_sourcing: false });
  });

  test('malformed body => OFF', async () => {
    const fetchImpl = () => Promise.resolve({ status: 200, json: () => Promise.reject(new Error('bad json')) });
    await expect(fetchFeatureFlags('/api', { fetchImpl })).resolves.toEqual({ sequential_auto_sourcing: false });
  });

  test('slower than the timeout => OFF', async () => {
    const fetchImpl = () => new Promise((resolve) => setTimeout(
      () => resolve({ status: 200, json: () => Promise.resolve({ sequential_auto_sourcing: true }) }), 200,
    ));
    await expect(fetchFeatureFlags('/api', { fetchImpl, timeoutMs: 20 }))
      .resolves.toEqual({ sequential_auto_sourcing: false });
  });

  test('requests GET <api>/features', async () => {
    const calls = [];
    const fetchImpl = (url) => { calls.push(url); return ok({ sequential_auto_sourcing: true })(); };
    await fetchFeatureFlags('https://x/api', { fetchImpl });
    expect(calls).toEqual(['https://x/api/features']);
  });
});
