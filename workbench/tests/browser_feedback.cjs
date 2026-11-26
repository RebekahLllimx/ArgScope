/* Real UI/API/KV, deterministic provider and synthetic reviewer only. */
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
  const read = async (id) =>
    (await page.request.get(`${base}/api/argument-runs/${id}`)).json();
  try {
    await page.goto(base);
    if (process.env.FEEDBACK_RESTART_ID) {
      const id = process.env.FEEDBACK_RESTART_ID;
      await page.locator(".run-list button").first().click();
      await page
        .getByRole("heading", {
          name: "Review the current interpretation",
          exact: true,
        })
        .waitFor();
      const state = await read(id);
      assert.equal(state.feedback_round, 1);
      assert.equal(state.versions.length, 3);
      assert.equal(
        state.events.filter((e) => e.node === "feedback_model_call").length,
        1,
      );
      await page
        .getByText("Saved versions and comparison (3)", { exact: true })
        .click();
      await page
        .getByLabel("Compare from", { exact: true })
        .selectOption(state.versions[0].ref);
      await page
        .getByLabel("Compare to", { exact: true })
        .selectOption(state.versions[2].ref);
      await page.locator(".version-change").first().waitFor();
      for (const label of [
        "Faithful to the passage",
        "Correct attribution",
        "Defensible interpretation",
      ])
        await page.getByLabel(label, { exact: true }).selectOption("accepted");
      await page
        .getByLabel("Reviewer label", { exact: true })
        .fill("Synthetic feedback browser test");
      await page
        .getByLabel("Reason for these judgments", { exact: true })
        .fill("Authored fixture only; no actual interpretation approval.");
      await page
        .getByLabel("Approve the displayed formal mapping", { exact: true })
        .check();
      await page
        .getByRole("button", { name: "Record review & finish", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Open report ↗", exact: true })
        .waitFor();
      const report = await (
        await page.request.get(`${base}/api/argument-runs/${id}/report.json`)
      ).json();
      assert.equal(report.report.saved_versions.length, 3);
      assert.equal(
        report.report.revision_feedback.reviewer,
        "Synthetic feedback browser test",
      );
      assert.equal(
        (
          await page.request.post(`${base}/api/argument-verify`, {
            data: report,
          })
        ).status(),
        200,
      );
      await fs.writeFile(
        path.join(out, "feedback-report.json"),
        JSON.stringify(report, null, 2),
      );
      console.log(
        JSON.stringify({
          restart: true,
          retained_versions: 3,
          repeated_feedback_calls: 0,
          final_export_replay: true,
          page_errors: errors,
          external_requests: external,
        }),
      );
    } else {
      await page.getByText("Try an example", { exact: true }).click();
      await page
        .getByLabel("Example or original passage", { exact: true })
        .selectOption("alarm-valid");
      await page
        .getByRole("button", { name: "Use example", exact: true })
        .click();
      const creating = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/argument-runs") &&
          r.request().method() === "POST",
      );
      await page.getByRole("button", { name: /Run demo review/ }).click();
      const id = (await (await creating).json()).id;
      await page
        .getByRole("heading", {
          name: "Review the current interpretation",
          exact: true,
        })
        .waitFor();
      const initial = await read(id);
      for (const label of [
        "Faithful to the passage",
        "Correct attribution",
        "Defensible interpretation",
      ])
        await page.getByLabel(label, { exact: true }).selectOption("accepted");
      await page
        .getByLabel("Approve the displayed formal mapping", { exact: true })
        .check();
      await page
        .getByText("Formula mapping and complete draft JSON", { exact: true })
        .click();
      const edited = structuredClone(initial.draft);
      edited.summary = "Reader changed this before requesting revision.";
      await page
        .getByLabel("Advanced draft JSON", { exact: true })
        .fill(JSON.stringify(edited));
      await page
        .getByRole("button", {
          name: "Apply validated JSON changes",
          exact: true,
        })
        .click();
      await page.waitForFunction(
        () =>
          document.querySelector('select[aria-label="Correct attribution"]')
            .value === "",
      );
      assert.equal(
        await page
          .getByLabel("Approve the displayed formal mapping", { exact: true })
          .isChecked(),
        false,
      );
      assert.equal(
        await page
          .getByRole("heading", {
            name: "Candidate formal checks · awaiting your review",
            exact: true,
          })
          .count(),
        0,
      );
      await page
        .getByLabel("Feedback reviewer", { exact: true })
        .fill("Synthetic feedback browser test");
      await page
        .getByLabel("Feedback for revision", { exact: true })
        .fill("Inspect the source attribution without inventing a premise.");
      const sending = page.waitForResponse((r) =>
        r.url().endsWith("/feedback"),
      );
      await page
        .getByRole("button", {
          name: "Revise once with my feedback",
          exact: true,
        })
        .click();
      assert.equal((await sending).status(), 202);
      await page
        .getByText("Saved versions and comparison (3)", { exact: true })
        .waitFor();
      await page
        .getByRole("heading", {
          name: "Review the current interpretation",
          exact: true,
        })
        .waitFor();
      const state = await read(id);
      assert.equal(state.feedback_round, 1);
      assert.equal(state.report, null);
      assert.equal(
        state.events.filter((e) => e.node === "human_review").length,
        0,
      );
      assert.equal(
        await page
          .getByRole("button", {
            name: "Revise once with my feedback",
            exact: true,
          })
          .count(),
        0,
      );
      assert.equal(
        await page
          .getByLabel("Correct attribution", { exact: true })
          .inputValue(),
        "",
      );
      await page
        .getByText("Saved versions and comparison (3)", { exact: true })
        .click();
      await page.locator(".version-change").first().waitFor();
      await page.locator(".version-history").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, "versions-desktop.png") });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator(".version-history").scrollIntoViewIfNeeded();
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 2,
        ),
      );
      await page.screenshot({ path: path.join(out, "versions-mobile.png") });
      console.log(
        JSON.stringify({
          run_id: id,
          flow: "edit-feedback-revision-pause-compare",
          versions: state.versions.length,
          approval_reset: true,
          unsaved_preview_hidden: true,
          one_feedback_attempt: true,
          model_calls: 0,
          page_errors: errors,
          external_requests: external,
        }),
      );
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
