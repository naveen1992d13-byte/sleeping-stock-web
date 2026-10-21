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

async function assertLayout(page, vp) {
  await page.setViewportSize({ width: vp.width, height: vp.height });
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 30000 });

  const panel = page.getByTestId('login-zoom-wrap');
  const formCol = page.getByTestId('login-form-column');
  const promo = page.getByTestId('login-promo-aside');
  const btn = page.getByTestId('login-submit-button');
  const email = page.getByTestId('login-email-input');

  const panelBox = await panel.boundingBox();
  expect(panelBox).toBeTruthy();
  const vwRatio = panelBox.width / vp.width;
  expect(vwRatio).toBeGreaterThanOrEqual(0.48);
  expect(vwRatio).toBeLessThanOrEqual(0.56);
  expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(vp.height + 1);
  expect(panelBox.x + panelBox.width).toBeLessThanOrEqual(vp.width + 1);

  const btnBox = await btn.boundingBox();
  const formBox = await formCol.boundingBox();
  expect(btnBox.y + btnBox.height).toBeLessThanOrEqual(formBox.y + formBox.height + 2);

  await email.fill(EMAIL);
  const trunc = await email.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth, v: el.value }));
  expect(trunc.v).toBe(EMAIL);
  expect(trunc.sw).toBeLessThanOrEqual(trunc.cw + 1);

  const scrollY = await page.evaluate(() => {
    const de = document.documentElement;
    const body = document.body;
    return Math.max(de.scrollHeight, body.scrollHeight) > Math.min(de.clientHeight, window.innerHeight) + 2;
  });
  expect(scrollY).toBeFalsy();

  const promoBox = await promo.boundingBox();
  const ratioPromo = promoBox.width / panelBox.width;
  const ratioForm = formBox.width / panelBox.width;
  expect(ratioPromo).toBeGreaterThan(0.56);
  expect(ratioPromo).toBeLessThan(0.64);
  expect(ratioForm).toBeGreaterThan(0.36);
  expect(ratioForm).toBeLessThan(0.44);

  // Empty space under form content inside form column
  const footer = page.locator('.nmts-login-footer');
  const footerBox = await footer.boundingBox();
  const emptyUnder = formBox.y + formBox.height - (footerBox.y + footerBox.height);
  expect(emptyUnder).toBeLessThanOrEqual(40);

  fs.mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, `stage-a3-${vp.name}.png`), fullPage: true });
}

test.describe('Stage A3 login card', () => {
  for (const vp of VIEWPORTS) {
    test(`viewport ${vp.name}`, async ({ page }) => {
      await assertLayout(page, vp);
    });
  }
});
