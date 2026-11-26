/* Responsive layout and progressive disclosure against an offline application. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { chromium } = createRequire(
  path.resolve(__dirname, "../../sourcelens/web/package.json"),
)("playwright");

(async () => {
  const base = process.env.REVIEW_TEST_URL;
  const out = process.env.REVIEW_ARTIFACTS;
  assert.equal(new URL(base).hostname, "127.0.0.1");
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.WORKBENCH_CHROME,
  });
  const page = await browser.newPage();
  let createdRuns = 0;
  const errors = [],
    external = [],
    layouts = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/argument-runs"
    )
      createdRuns++;
    if (
      /^https?:/.test(request.url()) &&
      new URL(request.url()).origin !== new URL(base).origin
    )
      external.push(request.url());
  });
  const viewports = [
    { width: 1440, height: 1050 },
    { width: 390, height: 844 },
    { width: 320, height: 760 },
  ];
  async function inspect(name, viewport) {
    const layout = await page.evaluate(() => {
      const visible = (element) => element.getClientRects().length > 0;
      const smallText = [
        ...document.querySelectorAll("p, small, label, summary"),
      ]
        .filter(visible)
        .filter(
          (element) =>
            Number.parseFloat(getComputedStyle(element).fontSize) < 13,
        )
        .map((element) => element.textContent.slice(0, 80));
      return {
        width: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        smallText,
      };
    });
    assert(
      layout.documentWidth <= viewport.width + 2,
      name + ": page overflows",
    );
    assert.deepEqual(
      layout.smallText,
      [],
      name + ": unreadable supporting text",
    );
    await page.evaluate(
      () =>
        document.activeElement instanceof HTMLElement &&
        document.activeElement.blur(),
    );
    await page.screenshot({
      path: path.join(out, `${name}-${viewport.width}.png`),
    });
    layouts.push({ name, ...layout });
  }
  try {
    let releaseExamples;
    const exampleGate = new Promise((resolve) => {
      releaseExamples = resolve;
    });
    await page.route("**/api/argument-examples", async (route) => {
      await exampleGate;
      await route.continue();
    });
    await page.goto(base);
    await page
      .getByLabel("Source title", { exact: true })
      .fill("My own passage");
    releaseExamples();
    await page.getByText("Example mode", { exact: true }).waitFor();
    assert.equal(
      await page.getByLabel("Source title", { exact: true }).inputValue(),
      "My own passage",
      "Startup metadata must not overwrite user input",
    );
    await page.unroute("**/api/argument-examples");

    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.goto(base);
      await page.getByLabel("Selected passage", { exact: true }).waitFor();
      await page.getByText("Example mode", { exact: true }).waitFor();
      assert.equal(
        await page
          .getByLabel("Example or original passage", { exact: true })
          .inputValue(),
        "",
      );
      for (const field of [
        "Source title",
        "Selected passage",
        "Your analysis question",
      ])
        assert.equal(
          await page.getByLabel(field, { exact: true }).inputValue(),
          "",
          "New reviews start with empty fields",
        );
      assert.equal(
        await page.locator(".example-picker").getAttribute("open"),
        null,
      );
      assert(
        await page
          .getByRole("button", { name: /Run demo review/ })
          .isDisabled(),
      );
      assert.equal(
        await page
          .locator("form.setup")
          .evaluate((form) => form.checkValidity()),
        false,
      );
      assert.match(
        await page.locator(".field-note").innerText(),
        /^0 \/ 1,500 words/,
      );
      assert.equal(
        (await (await page.request.get(base + "/api/argument-health")).json())
          .provider.live_model,
        false,
      );
      assert.match(
        await page.locator(".mode-banner").innerText(),
        /Example mode/,
      );
      assert.equal(
        (await page.locator(".saved-reviews").getAttribute("open")) !== null,
        viewport.width > 760,
      );
      const header = await page.locator("header").boundingBox();
      const mode = await page.locator(".mode-banner").boundingBox();
      assert(
        mode.y >= header.y && mode.y + mode.height <= header.y + header.height,
        "Mode indicator must remain inside the header",
      );
      if (viewport.width <= 760) {
        await page.locator(".saved-reviews > summary").focus();
        await page.keyboard.press("Enter");
        await page.waitForFunction(
          () => document.querySelector(".saved-reviews").open,
        );
        await page.keyboard.press("Enter");
      }
      await inspect("new", viewport);
    }

    await page.setViewportSize(viewports[0]);
    assert.equal(createdRuns, 0, "Opening a new page must not start a review");
    await page.getByLabel("Source title", { exact: true }).fill("My source");
    await page
      .getByLabel("Selected passage", { exact: true })
      .fill("If the alarm is armed, the indicator is lit.");
    assert(
      await page.getByRole("button", { name: /Run demo review/ }).isDisabled(),
    );
    await page
      .getByLabel("Your analysis question", { exact: true })
      .fill("Which conclusion follows?");
    assert.equal(
      await page.getByRole("button", { name: /Run demo review/ }).isDisabled(),
      false,
    );
    await page
      .getByText("Source reference and context", { exact: true })
      .click();
    await page
      .getByLabel("Notes and interpretation constraints", { exact: true })
      .fill("Keep this private working note out of the next review.");
    await page
      .getByLabel("Reference URL", { exact: true })
      .fill("https://example.com/my-source");
    await page
      .getByLabel("Generation workflow", { exact: true })
      .selectOption("bounded_review");
    await page.getByText("Try an example", { exact: true }).click();
    assert(
      await page
        .getByRole("button", { name: "Use example", exact: true })
        .isDisabled(),
    );
    await page
      .getByLabel("Example or original passage", { exact: true })
      .selectOption("alarm-valid");
    assert.equal(
      await page.getByLabel("Source title", { exact: true }).inputValue(),
      "My source",
      "Choosing an option alone must not replace the draft",
    );
    await page.getByRole("button", { name: /New argument review/ }).click();
    for (const field of [
      "Source title",
      "Selected passage",
      "Your analysis question",
      "Notes and interpretation constraints",
      "Reference URL",
    ])
      assert.equal(
        await page.getByLabel(field, { exact: true }).inputValue(),
        "",
        "Starting a new review clears previous input",
      );
    assert.equal(
      await page
        .getByLabel("Generation workflow", { exact: true })
        .inputValue(),
      "single_draft",
    );
    assert.equal(
      await page.locator(".example-picker").getAttribute("open"),
      null,
    );
    assert.equal(createdRuns, 0);
    await page.getByText("Try an example", { exact: true }).click();
    await page
      .getByLabel("Example or original passage", { exact: true })
      .selectOption("alarm-valid");
    await page
      .getByRole("button", { name: "Use example", exact: true })
      .click();
    await page.getByRole("button", { name: /Run demo review/ }).click();
    await page
      .getByRole("heading", {
        name: "Review the current interpretation",
        exact: true,
      })
      .waitFor();
    assert.equal(
      await page.locator(".node-details").getAttribute("open"),
      null,
    );
    assert.equal(
      await page.locator(".content-identity").getAttribute("open"),
      null,
    );
    assert.equal(await page.locator(".graph-guide").getAttribute("open"), null);
    await page.locator(".node-details > summary").click();
    await page.getByLabel("Speaker P1", { exact: true }).waitFor();
    await page.locator(".node-details > summary").click();
    assert.equal(
      await page
        .getByLabel("Approve the displayed formal mapping", { exact: true })
        .isChecked(),
      false,
    );
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.evaluate(() => scrollTo(0, 0));
      const graphTextSizes = await page
        .locator(".graph-canvas svg text")
        .evaluateAll((elements) =>
          elements.map(
            (element) =>
              Number.parseFloat(getComputedStyle(element).fontSize) *
              Math.hypot(element.getScreenCTM().a, element.getScreenCTM().b),
          ),
        );
      assert(
        graphTextSizes.length > 0 && Math.min(...graphTextSizes) >= 12.9,
        "Graph labels must not shrink to fit a narrow screen",
      );
      await inspect("review", viewport);
    }
    await page.setViewportSize(viewports[0]);
    const savedCount = await page.locator(".run-list button").count();
    await page.getByRole("button", { name: /New argument review/ }).click();
    assert.equal(
      await page.getByLabel("Selected passage", { exact: true }).inputValue(),
      "",
    );
    assert.equal(
      await page.locator(".run-list button").count(),
      savedCount,
      "Starting a new review preserves saved runs",
    );

    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.goto(base + "/library");
      await page
        .getByRole("heading", { name: "Search your sources", exact: true })
        .waitFor();
      assert.equal(
        await page.locator(".starter, .library-foot, .library-hint").count(),
        0,
      );
      await inspect("library", viewport);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log(
      JSON.stringify({
        status: "passed",
        layouts,
        keyboard_disclosure: true,
        blank_startup: true,
        explicit_example_loading: true,
        startup_input_preserved: true,
        empty_submit_blocked: true,
        new_review_resets_form: true,
        controls_retained: true,
        page_errors: errors,
        external_requests: external,
        model_calls: 0,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
