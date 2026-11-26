/* Inspect the generated, script-free report using the existing browser test tools. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");
const { chromium } = createRequire(
  path.resolve(__dirname, "../../sourcelens/web/package.json"),
)("playwright");

(async () => {
  assert(process.argv[2], "Pass the generated semantic-review.html path");
  const report = path.resolve(process.argv[2]);
  const screenshotDir = path.resolve(
    process.env.WORKBENCH_SCREENSHOT_DIR ||
      path.join(__dirname, "../outputs/browser"),
  );
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.WORKBENCH_CHROME
      ? { executablePath: process.env.WORKBENCH_CHROME }
      : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  const remoteRequests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (/^https?:/.test(request.url())) remoteRequests.push(request.url());
  });
  try {
    await page.goto(pathToFileURL(report).href);
    assert.match(await page.locator("h1").innerText(), /Read the source/);
    assert.equal(await page.locator("section.reading").count(), 2);
    assert.match(
      await page.locator("#reading-1 .reading-head").innerText(),
      /witness found/,
    );
    assert.match(
      await page.locator("#reading-2 .reading-head").innerText(),
      /unsupported semantics/,
    );
    assert.match(
      await page.locator(".source-text").innerText(),
      /not asserted/,
    );
    assert.equal(await page.locator("script").count(), 0);
    await fs.mkdir(screenshotDir, { recursive: true });
    await page.screenshot({
      path: path.join(screenshotDir, "review-desktop.png"),
      fullPage: true,
    });
    await page
      .getByRole("link", { name: "quotation-boundary-control", exact: true })
      .click();
    assert.match(page.url(), /#reading-2$/);
    const details = page.locator("#reading-2 details").first();
    await details.locator("summary").click();
    assert.equal(await details.getAttribute("open"), "");
    assert.match(await details.innerText(), /Example fixture author/);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(pathToFileURL(report).href);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "No page-level mobile overflow",
    );
    const table = page.locator("#reading-1 .table-scroll").first();
    assert(
      await table.evaluate(
        (element) => element.scrollWidth > element.clientWidth,
      ),
      "Wide table stays inside a scroll container",
    );
    await table.evaluate((element) => {
      element.scrollLeft = 100;
    });
    assert(
      await table.evaluate((element) => element.scrollLeft > 0),
      "All table columns remain reachable",
    );
    await page.keyboard.press("Tab");
    assert.equal(
      await page.evaluate(() => document.activeElement.textContent),
      "Skip to readings",
    );
    await page.keyboard.press("Enter");
    assert.match(page.url(), /#readings$/);
    await page.goto(pathToFileURL(report).href);
    await page.screenshot({
      path: path.join(screenshotDir, "review-mobile.png"),
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    assert.deepEqual(remoteRequests, []);
    process.stdout.write(
      JSON.stringify({
        status: "passed",
        readings: 2,
        remoteRequests: 0,
        pageErrors: 0,
        desktop: "1440x1000",
        mobile: "390x844",
        screenshotDir,
      }) + "\n",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
