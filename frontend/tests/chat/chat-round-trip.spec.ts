import { test, expect } from '../fixtures/feature-test';

/**
 * The whole product, the way a person uses it: pick an agent, ask a question,
 * get an answer.
 *
 * ★This is the ONLY check that covers the control which took the product down
 * in 0.0.518.1. The chat API round-trip (`scripts/chat-round-trip.py`) proves
 * the agent loop works and would have passed cleanly on that dead build — the
 * failure was in `DataSourceSelector.vue` and the backend was never involved.
 * Selecting the agent is the step that was impossible.
 *
 * ★NOT in the routine smoke and NOT in any suite. It spends real money on a
 * third-party model and takes minutes. Run it deliberately, before a release:
 *
 *   cd frontend
 *   PLAYWRIGHT_BASE_URL=http://localhost:8095 \
 *   SMOKE_TEST_MATCH='**\/chat\/**\/*.spec.ts' \
 *   npx playwright test --config=pw.smoke.config.ts --reporter=line
 *
 * ★What it does NOT assert is that the ANSWER is right. That is a model-quality
 * question and would make this flake on a reworded reply. The number is printed
 * for a human to read, and checked only as a warning. Same rule as Lane A.
 */

const AGENT = process.env.CHAT_AGENT || 'City Mart Retail';
const QUESTION = process.env.CHAT_QUESTION
  || 'How many rows are in the sales table? Answer with just the number.';
// Verified twice against the connector: SELECT COUNT(*) FROM fact_sales = 373932.
const EXPECTED = /373[,.\s]?932/;

test('pick an agent, ask a question, get an answer', async ({ page }) => {
  test.setTimeout(6 * 60 * 1000);   // a real model turn, not a render

  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(`uncaught: ${String(e).split('\n')[0]}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource|favicon/i.test(m.text())) {
      errors.push(`console: ${m.text().split('\n')[0]}`);
    }
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  // ── 1. the control that broke ───────────────────────────────────────────
  const picker = page.locator('[data-testid="composer-agent-picker"]').first();
  await expect(picker, 'the agent picker is not on the page').toBeVisible({ timeout: 20000 });
  await picker.click();

  // The popover lists the agents this user can pick.
  const option = page.getByText(AGENT, { exact: false }).last();
  await expect(option, `agent "${AGENT}" is not offered in the picker`).toBeVisible({ timeout: 20000 });
  await option.click();
  await page.keyboard.press('Escape');   // close the popover, keep the selection

  // ── 2. ask ──────────────────────────────────────────────────────────────
  // ★NOT getByPlaceholder. The composer is `MentionInput`, a contenteditable
  // div whose placeholder is a `data-placeholder` attribute painted by CSS
  // (`[contenteditable]:empty:before { content: attr(data-placeholder) }`).
  // There is no `placeholder` property for Playwright to match, so the
  // placeholder locator finds nothing and the failure reads as "the composer is
  // missing" on a page where it is plainly visible.
  const input = page.locator('[contenteditable="true"][data-placeholder]').first();
  await expect(input, 'the composer input is missing').toBeVisible({ timeout: 15000 });
  await input.click();
  await page.keyboard.type(QUESTION);
  // Enter submits — the icon-only send button carries no stable selector, and
  // the input's own @submit is the same path the button calls.
  await page.keyboard.press('Enter');

  // Submitting from home creates a report and navigates to it.
  await expect(page, 'submitting did not open a report').toHaveURL(/\/reports\/[^/]+/, { timeout: 60000 });

  // ── 3. wait for the turn to produce something ───────────────────────────
  // Poll the page rather than racing a single locator: the answer arrives in
  // streamed blocks, and which block carries it depends on how the agent
  // planned the turn.
  let answered = '';
  const deadline = Date.now() + 4 * 60 * 1000;
  while (Date.now() < deadline) {
    const body: string = await page.evaluate(() => (document.body as HTMLElement).innerText || '');
    // Everything after the echoed question is the response so far.
    const idx = body.indexOf(QUESTION.slice(0, 40));
    const after = idx >= 0 ? body.slice(idx + QUESTION.length) : '';
    if (after.trim().length > 20) {
      answered = after.trim();
      if (EXPECTED.test(after)) break;    // got the real answer, stop early
    }
    await page.waitForTimeout(5000);
  }

  console.log(`\n--- response (first 400 chars) ---\n${answered.slice(0, 400)}\n`);

  // ── assertions ──────────────────────────────────────────────────────────
  expect(errors, 'the chat turn raised errors in the browser').toEqual([]);
  expect(
    answered.length,
    'the agent produced no visible response within 4 minutes',
  ).toBeGreaterThan(20);

  // ★A warning, never a failure. The model may reword, round or add commas;
  // failing on that would make this test lie about the product being broken.
  if (!EXPECTED.test(answered)) {
    console.warn(
      `\n⚠ the expected row count (373932) did not appear in the response.\n` +
      `  The turn completed, so this is a model-quality observation, not a\n` +
      `  product failure. Read the response above and judge it.\n`,
    );
  }
});
