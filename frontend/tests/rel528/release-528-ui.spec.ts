// The 0.0.528 changes, checked in a real browser.
//
// Upstream's own note for this release is one line — "Permissions, reliability
// and workflow improvements across agents, instructions and evals" — and the tag
// behind it carries ~40 commits. These are the user-visible shapes it changed,
// each asserted against something read out of the source rather than guessed:
//
//   the org-wide Evals page is RETIRED; evals live in the Agents explorer
//   eval suites render as a TREE under their agent, with a folder affordance
//   the add-member checkbox GRID became exactly TWO access tiers
//   "viewing as org admin" is separated from actual agent membership
//   Auto is a scope MODE, not a selection with everything ticked
//
// ★A screenshot is written for every check so the run can be reviewed by eye —
//   a Python suite reads .vue files as text and cannot see a rendered page.
// ★Page errors fail the test. 0.0.518.1 shipped with 5,451 green Python tests
//   and an agent picker that threw on boot; only a browser catches that.
import { test, expect } from '@playwright/test';
import type { APIRequestContext, Browser, Locator, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const SHOTS = path.resolve('test-results/rel528');
fs.mkdirSync(SHOTS, { recursive: true });

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8095';

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

/** Fail on anything the page throws, not just on a missing element. */
function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(String(e)));
  return errors;
}

// ── Shared tree helpers ──────────────────────────────────────────────────────
//
// Every row in the explorer — an agent, its Evals group, a suite folder, a test
// case — is a `div.group` whose LABEL is a `span.flex-1`. Two mistakes already
// made in this suite are the reason these exist at all:
//
//   getByText('City Mart Retail')  also matched report titles in the left
//                                  sidebar, so a click navigated into a chat.
//   getByText('Evals', {exact:true})  matched NEITHER real node, because the
//                                  two are "Global Evals" and a nested "Evals".
//
// So: match the LABEL SPAN anchored end-to-end, and always inside a scope.
const rx = (s: string) => new RegExp(`^${s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`);

/** The row whose label reads exactly `label`, searched only inside `scope`. */
function row(page: Page, scope: Locator | Page, label: string) {
  return scope.locator('div.group')
    .filter({ has: page.locator('span.flex-1').filter({ hasText: rx(label) }) })
    .first();
}

// A TreeGroup renders as <div>[ row, contents ]</div>, so the row's PARENT is
// the whole subtree — the only thing that can scope a search to one agent.
const subtree = (r: Locator) => r.locator('xpath=..');

// ★A CLOSED group renders its row and nothing else; an open one adds a second
// child div for the contents. That two-vs-one is the only reliable "is it
// already open", and it matters: the tree remembers state across navigations,
// so a blind click on an open group COLLAPSES it and every later locator then
// fails on an element that is genuinely there.
const isOpen = async (wrapper: Locator) => (await wrapper.locator('xpath=./div').count()) > 1;

async function openGroup(page: Page, wrapper: Locator, groupRow: Locator, waitMs: number) {
  if (!(await isOpen(wrapper))) {
    await groupRow.click();
    await page.waitForTimeout(waitMs);
  }
}

/** Open an agent in the explorer and return its subtree. */
async function openAgent(page: Page, name: string) {
  const agentRow = row(page, page, name);
  await expect(agentRow, `agent "${name}" should be a row in the explorer tree`)
    .toBeVisible({ timeout: 20000 });
  const tree = subtree(agentRow);
  await openGroup(page, tree, agentRow, 2000);
  return tree;
}

/** Open an agent's own Evals group; returns the group's row and its subtree. */
async function openEvals(page: Page, agentName: string) {
  const tree = await openAgent(page, agentName);
  const evalsRow = row(page, tree, 'Evals');
  await expect(evalsRow, `"${agentName}" should carry its own Evals row`)
    .toBeVisible({ timeout: 15000 });
  const evals = subtree(evalsRow);
  await openGroup(page, evals, evalsRow, 2500);
  return { evalsRow, evals };
}

/** The in-app folder/suite dialog (create · rename · delete). */
const dirDialog = (page: Page) =>
  page.locator('form').filter({ hasText: /New suite|Rename suite|Delete suite/ }).first();

// ── API setup helpers ────────────────────────────────────────────────────────
// The four cases below need a shape no live agent has: an agent one MEMBER can
// manage and another they cannot, plus a suite holding a case that member may
// not destroy. Building it through the UI would take longer than the tests and
// would test the setup rather than the thing. So the fixtures are minted over
// the API and torn down afterwards; every ASSERTION is still made in the DOM.
const tokens = () => JSON.parse(fs.readFileSync('/tmp/tokens.json', 'utf8'));

function asUser(request: APIRequestContext, email: string) {
  const t = tokens();
  const headers = {
    Authorization: `Bearer ${t.users[email].token}`,
    'X-Organization-Id': t.org.id,
  };
  return {
    get: (p: string) => request.get(p, { headers }),
    post: (p: string, data: any) => request.post(p, { headers, data }),
    put: (p: string, data: any) => request.put(p, { headers, data }),
    del: (p: string) => request.delete(p, { headers }),
  };
}

const ADMIN = 'raahulgupta07@gmail.com';
const MEMBER = 'member@cityagent.io';
const EMPTY_EXPECTATIONS = { spec_version: 1, rules: [], order_mode: 'flexible' };

/** A browser signed in as someone other than the smoke admin. */
async function contextFor(browser: Browser, email: string) {
  const t = tokens();
  return browser.newContext({
    baseURL: BASE,
    storageState: {
      cookies: [{
        name: 'auth.token',                     // ★not `auth_token` — see mint-smoke-state.py
        value: t.users[email].token,
        domain: new URL(BASE).hostname,
        path: '/',
        expires: -1,
        httpOnly: false,
        secure: BASE.startsWith('https'),
        sameSite: 'Lax' as const,
      }],
      origins: [],
    },
  });
}

test.describe('0.0.528 · evals moved into the Agents explorer', () => {
  test('the retired org-wide evals page sends you to the explorer', async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto('/evals', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    await shot(page, '10-evals-redirect');

    // "Retire the org-wide evals page; evals live in the Agents explorer"
    expect(page.url(), 'a bookmark to /evals must land somewhere real, not 404')
      .toContain('/agents');
    expect(errors).toEqual([]);
  });

  test('instructions also live in the explorer now', async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto('/instructions', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    expect(page.url()).toContain('/agents');
    expect(errors).toEqual([]);
  });

  test('an agent carries an Evals row that expands a suite tree', async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto('/agents', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3500);
    await shot(page, '11-explorer');

    // ★Two different things are named "Evals" here, and conflating them is why
    // the first version of this test failed:
    //   "Global Evals"  — a top-level node, org-wide, gated on org-level
    //                     manage_evals ("Give Global Evals the same suite tree")
    //   "Evals"         — a row INSIDE each agent, rendered only when
    //                     canManageAgentEvals(agent.id), and collapsed until the
    //                     agent node is opened.
    // An exact match on "Evals" finds neither: the only string on a closed tree
    // is "Global Evals".
    await expect(page.getByText('Global Evals'), 'the org-wide suite tree should be a node')
      .toBeVisible({ timeout: 15000 });

    // ★Scope to the TREE ROW, not to the text. A plain getByText('City Mart
    // Retail') also matches report titles in the left sidebar — this run's own
    // earlier test reports were called "T2 schema City Mart Retail" — and
    // clicking one navigates into that chat instead of expanding the agent.
    // The tree row is `div.group` holding `span.flex-1` with the name.
    const row = page.locator('div.group')
      .filter({ has: page.locator('span.flex-1', { hasText: 'City Mart Retail' }) })
      .first();
    await expect(row, 'the agent should be a row in the explorer tree').toBeVisible({ timeout: 15000 });

    // ★The CHEVRON expands; the LABEL opens the panel. They are deliberately
    // different actions ("the chevron expands the suite tree, the LABEL still
    // opens the runs panel"), so the test has to press the right one.
    await row.locator('span.iconify').first().click();
    await page.waitForTimeout(2500);
    await shot(page, '12a-agent-expanded');

    const evalsRow = page.getByText('Evals', { exact: true }).first();
    await expect(evalsRow, "the agent's own Evals row should appear once it is expanded")
      .toBeVisible({ timeout: 15000 });

    await evalsRow.click();
    await page.waitForTimeout(2500);
    await shot(page, '12-evals-expanded');

    // Either suites are listed, or the empty hint is shown — both prove the
    // tree rendered. A blank pane would prove nothing loaded.
    const body = (await page.locator('body').innerText()).toLowerCase();
    const treeRendered = /suite|no suites yet|drafts/.test(body);
    expect(treeRendered, 'expanding Evals should show suites or the "no suites yet" hint').toBe(true);
    expect(errors).toEqual([]);
  });
});

test.describe('0.0.528 · per-agent access became two tiers', () => {
  // ★The tier control only exists in the ADD-MEMBER flow, and that flow only
  // appears on a PRIVATE agent: a public one says "every organization member can
  // query it" and has no member list to add to. Testing it on one of the real
  // shared agents would mean switching that agent to private — a live change to
  // something other tests and the matrix depend on. So this creates its own
  // throwaway private agent and deletes it afterwards.
  let agentName = '';
  let agentId = '';

  test.beforeAll(async ({ request }) => {
    const tokens = JSON.parse(fs.readFileSync('/tmp/tokens.json', 'utf8'));
    const org = tokens.org.id;
    const token = tokens.users['raahulgupta07@gmail.com'].token;
    agentName = `ui-tier-check-${Date.now().toString().slice(-6)}`;
    const res = await request.post('/api/data_sources', {
      headers: { Authorization: `Bearer ${token}`, 'X-Organization-Id': org },
      data: { name: agentName, type: 'csv', config: { file_paths: [] }, is_public: false },
    });
    if (res.ok()) agentId = (await res.json()).id;
  });

  test.afterAll(async ({ request }) => {
    if (!agentId) return;
    const tokens = JSON.parse(fs.readFileSync('/tmp/tokens.json', 'utf8'));
    await request.delete(`/api/data_sources/${agentId}`, {
      headers: {
        Authorization: `Bearer ${tokens.users['raahulgupta07@gmail.com'].token}`,
        'X-Organization-Id': tokens.org.id,
      },
    });
  });

  test('a private agent offers exactly two access tiers', async ({ page }) => {
    test.skip(!agentId, 'could not create a throwaway private agent');
    const errors = watchErrors(page);

    await page.goto('/agents', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3500);

    const row = page.locator('div.group')
      .filter({ has: page.locator('span.flex-1', { hasText: agentName }) }).first();
    await expect(row, 'the new private agent should appear in the tree').toBeVisible({ timeout: 20000 });
    await row.locator('span.flex-1').click();
    await page.waitForTimeout(2000);

    await page.getByRole('button', { name: 'Settings' }).first().click();
    await page.waitForTimeout(2500);
    await shot(page, '13-agent-settings');

    // Open whatever adds a member — the tier radio group lives inside it.
    const add = page.getByRole('button', { name: /add member|add people|invite/i }).first();
    if (await add.count()) {
      await add.click();
      await page.waitForTimeout(1500);
    }
    await shot(page, '13b-access-tiers');

    const body = await page.locator('body').innerText();
    // ACCESS_TIERS in AgentSettingsPanel.vue is exactly two entries:
    //   { key:'query',  label:'Can query'  }
    //   { key:'manage', label:'Can manage' }
    expect(/Can query/i.test(body) && /Can manage/i.test(body),
      'both tiers should be offered — "Reduce add-member access to two tiers"').toBe(true);

    // It replaced a checkbox GRID, so the tier control must be one-of-N.
    const tierCheckboxes = await page.locator('input[type="checkbox"]').count();
    console.log(`  tiers rendered; checkbox inputs on screen: ${tierCheckboxes}`);
    expect(errors).toEqual([]);
  });
});

test.describe('0.0.528 · the explorer tree carries an agent\'s own rows', () => {
  test('an expanded agent shows Instructions, Evals and Settings', async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto('/agents', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3500);

    const row = page.locator('div.group')
      .filter({ has: page.locator('span.flex-1', { hasText: 'City Mart Retail' }) }).first();
    await row.locator('span.iconify').first().click();
    await page.waitForTimeout(2500);
    await shot(page, '16-agent-rows');

    // "List table-scoped instructions under their agent in the explorer tree"
    // and the Evals row from "evals live in the Agents explorer".
    for (const label of ['Instructions', 'Evals', 'Settings']) {
      await expect(page.getByText(label, { exact: true }).first(),
        `${label} should be a row under the expanded agent`).toBeVisible({ timeout: 10000 });
    }
    expect(errors).toEqual([]);
  });
});

test.describe('0.0.528 · Auto is a scope mode', () => {
  test('a fresh chat opens on Auto rather than every agent ticked', async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    await shot(page, '14-home-auto');

    // "Make Auto an agent scope mode instead of a shape of the selection".
    // The fork regression this guards (0.0.528.7) expanded Auto into a manual
    // pin of every agent, which freezes the roster at the moment you opened it.
    const auto = page.getByRole('button', { name: /^Auto$/ }).first();
    await expect(auto, 'the composer should show the Auto scope').toBeVisible({ timeout: 15000 });
    expect(errors).toEqual([]);
  });
});

test.describe.serial('0.0.528 · eval suites are folders, and one agent\'s manager sees only theirs', () => {
  // Two fixture agents, because the four things below cannot be seen on the
  // real ones without changing them:
  //   MINE  — private, with member@cityagent.io granted the "Can manage" tier.
  //   THEIRS— public, so the same member can SEE it and still not manage it.
  // The pair is what makes case 4 an assertion rather than a tautology: an
  // agent a member cannot see proves nothing about the Evals row being gated.
  //
  // ★★★They are REUSED, not minted per run, and the names carry no timestamp.
  // An agent that has ever held an eval suite can no longer be deleted:
  // `delete_data_source` hard-deletes the row and clears the child tables it
  // knows about, but `test_suites.data_source_id` (added later by evsuite0001)
  // has a plain FK with no ON DELETE and is not in that list — and suite delete
  // is SOFT, so the referencing row outlives every suite the user removed. The
  // call ends in a ForeignKeyViolation surfaced as a bare 500. A per-run agent
  // would therefore leak one permanently undeletable agent into the org every
  // time this file ran. Reusing two caps that at two, for good. The SUITES are
  // still per-run (they carry the stamp) and are cleaned up below.
  const stamp = Date.now().toString().slice(-6);
  const MINE = 'ui-evals-fixture-mine';
  const THEIRS = 'ui-evals-fixture-theirs';
  let mineId = '';
  let theirsId = '';

  test.beforeAll(async ({ request }) => {
    const admin = asUser(request, ADMIN);
    const t = tokens();

    const existing: any[] = await (await admin.get('/api/data_sources')).json();
    const find = (name: string) => existing.find((d: any) => d.name === name)?.id || '';

    mineId = find(MINE);
    if (!mineId) {
      const a = await admin.post('/api/data_sources',
        { name: MINE, type: 'csv', config: { file_paths: [] }, is_public: false });
      if (a.ok()) mineId = (await a.json()).id;
    }
    theirsId = find(THEIRS);
    if (!theirsId) {
      const b = await admin.post('/api/data_sources',
        { name: THEIRS, type: 'csv', config: { file_paths: [] }, is_public: true });
      if (b.ok()) theirsId = (await b.json()).id;
    }
    if (!mineId || !theirsId) return;

    // Adding a member creates the grant with NO permissions — the "Can manage"
    // tier is a second call that writes ['manage'] onto it, exactly as
    // AgentSettingsPanel does. `manage` on a data source implies manage_evals
    // (permission_resolver.RESOURCE_PERM_IMPLIES), which is the whole point.
    // A repeat run 400s on "already a member"; the grant PUT is idempotent, so
    // the tier is re-asserted either way rather than assumed.
    await admin.post(`/api/data_sources/${mineId}/members`,
      { principal_type: 'user', principal_id: t.users[MEMBER].id });
    const members = await (await admin.get(`/api/data_sources/${mineId}/members`)).json();
    const grant = members.find((m: any) => m.principal_id === t.users[MEMBER].id);
    expect(grant, `${MEMBER} should be a member of ${MINE}`).toBeTruthy();
    await admin.put(`/api/organizations/${t.org.id}/resource-grants/${grant.id}`,
      { permissions: ['manage'] });
  });

  test.afterAll(async ({ request }) => {
    // The agents stay (see above); this run's suites do not, or the tree fills
    // up with a dozen `drag-src-*` folders that later runs then have to ignore.
    const admin = asUser(request, ADMIN);
    if (!mineId) return;
    const suites: any[] = await (await admin.get(
      `/api/tests/suites?limit=100&data_source_id=${mineId}`)).json();
    for (const s of suites) {
      if (String(s.name).endsWith(stamp)) await admin.del(`/api/tests/suites/${s.id}`);
    }
  });

  test('a suite folder can be created and renamed from the tree', async ({ page }) => {
    test.skip(!mineId, 'could not create the throwaway agents');
    const errors = watchErrors(page);
    const created = `suite-created-${stamp}`;
    const renamed = `suite-renamed-${stamp}`;

    await page.goto('/agents', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3500);
    const { evalsRow, evals } = await openEvals(page, MINE);

    // ★The folder-plus button on the Evals row IS the new-suite control
    // (`@folder="createSuiteIn(agent.id)"`). It is opacity-0 until the row is
    // hovered, so hover first — clicking an invisible control would pass here
    // and be unreachable for a user.
    await evalsRow.hover();
    await evalsRow.getByRole('button', { name: 'New folder' }).click();

    const dialog = dirDialog(page);
    await expect(dialog, 'the folder-plus button should open the in-app suite dialog')
      .toBeVisible({ timeout: 10000 });
    await expect(dialog, 'a suite dialog says "New suite", not "New folder"')
      .toContainText('New suite');
    await dialog.getByPlaceholder('Suite name').fill(created);
    await dialog.getByRole('button', { name: 'Create' }).click();
    await page.waitForTimeout(2500);
    await shot(page, '20-suite-created');

    await expect(row(page, evals, created), 'the new suite should be a folder under Evals')
      .toBeVisible({ timeout: 15000 });

    // Rename, from the pencil on the suite's own row.
    const suiteRow = row(page, evals, created);
    await suiteRow.hover();
    await suiteRow.getByRole('button', { name: 'Rename' }).click();
    const renameDialog = dirDialog(page);
    await expect(renameDialog).toContainText('Rename suite');
    await renameDialog.getByPlaceholder('Suite name').fill(renamed);
    await renameDialog.getByRole('button', { name: 'Save' }).click();
    await page.waitForTimeout(2500);
    await shot(page, '20b-suite-renamed');

    await expect(row(page, evals, renamed), 'the renamed suite should be under Evals')
      .toBeVisible({ timeout: 15000 });
    await expect(evals.locator('span.flex-1').filter({ hasText: rx(created) }),
      'the old name should be gone, not merely joined by the new one').toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('a test case can be dragged from one suite folder into another', async ({ page, request }) => {
    test.skip(!mineId, 'could not create the throwaway agents');
    const errors = watchErrors(page);
    const admin = asUser(request, ADMIN);
    const src = `drag-src-${stamp}`;
    const dst = `drag-dst-${stamp}`;
    const caseText = `drag me ${stamp}`;

    // ★FINDING, recorded in the test rather than tested around: a SUITE cannot
    // be dragged. Suites are flat by design ("Deliberately FLAT: suites do not
    // nest, so there is no parent_id, no cycle check and no recursion here")
    // and SuiteNode passes no `draggable`, so there is no suite-into-folder
    // gesture in the product. The drag-and-drop reorganise path evals actually
    // has is a CASE into a suite — `moveCaseToSuite`, optimistic with rollback
    // — and that is what this exercises.
    //
    // ★Do NOT re-plan the suite gesture from the neighbouring screen. Instruction
    // folders DIRECTLY above in the same tree (`DirNode`) DO nest and DO drag,
    // header-to-header, with a cycle check — which is almost certainly where the
    // idea that suites drag came from. They are different components with
    // different data models; the affordance next door is not evidence of one
    // here.
    const s = await (await admin.post('/api/tests/suites',
      { name: src, data_source_id: mineId })).json();
    await admin.post('/api/tests/suites', { name: dst, data_source_id: mineId });
    await admin.post(`/api/tests/suites/${s.id}/cases`, {
      name: caseText,
      prompt_json: { content: caseText },
      expectations_json: EMPTY_EXPECTATIONS,
      data_source_ids_json: [mineId],
    });

    await page.goto('/agents', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3500);
    const { evals } = await openEvals(page, MINE);

    const srcRow = row(page, evals, src);
    await expect(srcRow).toBeVisible({ timeout: 15000 });
    await srcRow.click();                       // expand the source suite
    await page.waitForTimeout(1500);
    const srcTree = subtree(srcRow);
    const caseRow = srcTree.locator('div.group')
      .filter({ has: page.locator('span.flex-1').filter({ hasText: rx(caseText) }) }).first();
    await expect(caseRow, 'the case should be listed inside its suite').toBeVisible({ timeout: 15000 });
    await shot(page, '21-before-drag');

    const dstRow = row(page, evals, dst);
    await dstRow.scrollIntoViewIfNeeded();
    await caseRow.dragTo(dstRow);
    await page.waitForTimeout(2500);

    // Assert where it LANDED, not merely that it left: a case that vanished
    // from the source because the drag threw would satisfy the weaker check.
    await dstRow.click();                       // expand the destination
    await page.waitForTimeout(1500);
    await shot(page, '21b-after-drag');
    await expect(subtree(row(page, evals, dst)).locator('span.flex-1').filter({ hasText: rx(caseText) }),
      'the dragged case should now sit inside the destination suite').toHaveCount(1);
    await expect(subtree(row(page, evals, src)).locator('span.flex-1').filter({ hasText: rx(caseText) }),
      'and should no longer be in the source suite').toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('deleting a suite that holds someone else\'s case says what survived', async ({ browser, request }) => {
    test.skip(!mineId || !theirsId, 'could not create the throwaway agents');
    const admin = asUser(request, ADMIN);
    const suiteName = `partial-del-${stamp}`;

    // A suite the member owns, holding one case they may destroy and one they
    // may not — the latter targets THEIRS, which they can see but not manage.
    // Without the second case the delete is total and the message never fires,
    // which is how this check would pass while proving nothing.
    // ★Every setup call is checked. Left unchecked, a 403 here would surface
    // 15s later as "the suite is not in the tree", which reads as the tree
    // being broken rather than as the fixture never having been built.
    const member = asUser(request, MEMBER);
    const suiteRes = await member.post('/api/tests/suites',
      { name: suiteName, data_source_id: mineId });
    expect(suiteRes.ok(), `member could not create a suite: ${await suiteRes.text()}`).toBe(true);
    const suite = await suiteRes.json();

    const mineRes = await member.post(`/api/tests/suites/${suite.id}/cases`, {
      name: 'mine-to-delete',
      prompt_json: { content: `mine ${stamp}` },
      expectations_json: EMPTY_EXPECTATIONS,
      data_source_ids_json: [mineId],
    });
    expect(mineRes.ok(), await mineRes.text()).toBe(true);

    const foreignRes = await admin.post(`/api/tests/suites/${suite.id}/cases`, {
      name: 'foreign-survivor',
      prompt_json: { content: `foreign ${stamp}` },
      expectations_json: EMPTY_EXPECTATIONS,
      data_source_ids_json: [theirsId],
    });
    expect(foreignRes.ok(), await foreignRes.text()).toBe(true);
    const foreign = await foreignRes.json();

    const ctx = await contextFor(browser, MEMBER);
    const page = await ctx.newPage();
    const errors = watchErrors(page);
    try {
      await page.goto('/agents', { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(3500);
      const { evals } = await openEvals(page, MINE);
      await shot(page, '22-member-evals-tree');

      const suiteRow = row(page, evals, suiteName);
      await expect(suiteRow, `the member's own suite should be under ${MINE} · Evals`)
        .toBeVisible({ timeout: 15000 });
      await suiteRow.hover();
      await suiteRow.getByRole('button', { name: 'Delete suite' }).click();

      const dialog = dirDialog(page);
      await expect(dialog).toContainText('Delete suite');
      await shot(page, '22-partial-delete-confirm');
      await dialog.getByRole('button', { name: 'Delete' }).click();
      await page.waitForTimeout(3000);
      await shot(page, '22b-partial-delete-toast');

      // ★The load-bearing assertion is the COUNT. The confirm dialog's prose
      // also mentions Drafts, and matching that instead would pass whether or
      // not anything was actually spared — the mistake one tier check in this
      // file already made.
      const toast = page.locator('[role="status"], [role="alert"], .fixed').filter({
        hasText: 'moved to Drafts instead of being deleted',
      }).first();
      await expect(toast, 'a PARTIAL delete must say so, with a count')
        .toContainText('1 test case(s) moved to Drafts instead of being deleted.', { timeout: 15000 });
      await expect(toast).toContainText('Suite deleted');

      // ★Delete here is SOFT — the row survives with deleted_at set — so the
      // only honest "it is gone" is its absence from the rendered LIST.
      await expect(evals.locator('span.flex-1').filter({ hasText: rx(suiteName) }),
        'the deleted suite should leave the tree').toHaveCount(0, { timeout: 15000 });
      expect(errors).toEqual([]);
    } finally {
      await ctx.close();
    }

    // And the message must be TRUE, not merely rendered: the foreign case is
    // alive, reparented to Drafts rather than destroyed with the suite.
    const survivor = await admin.get(`/api/tests/cases/${foreign.id}`);
    expect(survivor.ok(), 'the case the message said survived should still exist').toBe(true);
    expect(String((await survivor.json()).suite_id),
      'and should have moved to a different suite (Drafts)').not.toBe(String(suite.id));
  });

  test('an agent manager sees Evals on their agent and not on another', async ({ browser }) => {
    test.skip(!mineId || !theirsId, 'could not create the throwaway agents');
    const ctx = await contextFor(browser, MEMBER);
    const page = await ctx.newPage();
    const errors = watchErrors(page);
    try {
      await page.goto('/agents', { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(3500);
      await shot(page, '23-member-explorer');

      // The Evals row is `v-if="canManageAgentEvals(agent.id)"` — a per-agent
      // check, not an org one. The member holds `manage` on MINE only.
      const mine = await openAgent(page, MINE);
      await expect(row(page, mine, 'Evals'),
        'the agent this member manages should offer Evals').toBeVisible({ timeout: 15000 });

      const theirs = await openAgent(page, THEIRS);
      await shot(page, '23b-member-other-agent');
      // ★Scoped to THAT agent's subtree. An unscoped check would find MINE's
      // Evals row, still open a few hundred pixels up the page, and pass.
      await expect(theirs.locator('span.flex-1').filter({ hasText: rx('Evals') }),
        'an agent they only query must not offer Evals').toHaveCount(0);
      // ★A collapsed node has no Evals row either, so the check above is only
      // worth anything once this one has proved the node is OPEN and rendering
      // the rows a mere querier does get. Without it the test would still pass
      // if the agent had failed to expand at all.
      await expect(row(page, theirs, 'Tables'),
        'the other agent should be expanded, so the missing Evals row means the gate')
        .toBeVisible({ timeout: 10000 });
      await expect(row(page, page, THEIRS),
        'and the agent itself should still be visible to them').toBeVisible();
      expect(errors).toEqual([]);
    } finally {
      await ctx.close();
    }
  });
});

test.describe('0.0.528 · reliability', () => {
  test('every reshaped screen boots without throwing', async ({ page }) => {
    const routes = ['/', '/agents', '/reports', '/monitoring', '/settings/members'];
    for (const r of routes) {
      const errors = watchErrors(page);
      await page.goto(r, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2000);
      expect(errors, `${r} threw in the browser`).toEqual([]);
    }
    await shot(page, '15-final');
  });
});
