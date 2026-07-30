#!/usr/bin/env node
// ★Was an absolute path into one contributor's home directory
// (/home/user/bagofwords/...), inherited from the upstream project. It could
// only ever resolve on that machine, so this helper was dead everywhere else.
const REPO = require('path').resolve(__dirname, '..');
const { chromium } = require(REPO + '/frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  });
  for (const code of ['en', 'es', 'he']) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await ctx.addInitScript((c) => {
      try { localStorage.setItem('bow.locale', c); } catch {}
    }, code);
    const page = await ctx.newPage();
    await page.goto('http://localhost:3000/i18n-smoke', { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);
    const info = await page.evaluate(() => ({
      dir: document.documentElement.getAttribute('dir'),
      lang: document.documentElement.getAttribute('lang'),
      hello: document.querySelector('[data-test="smoke-hello"]')?.textContent?.trim(),
      loc: document.querySelector('[data-test="smoke-locale"]')?.textContent?.trim(),
    }));
    console.log(JSON.stringify({ code, ...info }));
    await page.screenshot({ path: `/tmp/smoke_${code}.png`, fullPage: true });
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
