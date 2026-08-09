import { test, expect } from '../fixtures/feature-test';

// The app version badge lives at the bottom-left of the sidebar (both
// expanded and collapsed states) and opens the changelog modal on click.
//
// ★This file covers the SIDEBAR only, and that is all it claims. Worth stating
// because in 0.0.518.1 the sidebar was the one region that still rendered while
// every page body was dead — so this suite passed, in full, on a product that
// was unusable. Nothing here is wrong; it simply cannot see a page.
// "Does the page render at all" lives in tests/smoke/every-route-renders.spec.ts.
// Inside the modal only the latest release starts expanded; older ones are
// collapsed and individually toggleable.

const badge = (page: any) => page.locator('button[name="app-version"]');
const versionRows = (page: any) => page.locator('ol > li');

async function openChangelog(page: any) {
  await badge(page).click();
  // ★Match the HEADING, not the text. A bare getByText("What's New") also
  // matches any release note that happens to mention the screen by name — one
  // did, in 0.0.526.1 — and Playwright's strict mode then fails the test on two
  // matches. The modal was fine; the locator was too wide.
  await expect(page.getByRole('heading', { name: "What's New" })).toBeVisible({ timeout: 15000 });
  // Wait for the timeline to load (fetches the full CHANGELOG.md).
  await expect(page.getByText('Latest', { exact: true })).toBeVisible({ timeout: 20000 });
}

test('version badge shows at the sidebar bottom-left and opens the changelog', async ({ page }) => {
  await expect(badge(page)).toBeVisible();
  await expect(badge(page)).toHaveText(/^v\d+\.\d+/);

  await openChangelog(page);

  const rows = versionRows(page);
  const count = await rows.count();
  expect(count).toBeGreaterThan(1);

  // Only the latest version is expanded by default.
  await expect(rows.nth(0).locator('button').first()).toHaveAttribute('aria-expanded', 'true');
  await expect(rows.nth(0).locator('ul li').first()).toBeVisible();
  await expect(rows.nth(1).locator('button').first()).toHaveAttribute('aria-expanded', 'false');
  await expect(rows.nth(1).locator('ul')).toHaveCount(0);

  // Older versions can be toggled open and closed again.
  await rows.nth(1).locator('button').first().click();
  await expect(rows.nth(1).locator('button').first()).toHaveAttribute('aria-expanded', 'true');
  await expect(rows.nth(1).locator('ul li').first()).toBeVisible();
  await rows.nth(1).locator('button').first().click();
  await expect(rows.nth(1).locator('ul')).toHaveCount(0);
});

test('version badge stays visible and clickable when the sidebar is collapsed', async ({ page }) => {
  await page.locator('button[aria-label="Collapse sidebar"]').click();
  await expect(badge(page)).toBeVisible();
  await openChangelog(page);
});
