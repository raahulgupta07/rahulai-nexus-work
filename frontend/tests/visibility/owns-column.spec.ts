import { test, expect } from '../fixtures/auth';

// The "Owns" column and the "Needs an owner" banner on Settings ▸ Members.
//
// ★This file lives in tests/visibility/ and must stay here. It uses the
// `adminPage` / `memberPage` fixtures, which read the GITIGNORED
// tests/config/{admin,member}.json storage states — a spec under
// **/{smoke,home}/** that needs them fails on a fresh clone and takes the
// pre-push gate down with it.
//
// ★The ADMIN cases are the load-bearing ones. Every "a member cannot see it"
// assertion here is equally satisfied by the feature having been deleted, so
// each one is paired with a positive control: the members table itself has to
// have rendered for the absence to mean anything.

const MEMBERS_URL = '/settings/members';

// ★★★NOT `getByRole('columnheader')`. Chrome's accessibility tree demotes this
// table — no `scope` attributes, no `<caption>`, no summary — to a LAYOUT table,
// so it exposes no columnheader roles at all. Measured against the live
// 0.0.531.2 screen: `locator('th')` finds all 13 headers and
// `getByRole('columnheader')` returns **0**, while every th is visible with a
// real bounding box. A role query here can never match, which makes an
// `expect(...).toBeVisible()` a permanent failure and — far worse — makes an
// `expect(...).toHaveCount(0)` pass no matter what is on screen. Both mistakes
// were in this file. Locate header cells structurally instead.
const headerCell = (page: any, label: string) =>
  page.locator('thead th').filter({ hasText: new RegExp(`^\\s*${label}\\s*$`) });

// The table is populated by a list request and the Owns counts by one request
// per member AFTER it, so an assertion on the counts has to wait for the table
// first rather than for the page.
async function openMembers(page: any) {
  await page.goto(MEMBERS_URL);
  await page.waitForLoadState('domcontentloaded');
  await expect(headerCell(page, 'Role').first())
    .toBeVisible({ timeout: 30000 });
}

// ★The member half of this screen has TWO honest outcomes: the settings tab is
// `manage_settings`-gated, so a plain member may get no members table at all,
// or may reach it and simply see fewer columns. Both are correct, and an
// assertion that only survives one of them is a flaky test, not a check. This
// returns which world we are in — and proves the session is alive either way,
// so "no table" can never quietly mean "signed out" or "blank page".
async function memberSeesMembersTable(page: any): Promise<boolean> {
  await page.goto(MEMBERS_URL);
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(5000);

  const rendered = await headerCell(page, 'Role')
    .first()
    .isVisible()
    .catch(() => false);

  if (!rendered) {
    await page.goto('/reports');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.getByRole('heading', { name: 'Reports', exact: true }))
      .toBeVisible({ timeout: 15000 });
  }
  return rendered;
}

test.describe('Owns column (Settings ▸ Members)', () => {

  test('admin sees the Owns column, with a cell on every member row', async ({ adminPage }) => {
    await openMembers(adminPage);

    await expect(headerCell(adminPage, 'Owns').first())
      .toBeVisible({ timeout: 15000 });

    // One cell per member row. The count is fetched per member after the list
    // renders, so the cell exists immediately and fills in; what must never
    // happen is a header with no column under it.
    await expect(adminPage.getByTestId('owns-cell').first()).toBeVisible({ timeout: 15000 });
  });

  test('an Owns cell always says something — a count or an em dash, never blank', async ({ adminPage }) => {
    await openMembers(adminPage);

    const cell = adminPage.getByTestId('owns-cell').first();
    await expect(cell).toBeVisible({ timeout: 15000 });
    // "N items" once the summary lands, "—" for somebody who owns nothing OR
    // whose summary could not be fetched. Those two are deliberately the same
    // on screen; an empty cell is neither and would mean the column is broken.
    await expect(cell).toHaveText(/(\d+\s+items|—)/, { timeout: 20000 });
  });

  test('the Transfer action appears only where there is something to transfer', async ({ adminPage }) => {
    await openMembers(adminPage);
    await expect(adminPage.getByTestId('owns-cell').first()).toBeVisible({ timeout: 15000 });
    // Give the per-member summaries time to land; the button is gated on the
    // count being above zero, so asserting before they arrive measures nothing.
    await adminPage.waitForTimeout(5000);

    const badges = await adminPage.getByTestId('owns-cell').getByText(/\d+\s+items/).count();
    const actions = await adminPage.getByTestId('member-transfer').count();
    expect(actions).toBe(badges);
  });

  test('opening Transfer names both halves: what moves and what stays', async ({ adminPage }) => {
    await openMembers(adminPage);
    await expect(adminPage.getByTestId('owns-cell').first()).toBeVisible({ timeout: 15000 });
    await adminPage.waitForTimeout(5000);

    const transfer = adminPage.getByTestId('member-transfer').first();
    // ★A skip is not a pass, and it is recorded as a skip on purpose: on an
    // installation where nobody owns anything there is no row to open, and
    // inventing content from a browser test would leave it behind.
    test.skip(await transfer.count() === 0, 'no member on this install owns anything');

    await transfer.click();
    await expect(adminPage.getByTestId('transfer-recipient')).toBeVisible({ timeout: 15000 });
    await expect(adminPage.getByTestId('transfer-confirm')).toBeVisible();

    // ★The line this release exists for. A dialog that names only what MOVES
    // reads as a complete promise, which is how somebody later asks where their
    // conversations went — so both numbers have to be on screen together.
    const split = adminPage.getByTestId('transfer-conversation-split');
    await expect(split).toBeVisible({ timeout: 15000 });
    await expect(split).toHaveText(/\d+[\s\S]*\d+/);
    // And that what is left behind is not read as deleted.
    await expect(split).toContainText(/not deleted/i);
  });

  test('a member gets no Owns column and no Transfer action', async ({ memberPage }) => {
    const sawTable = await memberSeesMembersTable(memberPage);
    // ★When the table did not render the helper has already navigated away to
    // prove the session is alive, so asserting testids here would be measuring
    // a different page. The screen being gated outright IS the stronger result.
    test.skip(!sawTable, 'the members screen itself is gated for this member');

    // ★The positive control: the table this member DID render still has its Role
    // header. Without it, "no Owns header" is satisfied by a blank page — and
    // with the old role-based locator it was satisfied by anything at all.
    await expect(headerCell(memberPage, 'Role').first()).toBeVisible();
    await expect(headerCell(memberPage, 'Owns')).toHaveCount(0);
    await expect(memberPage.getByTestId('owns-cell')).toHaveCount(0);
    await expect(memberPage.getByTestId('member-transfer')).toHaveCount(0);
  });
});

test.describe('Needs an owner banner', () => {

  test('when work is stranded the banner offers a reassign for each person', async ({ adminPage }) => {
    await openMembers(adminPage);
    await adminPage.waitForTimeout(5000);

    const banner = adminPage.getByTestId('needs-an-owner');
    // The banner is absent entirely when nothing is stranded — that is the
    // designed empty state, not a failure, and this install is usually in it.
    test.skip(await banner.count() === 0, 'nothing is stranded on this install');

    await expect(banner).toBeVisible();
    const rows = adminPage.getByTestId('orphan-row');
    expect(await rows.count()).toBeGreaterThan(0);
    // Every stranded person must have the action, not just the first: a banner
    // that names five people and can only fix one is worse than no banner.
    await expect(adminPage.getByTestId('orphan-reassign')).toHaveCount(await rows.count());

    await adminPage.getByTestId('orphan-reassign').first().click();
    await expect(adminPage.getByTestId('transfer-recipient')).toBeVisible({ timeout: 15000 });
    await expect(adminPage.getByTestId('transfer-confirm')).toBeVisible();
  });

  test('a member never sees the banner', async ({ memberPage }) => {
    // Seeing that content needs an owner is `manage_settings`, which a plain
    // member does not hold. The helper is the positive control: it proves the
    // session is alive whichever of the two gated outcomes we get.
    const sawTable = await memberSeesMembersTable(memberPage);
    test.skip(!sawTable, 'the members screen itself is gated for this member');

    await expect(memberPage.getByTestId('needs-an-owner')).toHaveCount(0);
    await expect(memberPage.getByTestId('orphan-row')).toHaveCount(0);
  });
});
