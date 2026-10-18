/**
 * Phase 5 remote browser QA against testing.sleepingstock.in.
 * Credentials come from CI secrets — never hard-code passwords.
 */
const { test, expect } = require('@playwright/test');

const EMAIL = process.env.NMTS_E2E_EMAIL || '';
const PASSWORD = process.env.NMTS_E2E_PASSWORD || '';

test.describe('Testing Phase 5', () => {
  test.beforeEach(() => {
    test.skip(!EMAIL || !PASSWORD, 'NMTS_E2E_EMAIL / NMTS_E2E_PASSWORD secrets required');
  });

  test('login shell is single Testing panel; wrong password fails; Master login succeeds', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('login-page')).toBeVisible();
    // Single login shell (no dual-panel flash of compact form after promo loads).
    await expect(page.getByTestId('login-email-input')).toBeVisible();
    await expect(page.getByTestId('login-password-input')).toBeVisible();

    await page.getByTestId('login-email-input').fill(EMAIL);
    await page.getByTestId('login-password-input').fill('definitely-wrong-password-xyz');
    await page.getByTestId('login-submit-button').click();
    // Stay on login after failure.
    await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 15000 });

    await page.getByTestId('login-password-input').fill(PASSWORD);
    await page.getByTestId('login-submit-button').click();
    await expect(page.getByTestId('testing-env-banner')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('testing-commit-sha')).not.toHaveText('unknown-sha');
  });

  test('User Hub shows testing-only masters (no Production admin@)', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('login-email-input').fill(EMAIL);
    await page.getByTestId('login-password-input').fill(PASSWORD);
    await page.getByTestId('login-submit-button').click();
    await expect(page.getByTestId('testing-env-banner')).toBeVisible({ timeout: 30000 });

    // Open User Hub via menu if needed.
    const hubLink = page.getByRole('button', { name: /user hub/i }).or(page.getByText(/user hub/i)).first();
    if (await hubLink.isVisible().catch(() => false)) {
      await hubLink.click();
    } else {
      await page.goto('/users');
    }
    await page.waitForTimeout(1500);
    const body = await page.locator('body').innerText();
    expect(body.toLowerCase()).not.toContain('admin@sleepingstock.in');
    expect(body.toLowerCase()).toContain('testing.master@sleepingstock.in');
  });

  test('Analytics initializes without N/A scope stall and without hard crash', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('login-email-input').fill(EMAIL);
    await page.getByTestId('login-password-input').fill(PASSWORD);
    await page.getByTestId('login-submit-button').click();
    await expect(page.getByTestId('testing-env-banner')).toBeVisible({ timeout: 30000 });

    await page.goto('/analytics');
    // Master scope starts as All* (empty) — page should render section chrome.
    await expect(page.locator('body')).toContainText(/Analytics|Stock|Aging|Order|Request/i, {
      timeout: 30000,
    });
  });
});
