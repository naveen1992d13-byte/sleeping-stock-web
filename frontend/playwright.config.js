const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './e2e',
  timeout: 120000,
  retries: 1,
  use: { headless: true, ignoreHTTPSErrors: true },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
