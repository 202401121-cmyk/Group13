/**
 * An unknown address: the server still answers 404, and the shopper sees plain words and a way home, with no
 * error code. axe (WCAG 2.0, 2.1 and 2.2, A and AA) at 390 and 1440 px, light and dark.
 */
import { expect, test } from "@playwright/test";
import { checkRow } from "./helpers";

test("an unknown address says so plainly and leads home", async ({ page }) => {
  const response = await page.goto("/no-such-page");
  expect(response?.status()).toBe(404);

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("We can't find that page");
  await expect(page.getByRole("heading")).toHaveCount(1);
  await expect(page.getByText("It may have moved, or the address may be wrong.")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("404");

  await page.getByRole("link", { name: "Go home" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Find the right product");
});

for (const size of [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
]) {
  test(`axe on the page at ${size.width} px`, async ({ page }) => {
    await page.setViewportSize(size);
    await page.goto("/no-such-page");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await checkRow(page, `page not found (${size.width} px)`);
  });
}
