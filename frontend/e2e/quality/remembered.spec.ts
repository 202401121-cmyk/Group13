/**
 * US-06 in the interview (S5-12 part A): a remembered answer shows as an answered turn, "Remembered from 14 Nov", with
 * Change and Forget. axe at 390 and 1440 px, light and dark. Set SHOTS_DIR to a folder outside the repository to save
 * the screenshots for the pull request. Mock mode: the mock remembers the laptop's weight.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { checkRow, reachQuestions, setTheme, type Theme } from "./helpers";

const SHOTS = process.env["SHOTS_DIR"];

async function screenshot(page: Page, width: number) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  for (const theme of ["light", "dark"] as Theme[]) {
    await setTheme(page, theme);
    await page.screenshot({
      path: join(SHOTS, `${width}-${theme}-remembered.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
  await setTheme(page, "light");
}

for (const size of [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
]) {
  test(`a remembered answer, with Change and Forget, at ${size.width} px`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize(size);
    await reachQuestions(page);
    for (let i = 0; i < 3; i++) {
      const high = page
        .getByRole("group", { name: /Quick replies/ })
        .getByRole("button", { name: "High" });
      await expect(high).toBeEnabled();
      await high.click();
    }
    // On a phone the list is folded away under its heading; open it as a shopper would.
    const toggle = page.getByRole("button", { name: /Answers so far/ });
    if ((await toggle.getAttribute("aria-expanded")) === "false" && size.width < 1024)
      await toggle.click();
    const row = page.locator("[data-remembered]");
    await expect(row.locator("[data-remembered-from]")).toHaveText(
      /^Remembered from 14 Nov( 2026)?$/,
    );
    await expect(row.getByRole("button", { name: "Change Weight" })).toBeVisible();
    await expect(row.getByRole("button", { name: "Forget Weight" })).toBeVisible();
    await checkRow(page, `questions, a remembered answer (${size.width} px)`);
    await row.scrollIntoViewIfNeeded();
    await screenshot(page, size.width);
  });
}
