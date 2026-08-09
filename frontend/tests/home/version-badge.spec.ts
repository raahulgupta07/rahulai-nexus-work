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
//
// ★★★WHO IS SIGNED IN HERE, AND WHAT THAT LEAVES UNCOVERED.
// pw.smoke.config.ts loads ONE storageState, minted by scripts/mint-smoke-state.py
// for the live ADMIN account. So every assertion below is the admin view: the
// badge is a button, it opens "What's New", and the account dropdown carries a
// Changelog row. A member sees the SAME version text rendered inert —
// `data-testid="app-version-static"`, no tooltip, no click, no tab stop — has NO
// Changelog row in the account dropdown, and cannot reach the modal at all.
// NONE of that member behaviour is checked here, and it cannot be: a second
// account needs a second storage state, which this config does not have. A
// member-authenticated spec belongs in tests/visibility/ alongside
// admin-only.spec.ts, which already runs under the main config's multi-account
// chain. Read "these tests pass" as "the admin path works", never as "the split
// works" — a regression that hands every member a clickable badge, or that drops
// the version text for members entirely, would leave this whole file green.

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

test('the badge reports the version the app is actually running', async ({ page }) => {
  // ★The shape check above (/^v\d+\.\d+/) passes on ANY plausible-looking
  // string, including one frozen into the bundle at some past build. That is
  // the failure this fork has already lived through: the hot-reload overlay
  // does not cover /app/VERSION, so the UI kept advertising the last BAKED
  // release over newer code — a version number that lies about the running
  // build, which nobody notices because it still looks like a version.
  //
  // So compare against the server rather than against a literal. No version is
  // written down here: GET /api/changelog serves `current_version` from
  // settings.PROJECT_VERSION, i.e. the VERSION file inside the running image.
  // The route is deliberately reachable unauthenticated (versionCheck.client.ts
  // polls it every 60s), so this needs no extra credentials.
  const resp = await page.request.get('/api/changelog');
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  const current = body.current_version;
  // Guard the guard: if the field ever stops being served, an undefined here
  // would make the comparison below vacuous rather than red.
  expect(typeof current).toBe('string');
  expect(current).toMatch(/^\d+\.\d+/);

  await expect(badge(page)).toHaveText(`v${current}`);
});

test('version badge stays visible and clickable when the sidebar is collapsed', async ({ page }) => {
  await page.locator('button[aria-label="Collapse sidebar"]').click();
  await expect(badge(page)).toBeVisible();
  await openChangelog(page);
});
