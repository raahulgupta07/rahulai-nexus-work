import { test, expect } from '../fixtures/feature-test';

/**
 * The sidebar menu: every item is present, and every item goes where its label
 * says it goes.
 *
 * ★This file used to be a `page.goto('/')` and NOTHING ELSE — a test named
 * "home menu is visible and contains expected links" that asserted neither.
 * It was green throughout the 0.0.518.1 outage, in which clicking these very
 * items changed the URL and rendered nothing at all.
 *
 * A test that navigates without asserting is worse than no test: it occupies
 * the slot where the real check would go and reports coverage that does not
 * exist. Someone reading the suite list sees "home menu" covered.
 *
 * ★What this file does NOT do is check that the destinations render — that is
 * `tests/smoke/every-route-renders.spec.ts`, which loads each route in
 * isolation. Kept separate deliberately: when the app dies at boot, a
 * click-driven sweep reports every route broken and tells you nothing about
 * which one actually is.
 */

// label -> href, exactly as the sidebar renders them.
// ★"Notifications" is deliberately absent: it opens a drawer, it is not a
// route. Asserting a link for it would fail forever on correct code.
const NAV = [
  { label: 'Automations', href: '/automations' },
  { label: 'Dashboards', href: '/dashboards' },
  { label: 'Agents', href: '/agents' },
  { label: 'Prompts', href: '/prompts' },
  { label: 'Queries', href: '/queries' },
  { label: 'Monitoring', href: '/monitoring' },
];

test('the sidebar renders every nav item, each pointing at its own route', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForLoadState('domcontentloaded');

  for (const item of NAV) {
    const link = page.locator(`a[href="${item.href}"]`).first();
    await expect(link, `no sidebar link to ${item.href}`).toBeVisible({ timeout: 20000 });
    await expect(
      link,
      `the link to ${item.href} does not carry its own label`,
    ).toContainText(item.label, { timeout: 10000 });
  }

  // "New report" is the primary action and is not one of the nav routes.
  await expect(page.getByText('New report').first()).toBeVisible({ timeout: 10000 });
});

const pageBody = (page: any): Promise<string> => page.evaluate(() => {
  const chrome = document.querySelector('aside, nav');
  const all = (document.body as HTMLElement).innerText || '';
  const side = chrome ? ((chrome as HTMLElement).innerText || '') : '';
  return all.replace(side, '').trim();
});

// Text the home page renders and no other page does. Used as a marker for
// "am I still looking at home".
const HOME_MARKER = /What can I help with\?/;

test('the home page renders its body and its agent picker', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  await expect(page.getByText(HOME_MARKER).first()).toBeVisible({ timeout: 20000 });
  await expect(
    page.locator('[data-testid="composer-agent-picker"]').first(),
    'the agent picker is missing from the home composer',
  ).toBeVisible({ timeout: 15000 });
});

test('clicking a nav item swaps the page, not just the URL', async ({ page }) => {
  // ★★★This assertion is written from a MEASUREMENT of the broken 0.0.518.1
  // build, not from a guess about it, and the guess was wrong twice.
  //
  // What actually happens when that component throws in setup():
  //   - home still renders — greeting, composer, suggestions all present
  //   - the AGENT PICKER is gone (that is the component that threw)
  //   - and Vue's state is left damaged, so the next route change updates the
  //     URL while the OLD page component stays mounted. `/monitoring` shows
  //     the home greeting, with `Cannot destructure property 'bum' of 'x'`.
  //
  // So "the page body is empty" is NOT the symptom, and both of this test's
  // earlier forms — one asserting the destination rendered, one asserting the
  // home body was non-empty — passed against the dead build with the dead
  // bundle verifiably being served. The symptom is that the page does not
  // CHANGE. Assert exactly that: home's marker text must be gone afterwards.
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  await expect(page.getByText(HOME_MARKER).first()).toBeVisible({ timeout: 20000 });

  await page.locator('a[href="/monitoring"]').first().click();
  await expect(page).toHaveURL(/\/monitoring$/, { timeout: 20000 });
  await page.waitForTimeout(3000);

  await expect(
    page.getByText(HOME_MARKER),
    'the URL changed to /monitoring but the home page is still on screen — ' +
    'the router moved and the view did not',
  ).toHaveCount(0);

  expect(
    (await pageBody(page)).length,
    'the URL changed to /monitoring but the page body stayed empty',
  ).toBeGreaterThan(40);
});
