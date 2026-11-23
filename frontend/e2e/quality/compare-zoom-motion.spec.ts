/**
 * US-33 (S5-12 edge cases): the compare view at 200% and 400% zoom (WCAG 1.4.4, 1.4.10) keeps the page from
 * scrolling sideways, with the table scrolling inside its own region; and with reduced motion, re-ranking and
 * comparing move nothing. Zoom is emulated as zoom.spec.ts does: the CSS viewport and the device pixel ratio.
 */
import { expect, test, type Page } from "@playwright/test";
import { finishResearch, reachBrief, startScenario } from "./helpers";
import { clippedText } from "./layout";

async function compareThree(page: Page) {
  const id = await reachBrief(page);
  await startScenario(page, id, "success");
  await finishResearch(page);
  for (const name of ["Sample laptop A", "Sample laptop B", "Sample laptop C"])
    await page.getByLabel(`Compare ${name}`).check();
  await page.getByRole("button", { name: "Compare (3)" }).click();
  await expect(page.getByRole("heading", { name: "Compare 3 products" })).toBeFocused();
}

/** What sticks out past the screen's edge, outside the table's own scrolling region. */
function outsideTheRegion(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const region = document.querySelector('[role="region"][aria-label="Comparison table"]');
    const out: string[] = [];
    if (document.documentElement.scrollWidth > vw + 1)
      out.push(`page scrolls sideways: ${document.documentElement.scrollWidth}px > ${vw}px`);
    for (const el of document.querySelectorAll<HTMLElement>("body *")) {
      if (region?.contains(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || (r.right <= vw + 1 && r.left >= -1)) continue;
      if (getComputedStyle(el).visibility === "hidden" || el.closest(".sr-only")) continue;
      out.push(`${el.tagName.toLowerCase()} spans ${Math.round(r.left)}..${Math.round(r.right)}`);
    }
    return out;
  });
}

for (const zoom of [
  { name: "200%", viewport: { width: 640, height: 360 }, deviceScaleFactor: 2 },
  { name: "400%", viewport: { width: 320, height: 256 }, deviceScaleFactor: 4 },
]) {
  test.describe(`compare at ${zoom.name} zoom`, () => {
    test.use({ viewport: zoom.viewport, deviceScaleFactor: zoom.deviceScaleFactor });

    test(`the page never scrolls sideways; the table scrolls in its own region (${zoom.name})`, async ({
      page,
    }) => {
      test.setTimeout(180_000);
      await page.clock.install();
      await compareThree(page);
      expect(await outsideTheRegion(page)).toEqual([]);
      const region = page.getByRole("region", { name: "Comparison table" });
      const scrolls = await region.evaluate((el) => el.scrollWidth > el.clientWidth);
      expect(scrolls, "the table scrolls inside its region").toBe(true);
      await region.focus();
      // Keyboard scrolling is smooth: a few presses, then wait for it to land.
      for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowRight");
      await expect.poll(() => region.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
      expect(await page.evaluate(() => window.scrollX)).toBe(0);
      expect((await clippedText(page)).filter((t) => !/^(th|td|table)\b/.test(t))).toEqual([]);
      await expect(page.getByRole("button", { name: "Close comparison" })).toBeVisible();
    });
  });
}

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("re-ranking and comparing move nothing", async ({ page }) => {
    test.setTimeout(180_000);
    await page.clock.install();
    const id = await reachBrief(page);
    await startScenario(page, id, "success");
    await finishResearch(page);
    const running = () =>
      page.evaluate(
        () =>
          document
            .getAnimations()
            .filter((a) => a.playState === "running")
            .map((a) => (a.effect as KeyframeEffect | null)?.target?.nodeName ?? "?").length,
      );
    await page.getByRole("button", { name: "Change what matters" }).click();
    const box = page.getByLabel(/, from 0 to 10$/).first();
    await box.fill("0");
    await expect(
      page.getByText("Confidence was worked out for your original priorities."),
    ).toBeVisible();
    expect(await running(), "animations running after a re-rank").toBe(0);
    const faded = await page
      .locator("[data-reveal]")
      .evaluateAll((els) => els.filter((e) => getComputedStyle(e).opacity !== "1").length);
    expect(faded, "no card waits to fade in").toBe(0);
    for (const name of ["Sample laptop A", "Sample laptop B"])
      await page.getByLabel(`Compare ${name}`).check();
    await page.getByRole("button", { name: "Compare (2)" }).click();
    await expect(page.getByRole("heading", { name: "Compare 2 products" })).toBeFocused();
    expect(await running(), "animations running in the compare view").toBe(0);
  });
});
