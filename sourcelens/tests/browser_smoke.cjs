/* Exercise the built frontend against a running local API. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { chromium } = createRequire(path.join(__dirname, "../web/package.json"))(
  "playwright",
);
process.chdir(path.resolve(__dirname, ".."));

(async () => {
  const base = process.env.SOURCELENS_TEST_URL || "http://127.0.0.1:8765";
  assert(
    ["127.0.0.1", "localhost"].includes(new URL(base).hostname),
    "Use a local test server",
  );
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.SOURCELENS_CHROME
      ? { executablePath: process.env.SOURCELENS_CHROME }
      : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    locale: "en-US",
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(base);
    await page
      .getByRole("heading", { name: "Follow the evidence." })
      .waitFor({ timeout: 10000 });
    const existing = await (
      await page.request.get(base + "/api/documents")
    ).json();
    if (existing.length === 0) {
      await page
        .getByRole("button", { name: "Load example library", exact: true })
        .click();
    } else {
      const seed = await page.request.post(base + "/api/examples");
      assert(seed.ok(), "Example ingestion");
      await page.reload();
    }
    await page.locator(".document").first().waitFor();
    await page
      .getByLabel("Research question", { exact: true })
      .fill(
        "How do write-ahead logs and checkpoints handle recovery after a crash?",
      );
    await page
      .getByRole("button", { name: "Start research", exact: true })
      .click();
    await page.locator(".run .status.succeeded").waitFor();
    assert(
      (await page.locator(".quote-card").count()) > 0,
      "Rendered evidence",
    );
    await fs.mkdir("docs/images", { recursive: true });
    await page.screenshot({
      path: "docs/images/workspace.png",
      fullPage: true,
    });
    await page.getByRole("button", { name: "C1", exact: true }).first().click();
    await page.getByLabel("Source passage", { exact: true }).waitFor();
    assert(
      await page.locator(".source-body").innerText(),
      "Citation resolves to source",
    );
    await page
      .getByLabel("Source passage", { exact: true })
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await page
      .locator(".library .document-title")
      .filter({ hasText: "Write-ahead logs and recovery" })
      .click();
    await page
      .getByRole("button", { name: "Import new version", exact: true })
      .click();
    await page.waitForFunction(
      () => document.activeElement?.name === "source_key",
    );
    const dialog = page.getByRole("dialog");
    await dialog
      .getByRole("button", { name: "Import document", exact: true })
      .focus();
    await page.keyboard.press("Tab");
    assert(
      await dialog
        .getByRole("button", { name: "Close", exact: true })
        .evaluate((element) => element === document.activeElement),
      "Dialog contains keyboard focus",
    );
    await page
      .getByRole("button", { name: "Paste Markdown", exact: true })
      .click();
    await page
      .getByLabel("Version label", { exact: true })
      .fill("Example notes v2");
    const text = await fs.readFile("examples/recovery.md", "utf8");
    await page
      .getByLabel("Document text", { exact: true })
      .fill(
        text +
          "\n\nA revised note retains explicit recovery assumptions. " +
          Date.now(),
      );
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Import document", exact: true })
      .click();
    await page.getByText(/cited sources? ha[sv]e? a newer version/).waitFor();
    await page
      .getByRole("button", { name: "Search passages", exact: true })
      .click();
    await page
      .getByLabel("Search query", { exact: true })
      .fill("idempotency key request parameters");
    await page.getByRole("combobox").selectOption("bm25");
    await page
      .getByRole("button", { name: "Search library", exact: true })
      .click();
    await page.locator(".search-card").first().waitFor();
    assert(
      (await page.locator(".search-card").first().innerText()).includes(
        "Idempotent effects",
      ),
    );
    await page.getByRole("button", { name: "Research", exact: true }).click();
    await page
      .getByLabel("Research question", { exact: true })
      .fill("How many moons orbit Neptune?");
    await page
      .getByRole("button", { name: "Start research", exact: true })
      .click();
    await page
      .getByText("No matching evidence was found in this library snapshot.", {
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("button", { name: "Recent runs", exact: true })
      .click();
    await page.locator(".history-row").first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Research", exact: true }).click();
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "No mobile horizontal overflow",
    );
    await page.screenshot({ path: "docs/images/mobile.png", fullPage: true });
    assert.deepEqual(errors, [], "No frontend runtime errors");
    console.log(
      JSON.stringify({
        result: "passed",
        checks: [
          "research",
          "citation resolution",
          "version update warning",
          "keyword search",
          "no-evidence response",
          "history",
          "mobile layout",
          "no runtime errors",
          "dialog keyboard focus",
        ],
      }),
    );
  } catch (error) {
    console.error(
      JSON.stringify({
        runtimeErrors: errors,
        pageText: await page.locator("body").innerText(),
      }),
    );
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
