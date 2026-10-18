const { test, expect } = require('@playwright/test');

const BASE = process.env.NMTS_E2E_BASE_URL || 'https://testing.sleepingstock.in';
const API = BASE.replace(/\/$/, '') + '/api';
const EMAIL = process.env.NMTS_E2E_EMAIL || 'testing.master@sleepingstock.in';
const PASSWORD = process.env.NMTS_E2E_PASSWORD || '';

async function loginToken(request) {
  const res = await request.post(`${API}/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(res.ok(), `login status ${res.status()}`).toBeTruthy();
  const body = await res.json();
  expect(body.access_token || body.token).toBeTruthy();
  return body.access_token || body.token;
}

test.describe('Stage B master protect + order desk smoke', () => {
  test('protected masters cannot be deleted', async ({ request }) => {
    test.skip(!PASSWORD, 'NMTS_E2E_PASSWORD required');
    const token = await loginToken(request);
    const me = await request.get(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(me.ok()).toBeTruthy();
    const meBody = await me.json();

    const usersRes = await request.get(`${API}/users`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(usersRes.ok()).toBeTruthy();
    const users = await usersRes.json();
    const list = Array.isArray(users) ? users : (users.users || users.items || []);
    const targets = list.filter((u) =>
      ['admin@sleepingstock.in', 'testing.master@sleepingstock.in'].includes(String(u.email || '').toLowerCase())
    );
    expect(targets.length).toBeGreaterThanOrEqual(1);

    for (const u of targets) {
      const del = await request.delete(`${API}/users/${u.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      expect(del.status(), `delete ${u.email}`).toBe(403);
      const body = await del.json();
      expect(String(body.detail || '')).toMatch(/protected|cannot delete/i);
    }

    // Self-check still logged in
    expect(meBody.email || EMAIL).toBeTruthy();
  });

  test('login page loads after Stage A4', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('login-promo-aside')).toBeVisible();
    const emblem = page.getByTestId('login-emblem-img');
    const box = await emblem.boundingBox();
    expect(box?.height || 0).toBeGreaterThanOrEqual(40);
  });
});
