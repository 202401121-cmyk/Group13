/**
 * Check 4 of the quality gate: screen sizes. Each screen is checked at 320, 360, 390, 768, 1024, 1440 and
 * 1920 px wide, and at 390 and 768 in landscape: nothing sticks out sideways, no text is cut off, and every
 * target is at least 44 x 44 px. A state is reached once and then resized, so it is the same state each time.
 */
import { expect, test, type Page } from "@playwright/test";
import { clippedText, horizontalOverflow, smallTargets } from "./layout";
import {
  finishResearch,
  reachBrief,
  reachQuestions,
  reachUnderstood,
  startScenario,
} from "./helpers";

const VIEWPORTS = [
  { name: "320", width: 320, height: 640 },
  { name: "360", width: 360, height: 740 },
  { name: "390", width: 390, height: 844 },
  { name: "768", width: 768, height: 1024 },
  { name: "1024", width: 1024, height: 1366 },
  { name: "1440", width: 1440, height: 900 },
  { name: "1920", width: 1920, height: 1080 },
  { name: "390 landscape", width: 844, height: 390 },
  { name: "768 landscape", width: 1024, height: 768 },
] as const;

async function checkSizes(page: Page, screen: string) {
  for (const vp of VIEWPORTS) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.waitForTimeout(50);
    const found = [
      ...(await horizontalOverflow(page)).map((s) => `overflow: ${s}`),
      ...(await clippedText(page)).map((s) => `clipped: ${s}`),
      ...(await smallTargets(page)).map((s) => `target: ${s}`),
    ];
    console.log(`[viewport] ${screen} @ ${vp.name}: ${found.length ? found.join("; ") : "pass"}`);
    expect.soft(found, `${screen} at ${vp.name}`).toEqual([]);
  }
  await page.setViewportSize({ width: 1280, height: 720 });
}

test.describe("screen sizes", () => {
  test.describe.configure({ timeout: 180_000 });

  test("information pages", async ({ page }) => {
    for (const [path, screen] of [
      ["/", "home"],
      ["/privacy", "privacy"],
      ["/about", "about"],
      ["/no-such-page", "page not found"],
    ] as const) {
      await page.goto(path);
      await checkSizes(page, screen);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.getByRole("button", { name: "Open menu" }).click();
    for (const vp of VIEWPORTS.filter((v) => v.width < 768)) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      const found = [...(await horizontalOverflow(page)), ...(await smallTargets(page))];
      console.log(`[viewport] menu open @ ${vp.name}: ${found.length ? found.join("; ") : "pass"}`);
      expect.soft(found, `menu open at ${vp.name}`).toEqual([]);
    }
  });

  test("request and interview screens", async ({ page }) => {
    await page.goto("/research");
    await checkSizes(page, "research, empty");
    await reachUnderstood(page);
    await checkSizes(page, "what we understood");
    await page.getByRole("button", { name: "Edit budget" }).click();
    await checkSizes(page, "what we understood, editing budget");
    await page.getByRole("button", { name: "Cancel" }).click();
    await page.getByRole("button", { name: /Looks right/ }).click();
    await expect(page.getByRole("button", { name: /Start the questions/ })).toBeEnabled();
    await checkSizes(page, "criteria");
    await reachQuestions(page);
    await page.getByRole("button", { name: "Why this question?" }).click();
    await checkSizes(page, "questions");
  });

  test("brief and results screens", async ({ page }) => {
    await page.clock.install();
    const id = await reachBrief(page);
    await checkSizes(page, "brief");
    await page.getByRole("button", { name: /Start research/ }).click();
    await page.clock.runFor(1_500);
    await checkSizes(page, "results, researching");
    await page.clock.runFor(7_000);
    await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
    for (const s of await page.locator("summary").all()) await s.click();
    await checkSizes(page, "results, every section open");
    for (const scenario of ["disputed", "stale", "no-offer", "relaxation"]) {
      await startScenario(page, id, scenario);
      await finishResearch(page);
      await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
      for (const s of await page.locator("summary").all()) await s.click();
      await checkSizes(page, `results, ${scenario}`);
    }
  });
});
