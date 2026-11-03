/**
 * The whole flow in mock mode: home page box -> what we understood -> criteria -> questions -> brief ->
 * results, with an axe accessibility check (WCAG 2 A and AA) on every page on the way.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function expectAccessible(page: Page, name: string) {
  const { violations } = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const summary = violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`);
  expect(summary, `axe violations on ${name}`).toEqual([]);
}

test("a shopper goes from the home page to ranked results", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Find the right product");
  await expectAccessible(page, "home");

  // The home page box starts the real flow, without putting the request in the address.
  const request = "Laptop for coding under ₹70,000";
  await page.getByLabel("Describe what you're looking for").fill(request);
  await page.getByRole("button", { name: /Start my research/ }).click();
  await expect(page).toHaveURL(/\/research$/);
  expect(page.url()).not.toContain("coding");

  await expect(page.getByText("Laptop", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Looks right/ })).toBeVisible();
  await expectAccessible(page, "what we understood");
  await page.getByRole("button", { name: /Looks right/ }).click();

  await expect(page).toHaveURL(/\/s\/sess_[0-9a-f]{24}\/criteria$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("What matters for a laptop");
  await expect(page.getByRole("heading", { name: /CPU performance/ })).toBeVisible();
  await expectAccessible(page, "criteria");
  await page.getByRole("button", { name: /Start the questions/ }).click();

  await expect(page).toHaveURL(/\/questions$/);
  const review = page.getByRole("link", { name: /Review your brief/ });
  const replies = page.getByRole("group", { name: /Quick replies/ });
  await expect(replies).toBeVisible();
  await expect(
    page.getByText(/How much do you care about CPU performance\?/).first(),
  ).toBeVisible();
  await expectAccessible(page, "questions");
  for (let i = 0; i < 10 && !(await review.isVisible()); i++) {
    await replies.getByRole("button", { name: /High/ }).click();
    await expect(review.or(replies.getByRole("button", { name: /High/ }))).toBeEnabled();
  }
  await expect(review).toBeVisible();
  await expectAccessible(page, "questions finished");
  await review.click();

  await expect(page).toHaveURL(/\/brief$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Your brief for a laptop");
  await expectAccessible(page, "brief");
  await page.getByRole("button", { name: /Start research/ }).click();

  await expect(page).toHaveURL(/\/results$/);
  await expect(page.getByRole("heading", { name: "Best match" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Sample laptop A/).first()).toBeVisible();
  await expectAccessible(page, "results");
});

test("the privacy page says what is kept", async ({ page }) => {
  await page.goto("/privacy");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Privacy");
  await expect(page.getByText(/You don't need an account/)).toBeVisible();
  await expectAccessible(page, "privacy");
});
