/**
 * What the shopper sees when a reply comes too late. In live mode the backend answers 409 and the screen catches
 * up from the session (the adapter test covers that). The mock's "test a conflict" reply ends in the same state,
 * so the page itself can be checked here.
 */
import { expect, test } from "@playwright/test";
import { checkRow, reachQuestions } from "./helpers";

test("a reply that came too late shows the current question and says why", async ({ page }) => {
  await reachQuestions(page);
  const composer = page.getByLabel("Or type your answer");
  await composer.fill("test a conflict");
  await composer.press("Enter");

  const notice = page
    .getByRole("status")
    .filter({ hasText: "This question was already answered. Here is where you are." });
  await expect(notice).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "CPU performance: how much does it matter to you?" }),
  ).toBeVisible();
  await checkRow(page, "questions, reply came too late");

  await page
    .getByRole("group", { name: /Quick replies/ })
    .getByRole("button", { name: "High" })
    .click();
  await expect(page.getByRole("heading", { name: /^Memory: how much/ })).toBeVisible();
  await expect(notice).toHaveCount(0);
});
