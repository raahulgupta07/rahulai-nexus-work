import { test, expect } from '../fixtures/auth';

// Release notes are an administrator's screen (0.0.528.14).
//
// A member should not have to dismiss a "What's New" dialog before asking a
// question, so for them the sidebar version is inert text and the Changelog row
// is gone. An admin keeps both.
//
// ★This lives in tests/visibility/, NOT tests/home/, on purpose: it needs the
// member storage state, and tests/config/*.json is gitignored. A spec in the
// pre-push smoke set (**/{smoke,home}/**) that depends on an untracked fixture
// fails for everyone on a fresh clone.
//
// ★This is TIDINESS, not access control, and must never be described as the
// latter. /api/changelog already caps a non-admin at the 3 most recent releases
// server-side, and its guard asserts the withheld version strings are absent
// from the response BODY. Hiding a menu row adds nothing to that.

test.describe('Changelog is an administrator surface', () => {

  test('a member sees the version as inert text, with no way to the changelog', async ({ memberPage }) => {
    await memberPage.goto('/');
    await memberPage.waitForLoadState('domcontentloaded');

    // The number stays exactly where it was — removing it would be a different
    // change from the one that was asked for.
    const staticBadge = memberPage.locator('[data-testid="app-version-static"]');
    await expect(staticBadge).toBeVisible({ timeout: 30000 });
    await expect(staticBadge).toHaveText(/^v\d+\.\d+/);

    // And it is not the admin control wearing different styling.
    await expect(memberPage.locator('button[name="app-version"]')).toHaveCount(0);

    // ★The assertion that actually matters. A `div` with no handler still
    // accepts a click; what must not happen is the dialog appearing.
    await staticBadge.click({ force: true });
    await expect(memberPage.getByRole('heading', { name: "What's New" })).toHaveCount(0);
  });

  test('a member has no Changelog row in the account menu', async ({ memberPage }) => {
    await memberPage.goto('/');
    await memberPage.waitForLoadState('domcontentloaded');

    // ★Anchor on the sidebar footer's structure, not on a display name: the
    // name belongs to whichever account minted the state, and keying on it is
    // how the first version of this spec failed against a correct build.
    const footer = memberPage.locator('[data-testid="app-version-static"]').locator('xpath=../..');
    await footer.locator('button').first().click({ timeout: 30000 });

    // Positive control first — otherwise the absence below would also pass if
    // the menu simply never opened.
    await expect(memberPage.getByText('Log out', { exact: true })).toBeVisible({ timeout: 10000 });
    await expect(memberPage.getByText('Changelog', { exact: true })).toHaveCount(0);
  });

  test('an admin keeps the button and the menu row', async ({ adminPage }) => {
    await adminPage.goto('/');
    await adminPage.waitForLoadState('domcontentloaded');

    // ★The half that a refusal-only test would miss. Both of the changes above
    // are also satisfied by removing the feature outright; this is what proves
    // it still exists for the people who should have it.
    await expect(adminPage.locator('button[name="app-version"]')).toBeVisible({ timeout: 30000 });
    await expect(adminPage.locator('[data-testid="app-version-static"]')).toHaveCount(0);

    await adminPage.locator('button[name="app-version"]').click();
    await expect(adminPage.getByRole('heading', { name: "What's New" })).toBeVisible({ timeout: 15000 });
  });
});
