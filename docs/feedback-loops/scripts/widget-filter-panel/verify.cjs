/**
 * Verification for the widget filter panel fixes ported from upstream 0.0.519.
 * Adapted from upstream's verify_filter_panel.js, which assumes their seeded
 * 122-row stocks widget. This runs against a REAL report on a running instance.
 *
 *   docker exec -w /app/backend dash-app python mint-smoke-state.py /tmp/smoke-state.json
 *   docker cp dash-app:/tmp/smoke-state.json /tmp/smoke-state.json
 *   cd frontend && REPORT_URL=http://localhost:8095/reports/<id> node ../docs/feedback-loops/scripts/widget-filter-panel/verify.cjs
 *
 * ★Must run from frontend/ — a script elsewhere cannot resolve @playwright/test.
 *
 * ★★★Assertions read the PANEL'S OWN "N of M rows" preview, never a page-wide
 * grid row count. A report with more than one widget has more than one AG Grid
 * pagination line, and `document.body.innerText.match(/1 to (\d+) of (\d+)/)`
 * returns whichever comes first — which is not necessarily the widget whose
 * funnel you clicked. Measured: on a real report that read "1 rows" unfiltered
 * and "54 rows" after applying a filter, i.e. filtering APPEARED to add rows,
 * and the committed-removal check appeared to fail when the fix was working.
 * Upstream's script is correct for their single-widget fixture and wrong here.
 *
 * ★The final check is the headline bug, and it keys off "Clear". That button
 * renders only while something is applied, so its presence after committing a
 * removal is exactly the uncommitted-removal defect: panel says "No filters
 * applied" over data that is still filtered.
 */
const PANEL = 'div.w-\\[380px\\]';
let fails = 0;
const say = (...a) => console.log(...a);
const check = (n, c, d) => { if (!c) fails++; say(`[${c ? 'PASS' : 'FAIL'}] ${n}${d ? '\n         ' + d : ''}`); };
const panelOpen = p => p.locator(PANEL).count().then(c => c > 0);
const panelText = async p => (await panelOpen(p))
  ? (await p.locator(PANEL).first().innerText()).replace(/\n+/g, ' | ') : '<closed>';
// The panel's OWN preview — belongs to the widget under test, unlike a
// page-wide grid count on a report that has more than one widget.
const preview = async p => ((await panelText(p)).match(/(\d+) of (\d+) rows/) || []).slice(1).map(Number);
async function clickFunnel(page) {
  for (const b of await page.locator('button').all()) {
    const h = await b.innerHTML().catch(() => '');
    if (h.includes('funnel')) { await b.click(); await page.waitForTimeout(1400); return true; }
  }
  return false;
}
async function xRemovers(page) {
  const out = [];
  for (const b of await page.locator(PANEL).locator('button').all()) {
    const h = await b.innerHTML().catch(() => '');
    if (h.includes('x-mark')) out.push(b);
  }
  return out;
}
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ storageState: '/tmp/smoke-state.json', viewport: { width: 1500, height: 950 } });
  const page = await ctx.newPage();
  page.on('pageerror', e => say('[pageerror]', String(e).slice(0, 160)));
  await page.goto(process.env.REPORT_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(20000);

  await clickFunnel(page);
  await page.getByRole('button', { name: 'Add filter' }).click();
  await page.waitForTimeout(900);
  let [m, t] = await preview(page);
  check('BUG C: an empty new condition does not zero the preview', m === t, `preview: ${m} of ${t}`);

  const ops = page.locator(PANEL).locator('button').filter({ hasText: /^equals$/ });
  if (await ops.count()) { await ops.last().click(); await page.waitForTimeout(600);
    const o = page.getByRole('option', { name: /^contains$/i }).first();
    if (await o.count()) { await o.click(); await page.waitForTimeout(600); } else await page.keyboard.press('Escape'); }
  await page.locator(PANEL).locator('input[type="text"]').last().fill('a');
  await page.waitForTimeout(1100);
  let [m2] = await preview(page);
  check('the condition narrows the preview', m2 < t, `preview: ${m2} of ${t}`);

  await page.getByRole('button', { name: 'Apply' }).click();
  await page.waitForTimeout(2200);
  check('BUG B: popover closes on Apply', !(await panelOpen(page)));

  await clickFunnel(page);
  check('reopen shows the applied condition (BUG D: column survives the round trip)',
    /contains/.test(await panelText(page)), (await panelText(page)).slice(0, 140));

  const rs = await xRemovers(page);
  await rs[0].click(); await page.waitForTimeout(1000);
  check('BUG A: Apply is reachable after removing the LAST condition',
    (await page.getByRole('button', { name: 'Apply' }).count()) > 0, (await panelText(page)).slice(0, 140));

  await page.getByRole('button', { name: 'Apply' }).click();
  await page.waitForTimeout(2500);
  await clickFunnel(page);
  const fin = await panelText(page);
  // Pre-fix this said "Clear | No filters applied" — Clear only renders while
  // something is still applied, so its presence IS the uncommitted removal.
  check('BUG A: the removal was COMMITTED — no stale Clear on reopen',
    /No filters applied/.test(fin) && !/Clear/.test(fin), fin.slice(0, 140));

  say(''); say(fails === 0 ? 'ALL CHECKS PASSED' : `${fails} CHECK(S) FAILED`);
  await browser.close(); process.exit(fails ? 1 : 0);
})();
