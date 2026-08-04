// Run the boot smoke against an ALREADY-RUNNING instance.
//
// ★The main config (playwright.config.ts) chains setup -> onboarding -> features,
// which builds a whole fresh install. That is right for CI and far too heavy for
// the question this suite answers: "is the app that I am about to ship alive?"
// This config skips the chain and points at a running container instead, so the
// smoke can be part of the release checklist rather than an all-afternoon job.
//
//   # 1. mint a cookie for the live admin (password is not needed; see CLAUDE.md)
//   docker cp scripts/mint-smoke-state.py dash-app:/app/backend/
//   docker exec -w /app/backend dash-app python mint-smoke-state.py > /tmp/smoke-state.json
//
//   # 2. run it
//   cd frontend
//   PLAYWRIGHT_BASE_URL=http://localhost:8095 \
//   SMOKE_STORAGE_STATE=/tmp/smoke-state.json \
//   npx playwright test --config=pw.smoke.config.ts --reporter=line
//
// ★It must live in frontend/ — a config outside this directory cannot resolve
// @playwright/test from frontend/node_modules.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  // Defaults to the smoke suite. Override to point the same "run against a
  // live instance" harness at any other spec:
  //   SMOKE_TEST_MATCH='**/home/**/*.spec.ts' npx playwright test --config=pw.smoke.config.ts
  testMatch: process.env.SMOKE_TEST_MATCH || '**/smoke/**/*.spec.ts',
  timeout: 90 * 1000,
  retries: 0,
  workers: 3,
  use: {
    headless: true,
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8095',
    storageState: process.env.SMOKE_STORAGE_STATE || '/tmp/smoke-state.json',
  },
});
