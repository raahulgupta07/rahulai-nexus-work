import { test, expect } from '../fixtures/feature-test';

/**
 * Every top-level route boots, throws nothing, and renders something of its own.
 *
 * ★This suite did not exist when 0.0.518.1 shipped a dead app.
 *
 * ★What that failure actually looked like — measured against the dead bundle,
 * not assumed, because the obvious assumption was wrong twice:
 *
 *   - a single `ReferenceError` in one component's `setup()`
 *   - the page it lives on STILL RENDERS. Greeting, composer, suggestions.
 *     Only the thrown component itself is missing.
 *   - but Vue's state is left damaged, so the next route change updates the URL
 *     while the OLD page component stays mounted — `/monitoring` showing the
 *     home screen, with `Cannot destructure property 'bum' of 'x' as it is null`
 *   - a FULL page load of that same route is fine, because nothing has crashed
 *     yet on a fresh boot
 *
 * So the user sees: one feature silently absent, and every nav item apparently
 * doing nothing. Which reads as "the whole product is broken".
 *
 * 5,451 Python tests passed on that build. They cannot see the frontend at all:
 * the fork's "frontend guards" read `.vue` files as TEXT, and grep cannot
 * evaluate declaration order.
 *
 * ★And this browser suite would have passed too. `home/home-menu.spec.ts`
 * navigated to `/` and asserted NOTHING — an empty test body.
 * `home/version-badge.spec.ts` only touched the sidebar, the one region that
 * survives the crash. Both green, app dead. A test that navigates without
 * asserting is worse than no test: it reports coverage it does not have.
 *
 * So this file holds two things down:
 *
 *   1. ★no uncaught page error and no console error on any route — this is the
 *      assertion that catches the 0.0.518.1 class, because a throw during setup
 *      does NOT necessarily empty the page
 *   2. the route rendered main content — body text beyond the sidebar shell
 *
 * (2) is the weaker of the two and is here as a backstop for the different
 * failure where a page renders nothing at all. Do not rely on it to catch a
 * thrown component: on the 0.0.518.1 bundle the home body was still full, and
 * a length check on it passed. (1) is what failed.
 *
 * ★Each route gets its own full page load, never a client-side click — one
 * broken page must not mask the others. The flip side is that this suite cannot
 * see the damaged-navigation symptom at all, since a fresh boot has nothing
 * damaged yet. That half is covered in tests/home/home-menu.spec.ts, which
 * clicks and then asserts the page actually CHANGED.
 */

// Routes reachable from the sidebar, plus the two that broke.
// ★Do not add a route here that needs seeded data — this suite must fail only
// when the app is broken, never when the fixture is thin.
const ROUTES: Array<{ path: string; name: string }> = [
  { path: '/', name: 'home' },
  { path: '/reports', name: 'reports' },
  { path: '/dashboards', name: 'dashboards' },
  { path: '/agents', name: 'agents' },
  { path: '/queries', name: 'queries' },
  { path: '/prompts', name: 'prompts' },
  { path: '/automations', name: 'automations' },
  { path: '/monitoring', name: 'monitoring' },
  // ★The second casualty of 0.0.518.1, and the page the bug report opened on.
  { path: '/app-analytics', name: 'app analytics' },
  { path: '/instructions', name: 'instructions' },
  { path: '/settings', name: 'settings' },
  { path: '/projects', name: 'projects' },
];

// Console noise that is not a broken page. Keep this list SHORT and specific —
// every entry is a class of failure this suite stops seeing.
const IGNORED = [
  /Failed to load resource/i,   // a missing thumbnail/asset, not a dead page
  /favicon/i,
  /\[intercom\]/i,
];

const isRealError = (text: string) => !IGNORED.some((re) => re.test(text));

for (const route of ROUTES) {
  test(`${route.name} (${route.path}) boots clean and renders its own content`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(`uncaught: ${String(e).split('\n')[0]}`));
    page.on('console', (m) => {
      if (m.type() === 'error' && isRealError(m.text())) {
        errors.push(`console: ${m.text().split('\n')[0]}`);
      }
    });

    await page.goto(route.path, { waitUntil: 'domcontentloaded' });
    // The shell paints fast; the page body waits on its own fetches.
    await page.waitForTimeout(6000);

    // ── 1. nothing threw ────────────────────────────────────────────────────
    expect(
      errors,
      `${route.path} raised errors during load — a throw in setup() takes the ` +
      `whole page body with it and leaves the sidebar behind, looking alive`,
    ).toEqual([]);

    // ── 2. something of its own actually rendered ───────────────────────────
    // Subtract the layout chrome, so "the sidebar drew" cannot pass for
    // "the page drew". That distinction is the entire point of this file.
    const mainText: string = await page.evaluate(() => {
      const chrome = document.querySelector('aside, nav');
      const all = (document.body as HTMLElement).innerText || '';
      const side = chrome ? ((chrome as HTMLElement).innerText || '') : '';
      return all.replace(side, '').trim();
    });

    expect(
      mainText.length,
      `${route.path} rendered only the sidebar shell (${mainText.length} chars ` +
      `of page content). The layout drew and the page did not — this is what a ` +
      `dead render tree looks like from the outside.`,
    ).toBeGreaterThan(40);
  });
}

test('the agent picker on the home page is present', async ({ page }) => {
  // ★The specific component that died in 0.0.518.1: `DataSourceSelector.vue`.
  // Named on its own so a failure says which feature is gone, not merely that
  // the home page is short of text.
  //
  // ★Asserted on data-testid, NOT on the word "Auto". The first version of this
  // test matched that text — and PASSED against the broken bundle, because the
  // MODEL picker sitting beside it renders "Auto" too. Measured: 11 passed,
  // 1 failed on a build where this component was entirely absent. A test that
  // passes on the bug it was written for is worse than no test.
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  await expect(
    page.locator('[data-testid="composer-agent-picker"]').first(),
    'the agent/data-source picker did not render on the home page',
  ).toBeVisible({ timeout: 15000 });
});
