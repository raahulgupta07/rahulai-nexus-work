import { test, expect } from '@playwright/test';

/**
 * Navigate away from home and back, watching for a setup() throw.
 *
 * `home-menu.spec.ts` covers the outbound leg — home → /monitoring, asserting
 * the view really swapped rather than only the URL. This covers the return,
 * which the existing suite does not touch and which is where the 0.0.518.1
 * damage was actually visible to a person: the first load of a route is fine
 * on a fresh boot, so the crash only shows once the router has moved and come
 * back and the component has to mount a second time.
 *
 * The agent picker is the specific component involved — `DataSourceSelector`,
 * whose `watch(isWorkspaceAuto, …, { immediate: true })` evaluates a chain of
 * computeds during setup. If any const in that chain is still in its temporal
 * dead zone, this is the mount that throws.
 */
test('home survives being navigated away from and returned to', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(`uncaught: ${String(e).split('\n')[0]}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource|favicon|\[intercom\]/i.test(m.text())) {
      errors.push(`console: ${m.text().split('\n')[0]}`);
    }
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const picker = page.locator('[data-testid="agent-picker"], [data-test="agent-picker"]')
    .or(page.locator('button:has-text("Auto")'))
    .first();
  const pickerFirstLoad = await picker.count();
  // ★Without this the test is vacuous: if the locator matches nothing, the
  // final comparison is 0 === 0 and passes on a page where the component never
  // rendered at all. Anchor the baseline before comparing against it.
  expect(
    pickerFirstLoad,
    'the agent picker was not found on first load — this spec cannot detect ' +
    'its disappearance, so the comparison below would be meaningless',
  ).toBeGreaterThan(0);

  await page.locator('a[href="/monitoring"]').first().click();
  await expect(page).toHaveURL(/\/monitoring$/, { timeout: 20000 });
  await page.waitForTimeout(3000);

  // Browser back, not a sidebar link — the sidebar has no anchor to "/", and
  // history navigation is the realistic way a person returns. It re-runs the
  // router client-side, so the component mounts again without a page reload.
  await page.goBack({ waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/localhost:\d+\/$/, { timeout: 20000 });
  await page.waitForTimeout(4000);

  expect(
    errors,
    'returning to home raised errors — a const read during setup() was still ' +
    'in its temporal dead zone on the second mount',
  ).toEqual([]);

  expect(
    await picker.count(),
    'the agent picker was present on first load but not after navigating back — ' +
    'that is the component throwing in setup(), exactly as in 0.0.518.1',
  ).toBe(pickerFirstLoad);
});
