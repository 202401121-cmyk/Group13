/**
 * US-50: on every merge to main, a shopper goes from a typed request through the interview to results, in a real
 * browser, against the real backend with fake LLM providers and the database off (backend/tests/worst_case/
 * fake_stack.py). The providers never answer, so intake keeps to its rules and the interview to its fixed questions,
 * and the search's stub stages give a sample result. It types a made-up request only.
 *
 * Run by its own config (e2e/merge/playwright.merge.config.ts), which starts both servers. It skips itself without
 * MERGE_E2E=1, so `npm run test:e2e` (mock mode) never runs it.
 */
import { expect, test } from "@playwright/test";

const REQUEST = "Laptop for coding under 80k";

test.skip(
  process.env["MERGE_E2E"] !== "1",
  "merge: run with e2e/merge/playwright.merge.config.ts and MERGE_E2E=1",
);

test("a shopper goes from a typed request through the interview to results", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Describe what you're looking for").fill(REQUEST);
  await page.getByRole("button", { name: /Start my research/ }).click();

  // Intake by its keyword rules: a laptop, then the criteria.
  await expect(page).toHaveURL(/\/research$/);
  await page.getByRole("button", { name: /Looks right/ }).click();
  await expect(page).toHaveURL(/\/s\/sess_[0-9a-f]{24}\/criteria$/, { timeout: 30_000 });
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/What matters for/);
  await page.getByRole("button", { name: /Start the questions/ }).click();

  // The fixed questions: answer each with its first quick reply until the brief is ready.
  await expect(page).toHaveURL(/\/questions$/);
  const review = page.getByRole("link", { name: /Review your brief/ });
  let answers = 0;
  for (let i = 0; i < 20 && !(await review.isVisible()); i++) {
    const replies = page.getByRole("group", { name: /Quick replies/ }).last();
    await expect(review.or(replies)).toBeVisible({ timeout: 15_000 });
    if (await review.isVisible()) break;
    await replies.getByRole("button").first().click();
    answers += 1;
    await page.waitForTimeout(250); // the next question or the summary arrives
  }
  await expect(review).toBeVisible({ timeout: 15_000 });
  expect(answers).toBeGreaterThan(0);
  await review.click();

  await expect(page).toHaveURL(/\/brief$/);
  await page.getByRole("button", { name: /Start research/ }).click();

  // Results: the stub stages' sample ranking, with its best match.
  await expect(page).toHaveURL(/\/results(\?search=srch_[0-9a-f]{24})?$/);
  await expect(page.getByRole("heading", { name: "Best match" })).toBeVisible({ timeout: 60_000 });
});
