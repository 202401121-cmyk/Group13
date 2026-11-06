/**
 * Check 8 of the quality gate, without WebGL: Chromium is started with WebGL turned off. The home page wash
 * is plain CSS, so it must still show, and the whole flow must run without a single error.
 */
import { expect, test, type Page } from "@playwright/test";
import { finishResearch, reachBrief } from "./helpers";

test.use({ launchOptions: { args: ["--disable-webgl", "--disable-webgl2", "--disable-3d-apis"] } });

/** Errors from the page itself. */
function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`page error: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });
  return errors;
}

test("the background still shows and nothing errors", async ({ page }) => {
  const errors = collectErrors(page);
  await page.clock.install();
  await page.goto("/");
  const webgl = await page.evaluate(() => !!document.createElement("canvas").getContext("webgl"));
  expect(webgl, "WebGL is really off").toBe(false);
  const wash = await page
    .locator(".bg-ambient")
    .evaluate((el) => getComputedStyle(el).backgroundImage);
  expect(wash).toContain("radial-gradient");
  await reachBrief(page);
  await page.getByRole("button", { name: /Start research/ }).click();
  await finishResearch(page);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  console.log(`[webgl off] errors: ${errors.join("; ") || "none"}`);
  expect(errors).toEqual([]);
});
