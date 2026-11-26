/* End-to-end delivery check against the independent zipapp server. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { chromium } = createRequire(
  path.resolve(__dirname, "../../sourcelens/web/package.json"),
)("playwright");

(async () => {
  const base = process.env.WORKBENCH_TEST_URL || "http://127.0.0.1:18766";
  assert(["127.0.0.1", "localhost"].includes(new URL(base).hostname));
  const shots = path.resolve(
    process.env.WORKBENCH_SCREENSHOT_DIR ||
      path.join(__dirname, "../outputs/prototype-browser"),
  );
  const artifacts = path.resolve(__dirname, "../outputs/prototype-browser");
  await fs.mkdir(shots, { recursive: true });
  await fs.mkdir(artifacts, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.WORKBENCH_CHROME
      ? { executablePath: process.env.WORKBENCH_CHROME }
      : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
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
  async function example(name) {
    await page
      .getByLabel("Example", { exact: true })
      .selectOption({ label: name });
    await page
      .getByRole("button", { name: "Open example as draft", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector("#review-actions button") &&
        !document.querySelector("#review-actions button").disabled,
    );
  }
  async function reviews() {
    await page.getByRole("button", { name: "Review", exact: true }).click();
    await page
      .getByLabel("Reviewer", { exact: true })
      .fill("Prototype fixture reviewer");
    await page
      .getByLabel("Rationale", { exact: true })
      .fill(
        "Self-review of an authored test control, not a classic paper or expert certification.",
      );
    let count = 0;
    for (const aspect of ["fidelity", "attribution", "interpretation"]) {
      await page
        .getByRole("button", {
          name: "Record " + aspect + " review",
          exact: true,
        })
        .click();
      count++;
      await page.waitForFunction(
        (n) => document.querySelectorAll("#review-badges .green").length === n,
        count,
      );
    }
  }
  async function run(outcome) {
    await page.getByRole("button", { name: "Run check", exact: true }).click();
    try {
      await page.waitForFunction(
        (wanted) =>
          Array.from(document.querySelectorAll(".result-card>.badge")).some(
            (el) => el.textContent === wanted.replaceAll("_", " "),
          ),
        outcome,
      );
    } catch (error) {
      throw new Error(
        "Expected " +
          outcome +
          "; page status: " +
          (await page.locator("#message").innerText()),
      );
    }
    await page.waitForFunction(
      () => !document.querySelector("#analyze").disabled,
    );
  }
  try {
    await page.goto(base);
    await page
      .getByRole("heading", { name: "Make the reasoning visible." })
      .waitFor();
    await example("invalid_control");
    await reviews();
    await run("witness_found");
    assert.match(await page.locator("pre.witness").innerText(), /"p": false/);
    assert.match(await page.locator("#asts").innerText(), /implies/);
    assert.match(await page.locator("#results-content").innerText(), /dpll-v1/);
    await page
      .getByRole("button", { name: "Save snapshot", exact: true })
      .click();
    await page.waitForFunction(() =>
      document.querySelector("#head").textContent.startsWith("Head: "),
    );
    const firstHead = (await page.locator("#head").innerText()).slice(6);
    await page
      .getByRole("button", { name: "Replay result", exact: true })
      .click();
    await page
      .getByRole("status")
      .filter({ hasText: "Result replay verified" })
      .waitFor();
    const resultDownload = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export result JSON", exact: true })
      .click();
    await (
      await resultDownload
    ).saveAs(path.join(artifacts, "result-bundle.json"));
    const htmlDownload = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export review HTML", exact: true })
      .click();
    await (await htmlDownload).saveAs(path.join(artifacts, "review.html"));
    await page.screenshot({
      path: path.join(shots, "prototype-desktop.png"),
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "Reconstruct", exact: true })
      .click();
    await page.getByLabel("Formula P2", { exact: true }).fill("p");
    await page.getByLabel("Formula P2", { exact: true }).press("Tab");
    await page.waitForFunction(
      () => document.querySelectorAll("#review-badges .green").length === 0,
    );
    await run("review_required");
    await reviews();
    await run("no_counterexample_complete");
    await page
      .getByRole("button", { name: "Save snapshot", exact: true })
      .click();
    await page.waitForFunction(
      (old) => document.querySelector("#head").textContent !== "Head: " + old,
      firstHead,
    );
    const old = await (
      await page.request.get(base + "/api/history/" + firstHead)
    ).json();
    assert.equal(old.document.readings[0].nodes[1].formula, "q");
    await page.reload();
    await page
      .getByLabel("Saved snapshot", { exact: true })
      .selectOption({ label: "affirming-consequent-control" });
    await page
      .getByRole("button", { name: "Open saved head", exact: true })
      .click();
    await page.waitForFunction(
      () => document.querySelector('[aria-label="Formula P2"]').value === "p",
    );
    await example("propagation-chain-32");
    await reviews();
    await run("no_counterexample_complete");
    const decisions = page
      .locator(".metrics>div")
      .filter({ has: page.getByText("decisions", { exact: true }) });
    assert.equal(await decisions.locator("strong").innerText(), "0");
    await page
      .getByLabel("Formal backend", { exact: true })
      .selectOption("exhaustive");
    await run("budget_exhausted");
    await example("semantic_readings");
    await page
      .getByLabel("Selected reading", { exact: true })
      .selectOption({ label: "quotation-boundary-control" });
    await reviews();
    await page
      .getByLabel("Formal backend", { exact: true })
      .selectOption("dpll");
    await run("unsupported_semantics");
    await example("invalid_control");
    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "No mobile page overflow",
    );
    const nodeEditor = page.getByRole("region", {
      name: "Node editor",
      exact: true,
    });
    assert(
      await nodeEditor.evaluate((el) => el.scrollWidth > el.clientWidth),
      "Wide editor scrolls locally",
    );
    await nodeEditor.evaluate((el) => {
      el.scrollLeft = 100;
    });
    assert(await nodeEditor.evaluate((el) => el.scrollLeft > 0));
    await nodeEditor.evaluate((el) => {
      el.scrollLeft = 0;
    });
    await page.screenshot({
      path: path.join(shots, "prototype-mobile.png"),
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    process.stdout.write(
      JSON.stringify({
        status: "passed",
        flows: [
          "edit",
          "three-review-aspects",
          "dpll",
          "ast",
          "witness",
          "save",
          "reload",
          "stale-review",
          "old-snapshot",
          "replay",
          "json-export",
          "html-export",
          "32-atom-propagation",
          "exhaustive-unknown",
          "quotation-boundary",
          "mobile-scroll",
        ],
        pageErrors: 0,
        externalRequests: 0,
      }) + "\n",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
