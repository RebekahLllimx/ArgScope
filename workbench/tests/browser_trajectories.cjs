/* Actual packaged reader -> loopback HTTP -> existing RESP KV. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { chromium } = createRequire(
  path.resolve(__dirname, "../../sourcelens/web/package.json"),
)("playwright");

(async () => {
  const base = process.env.WORKBENCH_TEST_URL;
  const report = process.env.WORKBENCH_TRAJECTORY_REPORT;
  const shots = process.env.WORKBENCH_SCREENSHOT_DIR;
  assert.equal(new URL(base).hostname, "127.0.0.1");
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.WORKBENCH_CHROME,
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
    acceptDownloads: true,
  });
  const errors = [],
    external = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      /^https?:/.test(request.url()) &&
      new URL(request.url()).origin !== new URL(base).origin
    )
      external.push(request.url());
  });
  const read = async (window) => {
    await page.locator("#trajectory-window").fill(String(window));
    await page.locator("#trajectory-measure").click();
    await page.waitForFunction((w) => {
      const value = document.querySelector("#trajectory-result pre");
      return (
        value && JSON.parse(value.textContent).measurement.final_window === w
      );
    }, window);
    return JSON.parse(
      await page.locator("#trajectory-result pre").textContent(),
    );
  };
  try {
    await page.goto(base);
    await page.getByText("Configured store: kv.", { exact: false }).waitFor();
    await page.locator("#trajectory-report").setInputFiles(report);
    await page.waitForFunction(
      () => document.querySelector("#trajectory-control").value.length === 64,
    );
    const first = await read(15);
    assert.equal(first.backend, "kv");
    const second = await read(5);
    assert.equal(first.measurement.control_key, second.measurement.control_key);
    assert.equal(first.measurement.treated_key, second.measurement.treated_key);
    assert.deepEqual(
      first.measurement.control_series,
      second.measurement.control_series,
    );
    const manual =
      second.measurement.control_series.slice(-5).reduce((a, b) => a + b, 0) /
      5;
    assert.equal(second.measurement.control_mean, manual);
    assert.match(second.scope, /Exploratory/);
    await page
      .getByRole("img", { name: /Control and intervention cooperation/ })
      .waitFor();
    await page
      .locator(".trajectory-reader")
      .screenshot({ path: path.join(shots, "trajectory-desktop.png") });
    const pending = page.waitForEvent("download");
    await page.locator("#trajectory-export").click();
    const download = await pending;
    assert.equal(download.suggestedFilename(), "exploratory-measurement.json");
    const exported = JSON.parse(
      await fs.readFile(await download.path(), "utf8"),
    );
    assert.deepEqual(exported, second);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator(".trajectory-reader").scrollIntoViewIfNeeded();
    await page.waitForFunction(
      () => document.querySelector("#trajectory-result canvas").width < 400,
    );
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    );
    await page
      .locator(".trajectory-reader")
      .screenshot({ path: path.join(shots, "trajectory-mobile.png") });
    await page.locator("#trajectory-window").fill("1000");
    assert(await page.locator("#trajectory-export").isDisabled());
    await page.locator("#trajectory-measure").click();
    await page.waitForFunction(() =>
      document.querySelector("#message").textContent.includes("entirely after"),
    );
    assert.equal(await page.locator("#trajectory-result canvas").count(), 0);
    await page.locator("#trajectory-window").fill("5");
    await page.locator("#trajectory-control").fill("f".repeat(64));
    await page.locator("#trajectory-measure").click();
    await page.waitForFunction(() =>
      document.querySelector("#message").textContent.includes("missing"),
    );
    assert.equal(await page.locator("#trajectory-result canvas").count(), 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log(
      JSON.stringify({
        status: "passed",
        browser: browser.version(),
        flows: [
          "configured-kv",
          "experiment-report-import",
          "stored-pair",
          "changed-window-same-trajectories",
          "manual-arithmetic",
          "plot",
          "exploratory-export",
          "invalid-window-rejected",
          "missing-record-rejected",
          "desktop-mobile-no-overflow",
        ],
        page_errors: errors,
        external_requests: external,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
