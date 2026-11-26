/* Force an older progress response to arrive after a newer human-pause response. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {createRequire} = require('node:module');
const {chromium} = createRequire(path.resolve(__dirname, '../../sourcelens/web/package.json'))('playwright');
(async () => {
  const base = process.env.REVIEW_TEST_URL;
  const browser = await chromium.launch({headless: true, executablePath: process.env.WORKBENCH_CHROME});
  const page = await browser.newPage();
  let count = 0, releaseStale, staleDone;
  const gate = new Promise(resolve => {releaseStale = resolve;});
  const delivered = new Promise(resolve => {staleDone = resolve;});
  try {
    await page.route(/\/api\/argument-runs\/[a-f0-9]+$/, async route => {
      if (route.request().method() !== 'GET') return route.continue();
      const index = ++count;
      const response = await route.fetch();
      const body = await response.json();
      if (index === 1) body.status = 'running'; // Ensure the progress stream is exercised.
      if (index === 2) {
        body.status = 'running'; // A valid older status, intentionally delayed.
        await gate;
      }
      await route.fulfill({response, json: body});
      if (index === 2) staleDone();
    });
    await page.goto(base);
    await page.getByLabel('Example or original passage', {exact: true}).selectOption('alarm-valid');
    await page.getByRole('button', {name: 'Use example', exact: true}).click();
    await page.getByRole('button', {name: /Run demo review/}).click();
    const heading = page.getByRole('heading', {name: 'Review the current interpretation', exact: true});
    await heading.waitFor();
    assert(count >= 3, 'Concurrent status responses were exercised');
    releaseStale();
    await delivered;
    await page.waitForTimeout(250); // Allow the fulfilled fetch and React render to settle.
    assert(await heading.isVisible(), 'A delayed running response must not replace the newer human-pause view');
    console.log(JSON.stringify({status: 'passed', overlapping_gets: count, stale_response_ignored: true, model_calls: 0}));
  } finally {releaseStale(); await browser.close();}
})().catch(error => {console.error(error); process.exit(1);});
