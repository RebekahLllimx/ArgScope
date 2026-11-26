/* Packaged SourceLens UI/API: authored examples only, no inference. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { chromium } = createRequire(
  path.resolve(__dirname, "../../sourcelens/web/package.json"),
)("playwright");
(async () => {
  const base = process.env.REVIEW_TEST_URL,
    out = process.env.REVIEW_ARTIFACTS;
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.WORKBENCH_CHROME,
  });
  const page = await browser.newPage({
    viewport: { width: 1360, height: 950 },
  });
  const errors = [],
    external = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (
      /^https?:/.test(r.url()) &&
      new URL(r.url()).origin !== new URL(base).origin
    )
      external.push(r.url());
  });
  try {
    await page.goto(base + "/library");
    await page.getByRole("heading", { name: "Search your sources" }).waitFor();
    await page
      .getByRole("button", { name: "Load example library", exact: true })
      .click();
    await page.locator(".document").first().waitFor();
    const docs = await (await page.request.get(base + "/api/documents")).json();
    assert.equal(docs.length, 3);
    await page
      .getByLabel("Research question", { exact: true })
      .fill(
        "How do write-ahead logs and checkpoints handle recovery after a crash?",
      );
    await page
      .getByRole("button", { name: "Start research", exact: true })
      .click();
    await page.locator(".run .status.succeeded").waitFor({ timeout: 20000 });
    assert((await page.locator(".quote-card").count()) > 0);
    await page.getByRole("button", { name: "C1", exact: true }).first().click();
    await page.getByLabel("Source passage", { exact: true }).waitFor();
    assert(await page.locator(".source-body").innerText());
    await page
      .getByLabel("Source passage", { exact: true })
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await page.screenshot({
      path: path.join(out, "library-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(out, "library-mobile.png"),
      fullPage: true,
    });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 2,
      ),
    );
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log(
      JSON.stringify({
        status: "passed",
        example_documents: 3,
        source_research: true,
        citation_resolution: true,
        desktop_mobile: true,
        page_errors: errors,
        external_requests: external,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
