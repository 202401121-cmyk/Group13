/**
 * Data coverage next to an estimate (US-18) and the link to research a product we couldn't check (US-17), on the
 * real page in mock mode, with axe at 390 and 1440 px, light and dark, and every section open.
 */
import { expect, test, type Page } from "@playwright/test";
import { checkRow, finishResearch, reachBrief, startScenario } from "./helpers";

async function showScenario(page: Page, sessionId: string, scenario: string) {
  await startScenario(page, sessionId, scenario);
  await finishResearch(page);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  await page.evaluate(() => {
    for (const d of document.querySelectorAll("details")) d.open = true;
  });
}

test.describe("estimates and unchecked products", () => {
  test.describe.configure({ mode: "parallel", timeout: 240_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  for (const size of [
    { width: 390, height: 844 },
    { width: 1440, height: 900 },
  ]) {
    test(`axe at ${size.width} px`, async ({ page }) => {
      await page.setViewportSize(size);
      const id = await reachBrief(page);
      for (const scenario of ["estimated", "couldnt-check"]) {
        await showScenario(page, id, scenario);
        await checkRow(page, `results, ${scenario}, sections open (${size.width} px)`);
      }
    });
  }

  test("an estimate shows its product's coverage, or says it isn't known", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "estimated");
    const best = page.getByRole("article", { name: /Sample laptop A/ });
    const next = page.getByRole("article", { name: /Sample laptop B/ });
    await expect(best.getByText("Estimated. Data coverage 62%")).toBeVisible();
    await expect(next.getByText("Estimated. Coverage not known")).toBeVisible();
    await expect(next.getByText(/\b0%/)).toHaveCount(0);
  });

  test("each product shows its coverage on the card", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "success");
    for (const [name, coverage] of [
      ["Sample laptop A", "Data coverage 92%"],
      ["Sample laptop B", "Data coverage 84%"],
      ["Sample laptop C", "Data coverage 76%"],
    ] as const) {
      await expect(page.getByRole("article", { name }).getByText(coverage)).toBeVisible();
    }
  });

  test("a product we couldn't check links to a web search in a new tab", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "couldnt-check");
    const link = page.getByRole("link", { name: /Search the web/ });
    await expect(link).toHaveCount(1);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    const href = await link.getAttribute("href");
    expect(new URL(href!).origin).toBe("https://www.google.com");
    expect(new URL(href!).searchParams.get("q")).toMatch(/^Sample laptop F \S/);
    const box = await link.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  });
});
