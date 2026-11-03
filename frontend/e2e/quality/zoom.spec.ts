/**
 * Check 5 of the quality gate: zoom and text spacing.
 * - 200% browser zoom on a 1280 x 720 screen leaves 640 x 360 CSS pixels (WCAG 1.4.4).
 * - 400% zoom on a 1280 x 1024 screen leaves 320 x 256 CSS pixels (WCAG 1.4.10 reflow): no sideways scrolling.
 * - Text spacing (WCAG 1.4.12): line height 1.5, paragraph spacing 2em, letter spacing 0.12em and word
 *   spacing 0.16em, injected as a style. Nothing may be cut off.
 * Zoom is emulated with the CSS viewport and the device pixel ratio, which is what browser zoom changes.
 */
import { expect, test, type Page } from "@playwright/test";
import { clippedText, horizontalOverflow } from "./layout";
import { finishResearch, reachBrief, reachQuestions, reachUnderstood } from "./helpers";

const TEXT_SPACING = `
  * { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }
  p { margin-bottom: 2em !important; }
`;

async function check(page: Page, label: string) {
  const found = [
    ...(await horizontalOverflow(page)).map((s) => `overflow: ${s}`),
    ...(await clippedText(page)).map((s) => `clipped: ${s}`),
  ];
  console.log(`[zoom] ${label}: ${found.length ? found.join("; ") : "pass"}`);
  expect.soft(found, label).toEqual([]);
}

async function everyScreen(page: Page, mode: string) {
  await page.goto("/");
  await check(page, `home, ${mode}`);
  await page.goto("/privacy");
  await check(page, `privacy, ${mode}`);
  await reachUnderstood(page);
  await check(page, `what we understood, ${mode}`);
  await reachQuestions(page);
  await check(page, `questions, ${mode}`);
  await reachBrief(page);
  await check(page, `brief, ${mode}`);
  await page.getByRole("button", { name: /Start research/ }).click();
  await finishResearch(page);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  for (const s of await page.locator("summary").all()) await s.click();
  await check(page, `results, ${mode}`);
}

test.describe("zoom and text spacing", () => {
  test.describe.configure({ timeout: 240_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  test.describe("200% zoom", () => {
    test.use({ viewport: { width: 640, height: 360 }, deviceScaleFactor: 2 });
    test("every screen reflows at 200%", async ({ page }) => {
      await everyScreen(page, "200% zoom");
    });
  });

  test.describe("400% zoom", () => {
    test.use({ viewport: { width: 320, height: 256 }, deviceScaleFactor: 4 });
    test("every screen reflows at 400% without sideways scrolling", async ({ page }) => {
      await everyScreen(page, "400% zoom");
    });
  });

  test.describe("text spacing", () => {
    for (const vp of [
      { width: 1280, height: 720 },
      { width: 390, height: 844 },
    ]) {
      test(`nothing is cut off with wider text spacing at ${vp.width} px`, async ({ page }) => {
        await page.setViewportSize(vp);
        await page.addInitScript((css) => {
          document.addEventListener("DOMContentLoaded", () => {
            const style = document.createElement("style");
            style.textContent = css;
            document.head.append(style);
          });
        }, TEXT_SPACING);
        await everyScreen(page, `text spacing ${vp.width} px`);
      });
    }
  });
});
