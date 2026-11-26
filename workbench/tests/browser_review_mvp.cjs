/* Isolated Chrome against the actual packaged FastAPI/LangGraph/KV MVP. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { chromium } = createRequire(
  path.resolve(__dirname, "../../sourcelens/web/package.json"),
)("playwright");

(async () => {
  const base = process.env.REVIEW_TEST_URL,
    shots = process.env.REVIEW_SCREENSHOTS,
    artifacts = process.env.REVIEW_ARTIFACTS;
  assert.equal(new URL(base).hostname, "127.0.0.1");
  await fs.mkdir(shots, { recursive: true });
  await fs.mkdir(artifacts, { recursive: true });
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
  const readRun = async (id) =>
    (await page.request.get(base + "/api/argument-runs/" + id)).json();
  const awaitReview = async () => {
    await page
      .getByRole("heading", {
        name: "Review the current interpretation",
        exact: true,
      })
      .waitFor();
    await page.waitForFunction(
      () =>
        document.querySelector(".human-review") &&
        document.querySelector(".human-review select")?.value === "",
    );
  };
  const start = async (example) => {
    await page.getByRole("button", { name: /New argument review/ }).click();
    await page.getByText("Try an example", { exact: true }).click();
    await page
      .getByLabel("Example or original passage", { exact: true })
      .selectOption(example);
    await page
      .getByRole("button", { name: "Use example", exact: true })
      .click();
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/argument-runs") &&
        r.request().method() === "POST",
    );
    // This retained scenario explicitly exercises the optional critique loop.
    if (await page.getByLabel("Generation workflow", { exact: true }).count())
      await page
        .getByLabel("Generation workflow", { exact: true })
        .selectOption("bounded_review");
    await page.getByRole("button", { name: /Run demo review/ }).click();
    const created = await response;
    assert.equal(created.status(), 202, await created.text());
    const id = (await created.json()).id;
    await awaitReview();
    return id;
  };
  const judgments = async ({ approve = true, dispute = false } = {}) => {
    await page
      .getByLabel("Faithful to the passage", { exact: true })
      .selectOption("accepted");
    await page
      .getByLabel("Correct attribution", { exact: true })
      .selectOption(dispute ? "disputed" : "accepted");
    await page
      .getByLabel("Defensible interpretation", { exact: true })
      .selectOption("accepted");
    await page
      .getByLabel("Reviewer label", { exact: true })
      .fill("MVP authored-control reviewer");
    await page
      .getByLabel("Reason for these judgments", { exact: true })
      .fill(
        "Self-review of the authored demo and displayed mapping; no expert certification.",
      );
    await page
      .getByLabel("Approve the displayed formal mapping", {
        exact: true,
      })
      .setChecked(approve);
  };
  const finish = async (id, expected, opts) => {
    await judgments(opts);
    await page
      .getByRole("button", { name: "Record review & finish", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Open report ↗", exact: true })
      .waitFor();
    const done = await readRun(id);
    assert.equal(done.status, "succeeded", JSON.stringify(done));
    assert.equal(done.report.formal_checks[0].outcome, expected);
    assert.equal(done.report.provider.live_model, false);
    return done;
  };
  try {
    await page.goto(base);
    await page.getByRole("button", { name: /Run demo review/ }).waitFor();
    await page.getByText("Example mode", { exact: true }).waitFor();
    assert.match(
      await page.locator(".mode-banner").innerText(),
      /Example mode/,
    );
    await page.screenshot({
      path: path.join(shots, "mvp-start-desktop.png"),
      fullPage: true,
    });
    let firstId, done;
    if (process.env.REVIEW_RESTART_RUN) {
      firstId = process.env.REVIEW_RESTART_RUN;
      const before = await readRun(firstId);
      assert.equal(before.status, "awaiting_review");
      await page
        .locator(".run-list button")
        .filter({ hasText: before.request.question })
        .first()
        .click();
      await awaitReview();
      done = await finish(firstId, "no_counterexample_complete");
      assert.equal(
        done.events.filter((e) => e.node === "model_call").length,
        2,
      );
    } else {
      firstId = await start("alarm-gap");
      let value = await readRun(firstId);
      assert.equal(value.revision, 1);
      assert.equal(
        value.draft.readings[0].nodes.find((n) => n.id === "A1").commitment,
        "proposed_assumption",
      );
      assert(
        value.events.some((e) => e.node === "kv_tool_result" && e.worker_pid),
      );
      assert(
        await page.getByText("Revision recorded", { exact: true }).count(),
      );
      await page
        .getByText("Formula mapping and complete draft JSON", { exact: true })
        .click();
      await page.evaluate(() => window.scrollTo(0, 0));
      const editor = page.getByLabel("Advanced draft JSON", { exact: true });
      const original = await editor.inputValue();
      await editor.fill("{}");
      assert(
        await page
          .getByRole("button", { name: "Record review & finish", exact: true })
          .isDisabled(),
      );
      await page
        .getByRole("button", {
          name: "Apply validated JSON changes",
          exact: true,
        })
        .click();
      await page.getByRole("alert").waitFor();
      assert.equal(errors.length, 0);
      await editor.fill(original);
      await page
        .getByRole("button", {
          name: "Apply validated JSON changes",
          exact: true,
        })
        .click();
      await page.waitForFunction(
        () =>
          Array.from(document.querySelectorAll("button")).find(
            (button) => button.textContent === "Apply validated JSON changes",
          )?.disabled === true,
      );
      await page.locator(".node-details > summary").click();
      await page
        .getByLabel("Speaker P1", { exact: true })
        .fill("Human-reviewed source voice");
      await page
        .getByText("Formula mapping and complete draft JSON", { exact: true })
        .click();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: path.join(shots, "mvp-review-desktop.png"),
        fullPage: true,
      });
      await page.screenshot({
        path: path.join(shots, "review-overview.png"),
        fullPage: false,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => window.scrollTo(0, 0));
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      );
      await page.screenshot({
        path: path.join(shots, "mvp-review-mobile.png"),
        fullPage: true,
      });
      await page.setViewportSize({ width: 1440, height: 1100 });
      done = await finish(firstId, "witness_found");
      assert.deepEqual(done.report.formal_checks[0].assignment, {
        p: false,
        q: true,
      });
      assert.notEqual(done.draft_ref, value.draft_ref);
    }
    await page
      .getByRole("button", { name: "Open report ↗", exact: true })
      .click();
    await page
      .getByRole("heading", {
        name: "Formal checks and review",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("heading", { name: "Argument analysis", exact: true })
      .waitFor();
    assert((await page.locator(".report-node").count()) >= 3);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(shots, "mvp-report-desktop.png"),
      fullPage: true,
    });
    let pending = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export report JSON", exact: true })
      .click();
    const jsonDownload = await pending;
    const jsonFile = path.join(artifacts, "argument-review.json");
    await jsonDownload.saveAs(jsonFile);
    const packet = JSON.parse(await fs.readFile(jsonFile, "utf8"));
    assert.equal(packet.report.provider.live_model, false);
    await page
      .getByLabel("Verify exported packet", { exact: true })
      .setInputFiles(jsonFile);
    await page
      .getByRole("alert")
      .filter({ hasText: "snapshot_verified" })
      .waitFor();
    pending = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export review HTML", exact: true })
      .click();
    const htmlDownload = await pending;
    const htmlFile = path.join(artifacts, "argument-review.html");
    await htmlDownload.saveAs(htmlFile);
    const html = await fs.readFile(htmlFile, "utf8");
    assert(html.includes("DEMO"));
    assert(!/<script\b/i.test(html));
    if (!process.env.REVIEW_RESTART_RUN) {
      for (const [name, outcome] of [
        ["alarm-valid", "no_counterexample_complete"],
        ["perspective", "unsupported_semantics"],
        ["knowledge", "unsupported_semantics"],
        ["attribution", "unsupported_semantics"],
      ]) {
        const id = await start(name);
        await finish(id, outcome);
      }
      const pausedId = await start("alarm-valid");
      assert.equal(
        (await readRun(pausedId)).events.filter((e) => e.node === "model_call")
          .length,
        2,
      );
      await page.reload();
      await page.getByRole("button", { name: /Run demo review/ }).waitFor();
      const saved = (
        await page.request.get(base + "/api/argument-runs")
      ).json();
      assert((await saved).some((r) => r.id === pausedId));
      await page.setViewportSize({ width: 390, height: 844 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      );
      await page.screenshot({
        path: path.join(shots, "mvp-start-mobile.png"),
        fullPage: true,
      });
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      console.log(
        JSON.stringify({
          status: "passed",
          paused_run: pausedId,
          flows: [
            "demo-label",
            "source-question-entry",
            "actual-kv-tool-child",
            "conditional-revision",
            "invalid-json-safe",
            "validated-edit",
            "three-human-judgments",
            "content-change-binding",
            "verified-countermodel",
            "json-export-and-browser-reverify",
            "script-free-html",
            "five-authored-controls",
            "saved-runs",
            "desktop-mobile-no-overflow",
          ],
          page_errors: errors,
          external_requests: external,
          browser: browser.version(),
        }),
      );
    } else {
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      console.log(
        JSON.stringify({
          status: "passed",
          flows: [
            "actual-process-restart",
            "restored-human-interrupt",
            "no-repeated-demo-calls",
            "approved-valid-inference",
            "portable-json-replay",
            "html-export",
          ],
          page_errors: errors,
          external_requests: external,
          browser: browser.version(),
        }),
      );
    }
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
