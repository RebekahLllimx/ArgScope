/* Synthetic authored-paper acceptance tests; never records real user judgments. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { chromium } = createRequire(
  path.resolve(__dirname, "../../sourcelens/web/package.json"),
)("playwright");
(async () => {
  const base = process.env.REVIEW_TEST_URL,
    out = process.env.REVIEW_ARTIFACTS,
    shots = process.env.REVIEW_SCREENSHOTS;
  await fs.mkdir(out, { recursive: true });
  await fs.mkdir(shots, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.WORKBENCH_CHROME,
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
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
    (await page.request.get(base + "/api/argument-runs/" + id)).json();
  async function start(id) {
    await page.getByRole("button", { name: /New argument review/ }).click();
    await page.getByText("Try an example", { exact: true }).click();
    await page
      .getByLabel("Example or original passage", { exact: true })
      .selectOption(id);
    await page
      .getByRole("button", { name: "Use example", exact: true })
      .click();
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/argument-runs") &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: /Run demo review/ }).click();
    const created = await response;
    assert.equal(created.status(), 202);
    const run = (await created.json()).id;
    await page
      .getByRole("heading", {
        name: "Review the current interpretation",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("heading", {
        name: "Candidate formal checks · awaiting your review",
        exact: true,
      })
      .waitFor();
    return run;
  }
  async function finish(id) {
    for (const label of [
      "Faithful to the passage",
      "Correct attribution",
      "Defensible interpretation",
    ])
      await page.getByLabel(label, { exact: true }).selectOption("accepted");
    await page
      .getByLabel("Reviewer label", { exact: true })
      .fill("AUTOMATED authored-case fixture; not a real user judgment");
    await page
      .getByLabel("Reason for these judgments", { exact: true })
      .fill(
        "Synthetic browser acceptance fixture only; no semantic quality or expert approval claim.",
      );
    await page
      .getByLabel("Approve the displayed formal mapping", { exact: true })
      .check();
    await page
      .getByRole("button", { name: "Record review & finish", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Open report ↗", exact: true })
      .waitFor();
    return read(id);
  }
  try {
    await page.goto(base);
    await page.getByRole("button", { name: /Run demo review/ }).waitFor();
    await page.getByText("Example mode", { exact: true }).waitFor();
    assert.match(
      await page.locator(".mode-banner").innerText(),
      /Example mode/,
    );
    const quine = await start("quine-description-first-order");
    let state = await read(quine);
    assert.equal(
      state.candidate_checks[0].semantics,
      "classical-first-order-v1",
    );
    assert.equal(
      state.candidate_checks[0].outcome,
      "no_counterexample_complete",
    );
    assert.equal(state.candidate_checks[0].human_approved, false);
    const originalFormula = state.draft.readings[0].formal_mapping.formulas.C;
    assert.equal(
      await page
        .locator(".selected-formula .formula")
        .getAttribute("data-formula"),
      originalFormula,
    );
    assert.equal(await page.locator(".selected-formula math").count(), 1);
    assert.equal(await page.locator(".graph-card-body math").count(), 2);
    assert.match(
      await page.locator(".selected-formula annotation").textContent(),
      /\\forall/,
    );
    await page.evaluate(() => document.fonts.ready);
    assert(
      await page.evaluate(() => document.fonts.check("18px KaTeX_Main")),
      "Bundled math fonts must load",
    );
    await page
      .getByRole("button", { name: "Select graph node P1", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    assert.equal(
      await page
        .locator(".selected-formula .formula")
        .getAttribute("data-formula"),
      state.draft.readings[0].formal_mapping.formulas.P1,
    );
    await page
      .getByRole("button", { name: "Select graph node C", exact: true })
      .click();
    for (const [width, height, label] of [
      [1440, 1100, "desktop"],
      [390, 844, "mobile"],
      [320, 760, "narrow"],
    ]) {
      await page.setViewportSize({ width, height });
      await page.evaluate(() => {
        document.activeElement?.blur?.();
        window.scrollTo(0, 0);
      });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      await page.screenshot({
        path: path.join(shots, "quine-fol-" + label + ".png"),
        fullPage: label === "desktop",
      });
      if (label === "desktop")
        await page.screenshot({ path: path.join(shots, "quine-overview.png") });
      if (label === "mobile") {
        await page
          .getByRole("heading", {
            name: "Candidate formal checks · awaiting your review",
            exact: true,
          })
          .scrollIntoViewIfNeeded();
        assert(
          await page
            .getByRole("heading", {
              name: "Candidate formal checks · awaiting your review",
              exact: true,
            })
            .isVisible(),
        );
        await page.screenshot({
          path: path.join(shots, "quine-fol-mobile-checks.png"),
          fullPage: false,
        });
      }
      if (label !== "desktop") {
        await page.locator(".selected-formula").scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(shots, "quine-formula-" + label + ".png"),
        });
      }
    }
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page
      .getByText("Formula mapping and complete draft JSON", { exact: true })
      .click();
    const edit = structuredClone(state.draft);
    edit.readings[0].formal_mapping.formulas.C = "forall x. ~W(x)";
    await page
      .getByLabel("Advanced draft JSON", { exact: true })
      .fill(JSON.stringify(edit));
    await page
      .getByRole("button", {
        name: "Apply validated JSON changes",
        exact: true,
      })
      .click();
    await page.waitForFunction(
      () =>
        !document.body.textContent.includes("Apply valid JSON changes before"),
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
    state = await finish(quine);
    assert.equal(state.status, "succeeded");
    assert.deepEqual(state.candidate_checks, []);
    assert.equal(state.report.formal_checks[0].outcome, "witness_found");
    assert.equal(state.report.formal_checks[0].finite_model.domain_size, 2);
    assert.equal(
      state.report.formal_checks[0].validation.validator,
      "first-order-original-ast-v1",
    );
    await page
      .getByRole("button", { name: "Open report ↗", exact: true })
      .click();
    await page
      .getByRole("heading", {
        name: "Independently checked finite countermodel",
      })
      .waitFor();
    const packet = await (
      await page.request.get(
        base + "/api/argument-runs/" + quine + "/report.json",
      )
    ).json();
    const replay = await page.request.post(base + "/api/argument-verify", {
      data: packet,
    });
    assert.equal(replay.status(), 200);
    assert.equal((await replay.json()).status, "snapshot_verified");
    const html = await (
      await page.request.get(
        base + "/api/argument-runs/" + quine + "/report.html",
      )
    ).text();
    assert(!html.includes("<script"));
    assert(html.includes("first-order-original-ast-v1"));
    await fs.writeFile(
      path.join(out, "quine-test-packet.json"),
      JSON.stringify(packet, null, 2),
    );
    await fs.writeFile(path.join(out, "quine-test-report.html"), html);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(shots, "quine-countermodel-report.png"),
      fullPage: true,
    });
    const chalmers = await start("chalmers-singularity-original");
    state = await finish(chalmers);
    assert.equal(
      state.report.formal_checks[0].outcome,
      "no_counterexample_complete",
    );
    const cpacket = await (
      await page.request.get(
        base + "/api/argument-runs/" + chalmers + "/report.json",
      )
    ).json();
    await fs.writeFile(
      path.join(out, "chalmers-test-packet.json"),
      JSON.stringify(cpacket, null, 2),
    );
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log(
      JSON.stringify({
        status: "passed",
        flows: [
          "authored-paper-label",
          "first-order-candidate-preview",
          "desktop-mobile-layout",
          "edit-hides-stale-preview",
          "human-fixture-approval",
          "first-order-two-object-countermodel",
          "original-ast-recheck",
          "browser-json-replay",
          "script-free-html",
          "chalmers-complete-report",
        ],
        synthetic_reviewers_only: true,
        page_errors: errors,
        external_requests: external,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
