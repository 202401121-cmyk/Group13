/**
 * The result states that carry a promise to the shopper, checked on the real page in mock mode: what each one
 * says, and axe (WCAG 2.0, 2.1 and 2.2, A and AA) at 390 and 1440 px, light and dark.
 */
import { expect, test, type Page } from "@playwright/test";
import { checkRow, finishResearch, reachBrief, startScenario } from "./helpers";

const SIZES = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
];

const STATES = ["tie", "tie-first", "stale", "no-offer"];

/** Runs one mock scenario to its results, with every section open. */
async function showScenario(page: Page, sessionId: string, scenario: string) {
  await startScenario(page, sessionId, scenario);
  await finishResearch(page);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  await page.evaluate(() => {
    for (const d of document.querySelectorAll("details")) d.open = true;
  });
}

test.describe("result states", () => {
  test.describe.configure({ mode: "parallel", timeout: 240_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  for (const size of SIZES) {
    test(`axe on every changed state at ${size.width} px`, async ({ page }) => {
      await page.setViewportSize(size);
      const id = await reachBrief(page);
      for (const scenario of STATES) {
        await showScenario(page, id, scenario);
        await checkRow(page, `results, ${scenario} (${size.width} px)`);
      }
    });
  }

  test("a tie for second has one card and the rest of the page is unchanged", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "tie");
    const tie = page.getByRole("region", { name: "Too close to call" });
    await expect(tie).toHaveCount(1);
    await expect(tie).toContainText("Sample laptop B and Sample laptop C are tied for 2nd.");
    await expect(tie).toContainText("What separates them: CPU performance");
    await expect(page.getByText("Tied for 2nd", { exact: true })).toHaveCount(2);
    await expect(page.getByText(/#=/)).toHaveCount(0);
    await expect(page.getByRole("list", { name: "Best match" }).getByRole("article")).toHaveCount(
      1,
    );
    await expect(
      page.getByRole("list", { name: "Other options" }).getByRole("article"),
    ).toHaveCount(2);
  });

  test("old prices say so, from their as-of time", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "stale");
    await expect(page.getByText("Price may have changed")).toHaveCount(3);
    await expect(page.getByText(/^Old price/)).toHaveCount(0);
    await expect(page.getByText("We don't know when this price was checked")).toHaveCount(1);
    await expect(page.getByText(/^Price checked /)).toHaveCount(5);

    // Every other scenario's prices are recent, so none carries the warning.
    await showScenario(page, id, "success");
    await expect(page.getByText("Price may have changed")).toHaveCount(0);
  });

  test("no offer says so and links to a store search", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "no-offer");
    const b = page.getByRole("region", { name: "Store offers for Sample laptop B" });
    await expect(b).toContainText("No price found yet");
    const search = b.getByRole("link", { name: /Search stores/ });
    await expect(search).toHaveAttribute("target", "_blank");
    await expect(search).toHaveAttribute("rel", "noopener noreferrer");
    await expect(search).toHaveAttribute("href", /^https:\/\/example\.com\/store-search\?q=/);
  });

  test("a tie for first names no single best match", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "tie-first");
    const best = page.getByRole("list", { name: "Best match" });
    await expect(best.getByRole("article")).toHaveCount(2);
    await expect(best.getByRole("region", { name: "Too close to call" })).toContainText(
      "Sample laptop A and Sample laptop B are tied for 1st.",
    );
    await expect(best.getByText("Tied for 1st", { exact: true })).toHaveCount(2);
    await expect(best.getByText("Best match", { exact: true })).toHaveCount(0);
    await expect(
      page.getByRole("list", { name: "Other options" }).getByRole("article"),
    ).toHaveCount(1);
    await expect(page.getByText("are too close to call.")).toHaveCount(1);
  });
});
