// Standalone config: NO globalSetup. The suite's global.setup.ts signs up a
// brand-new admin, which against a live install writes a real user and org
// into the real database. This config reuses an existing session instead and
// only reads.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '**/dashboards/**/*.spec.ts',
  timeout: 90 * 1000,
  retries: 0,
  workers: 1,
  use: {
    headless: true,
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8095',
    storageState: process.env.PW_STATE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
