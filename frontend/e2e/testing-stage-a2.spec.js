const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const BASE = process.env.NMTS_E2E_BASE_URL || 'https://testing.sleepingstock.in';
const OUT = path.join(__dirname, '..', 'test-results');
const EMAIL = 'testing.master@sleepingstock.in';

const VIEWPORTS = [
  { name: '1366x768', width: 1366, height: 768 },
  { name: '1600x900', width: 1600, height: 900 },
  { name: '1920x1080', width: 1920, height: 1080 },
];

async function assertLoginLayout(page, vp) {
  await page.setViewportSize({ width: vp.width, height: vp.height, deviceScaleFactor: 1 });
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 30000 });
  const panel = page.locator('.nmts-login-panel--split');
  await expect(panel).toBeVisible();
  const card = page.locator('.nmts-login-card');
  const btn = page.getByTestId('login-submit-button');
  const email = page.getByTestId('login-email-input');

  const panelBox = await panel.boundingBox();
  expect(panelBox).toBeTruthy();
  expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(vp.height + 1);
  expect(panelBox.x + panelBox.width).toBeLessThanOrEqual(vp.width + 1);

  const cardBox = await card.boundingBox();
  const btnBox = await btn.boundingBox();
  expect(btnBox.y + btnBox.height).toBeLessThanOrEqual(cardBox.y + cardBox.height + 2);

  await email.fill(EMAIL);
  const trunc = await email.evaluate((el) => ({
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
    value: el.value,
  }));
  expect(trunc.value).toBe(EMAIL);
  expect(trunc.scrollWidth).toBeLessThanOrEqual(trunc.clientWidth + 1);

  const scroll = await page.evaluate(() => ({
    x: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    bodyOverflow: getComputedStyle(document.body).overflowX,
  }));
  expect(scroll.x).toBeFalsy();

  // Promo: no dark side strips — media should cover aside (object-fit cover)
  const promo = page.getByTestId('login-promo-aside');
  await expect(promo).toBeVisible();
  const cover = await page.evaluate(() => {
    const media = document.querySelector('.nmts-login-promo-media img, .nmts-login-promo-media video');
    if (!media) return { hasMedia: false };
    const cs = getComputedStyle(media);
    return { hasMedia: true, fit: cs.objectFit, bg: cs.backgroundColor };
  });
  if (cover.hasMedia) {
    expect(cover.fit).toBe('cover');
  }

  // Card size stable across a short wait (promo swap should not shift layout shell)
  const before = await panel.boundingBox();
  await page.waitForTimeout(1200);
  const after = await panel.boundingBox();
  expect(Math.abs(after.width - before.width)).toBeLessThanOrEqual(2);
  expect(Math.abs(after.height - before.height)).toBeLessThanOrEqual(8);

  fs.mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, `stage-a2-${vp.name}.png`), fullPage: true });
}

test.describe('Stage A2 login layout', () => {
  for (const vp of VIEWPORTS) {
    test(`viewport ${vp.name}`, async ({ page }) => {
      await assertLoginLayout(page, vp);
    });
  }
});
