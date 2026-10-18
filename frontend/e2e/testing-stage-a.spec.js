const { test, expect } = require('@playwright/test');

const BASE = process.env.NMTS_E2E_BASE_URL || 'https://testing.sleepingstock.in';
const EMAIL = process.env.NMTS_E2E_EMAIL || '';
const PASSWORD = process.env.NMTS_E2E_PASSWORD || '';

test.describe('Stage A UI', () => {
  test.use({ viewport: { width: 1600, height: 900 } });

  test('login split card sized with vw/vh; two-panel from first paint', async ({ page }) => {
    await page.goto(BASE + '/');
    await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 30000 });
    const panel = page.locator('.nmts-login-panel--split');
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box).toBeTruthy();
    // ~52vw width and within ~62vh height on 1600x900 (allow tolerance)
    expect(box.width).toBeGreaterThan(700);
    expect(box.width).toBeLessThanOrEqual(860);
    expect(box.height).toBeGreaterThan(300);
    expect(box.height).toBeLessThanOrEqual(580);
    await expect(page.locator('.nmts-login-promo')).toBeVisible();
    await expect(page.getByTestId('login-email-input')).toBeVisible();
  });

  test('menu dark theme + Sleeping Stock NMTS header', async ({ page }) => {
    test.skip(!EMAIL || !PASSWORD, 'credentials required');
    await page.goto(BASE + '/');
    await page.getByTestId('login-email-input').fill(EMAIL);
    await page.getByTestId('login-password-input').fill(PASSWORD);
    await page.getByTestId('login-submit-button').click();
    await expect(page.getByTestId('dashboard-layout')).toBeVisible({ timeout: 45000 });
    await page.getByTestId('sidebar-menu-toggle').click();
    const brand = page.getByTestId('app-menu-brand-header');
    await expect(brand).toBeVisible();
    await expect(brand.locator('.nmts-word-sleeping')).toHaveText('Sleeping');
    await expect(brand.locator('.nmts-word-stock')).toHaveText('Stock');
    await expect(brand.locator('.nmts-app-menu-brand-nmts')).toHaveText('NMTS');
    const menu = page.locator('.nmts-app-menu-dropdown');
    await expect(menu).toBeVisible();
    const bg = await menu.evaluate((el) => getComputedStyle(el).backgroundColor + getComputedStyle(el).backgroundImage);
    expect(bg.toLowerCase()).not.toContain('rgb(255, 255, 255)');
  });
});
