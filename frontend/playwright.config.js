const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './e2e',
  timeout: 120000,
  retries: 0,
  use: {
    headless: true,
    ignoreHTTPSErrors: true,
    deviceScaleFactor: 1,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium', deviceScaleFactor: 1 } }],
});
