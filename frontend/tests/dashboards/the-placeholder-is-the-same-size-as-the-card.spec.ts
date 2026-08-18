import { test, expect } from '../fixtures/feature-test';

/**
 * A loading placeholder must be the size of the thing it stands in for.
 * /dashboards was not: it drew 10 bone cards against a page limit of 15 — one
 * row short at lg:grid-cols-5 — over a 60px card body, where ArtifactCard's
 * body is 75px (p-3 + a text-xs line-clamp-2 title of 32px + mt-1 + a 10px
 * byline at the inherited 1.5 line-height + p-3). Everything under the grid
 * stepped down as the real cards arrived.
 *
 * ★This measures the RENDER, not the classes. A class-string assertion passes
 * while a padding change inside ArtifactCard moves the real card — which is
 * precisely how the placeholder drifted in the first place. So: stall the
 * artifacts call, measure the bone, release it, measure the card, compare.
 *
 * ★It also runs on a surface that is actually mounted. The same defect exists
 * in components/home/RecentReports.vue, which has no caller anywhere in the
 * app — a guard pointed there would measure code no user ever loads and pass
 * forever.
 *
 * Card COUNT is deliberately not asserted: 15 is the page's own pagination
 * limit, the honest ceiling, not a measurement. Per-card height is what must
 * not drift, and that is what this pins.
 */

// Subpixel only. Both boxes are built from the same line-height arithmetic,
// so anything past a rounding error is a real divergence.
const TOLERANCE_PX = 2;

test('the bone card is the same size as the artifact card it replaces', async ({ page }) => {
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });

  await page.route('**/artifacts**', async (route) => {
    await held;
    await route.continue();
  });

  await page.goto('/dashboards', { waitUntil: 'commit' });
  await page.waitForLoadState('domcontentloaded');

  const bone = page.getByTestId('artifact-card-bone').first();
  await expect(bone).toBeVisible({ timeout: 20000 });
  const boneBox = await bone.boundingBox();
  expect(boneBox, 'bone card has no box').not.toBeNull();

  release();

  // Positive control. Without it these assertions could pass against a page
  // that never settled — a bone measured twice and called a match.
  //
  // ★Not a skip. A skip here reports green on a workspace that simply failed
  // to load, which is the exact shape of the bug this file exists to catch.
  const card = page.getByTestId('artifact-card').first();
  await expect(card, 'the real cards never arrived after the stall was released')
    .toBeVisible({ timeout: 30000 });
  await expect(bone).toHaveCount(0);

  const cardBox = await card.boundingBox();
  expect(cardBox, 'artifact card has no box').not.toBeNull();

  const dh = Math.abs(boneBox!.height - cardBox!.height);
  const dw = Math.abs(boneBox!.width - cardBox!.width);

  expect(dh, `card height drifted ${dh}px (bone ${boneBox!.height}, card ${cardBox!.height})`)
    .toBeLessThanOrEqual(TOLERANCE_PX);
  expect(dw, `card width drifted ${dw}px (bone ${boneBox!.width}, card ${cardBox!.width})`)
    .toBeLessThanOrEqual(TOLERANCE_PX);
});
