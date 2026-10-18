/**
 * Remote browser QA for Testing rollback (pre-2026-10-18 code).
 * Expects Production overlay visible (including admin@sleepingstock.in).
 * Credentials from CI env — never hard-code passwords.
 */
const { test, expect } = require('@playwright/test');

const EMAIL = process.env.NMTS_E2E_EMAIL || '';
const PASSWORD = process.env.NMTS_E2E_PASSWORD || '';
const BASE = process.env.NMTS_E2E_BASE_URL || 'https://testing.sleepingstock.in';

test.describe('Testing rollback browser', () => {
  test.beforeEach(() => {
    test.skip(!EMAIL || !PASSWORD, 'NMTS_E2E_EMAIL / NMTS_E2E_PASSWORD required');
  });

  test('login page opens; Master login succeeds; banner shows rolled-back build', async ({ page }) => {
    await page.goto(BASE + '/');
    await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 30000 });
    await page.getByTestId('login-email-input').fill(EMAIL);
    await page.getByTestId('login-password-input').fill(PASSWORD);
    await page.getByTestId('login-submit-button').click();
    await expect(page.getByTestId('testing-env-banner')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('testing-commit-sha')).toContainText('5cad631');
  });

  test('User Hub shows mirrored Production admin@ and Testing Master', async ({ page }) => {
    await page.goto(BASE + '/');
    await page.getByTestId('login-email-input').fill(EMAIL);
    await page.getByTestId('login-password-input').fill(PASSWORD);
    await page.getByTestId('login-submit-button').click();
    await expect(page.getByTestId('testing-env-banner')).toBeVisible({ timeout: 30000 });
    await page.goto(BASE + '/users');
    await page.waitForTimeout(2000);
    const body = (await page.locator('body').innerText()).toLowerCase();
    expect(body).toContain('admin@sleepingstock.in');
    expect(body).toContain('testing.master@sleepingstock.in');
  });

  test('Analytics opens without 5xx / crash', async ({ page }) => {
    const errors = [];
    page.on('response', (res) => {
      if (res.status() >= 500 && res.url().includes('testing.sleepingstock.in')) {
        errors.push(res.status() + ' ' + res.url());
      }
    });
    await page.goto(BASE + '/');
    await page.getByTestId('login-email-input').fill(EMAIL);
    await page.getByTestId('login-password-input').fill(PASSWORD);
    await page.getByTestId('login-submit-button').click();
    await expect(page.getByTestId('testing-env-banner')).toBeVisible({ timeout: 30000 });
    await page.goto(BASE + '/analytics');
    await expect(page.locator('body')).toContainText(/Analytics|Stock|Aging|Order|Request|Dashboard/i, {
      timeout: 30000,
    });
    expect(errors, 'no 5xx from testing host').toEqual([]);
  });
});
