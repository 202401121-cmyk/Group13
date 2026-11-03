/**
 * @live The whole shopper flow against the real stack: frontend in live mode, backend, LLM providers and (when on)
 * the database. Start the stack with `python scripts/dev.py` (or `--no-db`), then from frontend/:
 *   LIVE_E2E=1 npx playwright test -c e2e/live/playwright.live.config.ts
 * It skips itself without LIVE_E2E=1, so `npm run test:e2e` and CI never run it. It types a made-up request only.
 * At the end it prints the session ID and each step's time; check the saved row with
 *   python scripts/check_request_saved.py <session ID>
 */
import { expect, test } from "@playwright/test";

const REQUEST = "Laptop for coding under 80k";
const MIN_ANSWERS = 5;

test.skip(process.env["LIVE_E2E"] !== "1", "live: start scripts/dev.py and set LIVE_E2E=1 to run");

test("@live a shopper goes from a typed request to ranked results with sources", async ({
  page,
}) => {
  const timings: Record<string, number> = {};
  let started = Date.now();
  const lap = (step: string) => {
    timings[step] = Date.now() - started;
    started = Date.now();
  };

  await page.goto("/");
  await page.waitForLoadState("networkidle"); // the dev server is slower to hydrate than a production build
  await page.getByLabel("Describe what you're looking for").fill(REQUEST);
  await page.getByRole("button", { name: /Start my research/ }).click();
  await expect(page).toHaveURL(/\/research$/);

  // What we understood. If the backend asks a follow-up (which kind of product?), answer it first.
  const looksRight = page.getByRole("button", { name: /Looks right/ });
  const followUp = page.getByRole("group", { name: "Product type" });
  await expect(looksRight.or(followUp)).toBeVisible({ timeout: 30_000 });
  if (await followUp.isVisible()) {
    await followUp.getByRole("button").first().click();
    await expect(looksRight).toBeVisible({ timeout: 30_000 });
  }
  lap("understood");
  await looksRight.click();

  await expect(page).toHaveURL(/\/s\/sess_[0-9a-f]{24}\/criteria$/, { timeout: 30_000 });
  const sessionId = /\/s\/(sess_[0-9a-f]{24})\//.exec(page.url())?.[1] ?? "";
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/What matters for/);
  await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible({ timeout: 30_000 });
  expect(await page.getByRole("heading", { level: 2 }).count()).toBeGreaterThanOrEqual(3);
  lap("criteria");
  await page.getByRole("button", { name: /Start the questions/ }).click();

  await expect(page).toHaveURL(/\/questions$/);
  const review = page.getByRole("link", { name: /Review your brief/ });
  let answers = 0;
  for (let i = 0; i < 20 && !(await review.isVisible()); i++) {
    const replies = page.getByRole("group", { name: /Quick replies/ }).last();
    await expect(review.or(replies)).toBeVisible({ timeout: 15_000 });
    if (await review.isVisible()) break;
    const preferred = replies.getByRole("button", { name: /high|very|important|must/i });
    const choice =
      (await preferred.count()) > 0 ? preferred.first() : replies.getByRole("button").first();
    await choice.click();
    answers += 1;
    await page.waitForTimeout(250); // let the next question or the summary arrive
  }
  await expect(review).toBeVisible({ timeout: 15_000 });
  expect(answers).toBeGreaterThanOrEqual(MIN_ANSWERS);
  lap(`interview (${answers} answers)`);
  await review.click();

  await expect(page).toHaveURL(/\/brief$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/Your brief for/);
  lap("brief");
  await page.getByRole("button", { name: /Start research/ }).click();

  await expect(page).toHaveURL(/\/results$/);
  await expect(page.getByRole("heading", { name: "Best match" })).toBeVisible({ timeout: 180_000 });
  const best = page.getByRole("list", { name: "Best match" });
  await expect(best.getByRole("region", { name: /Why .* ranks here/ })).toBeVisible();
  await expect(best.getByRole("link").first()).toHaveAttribute("href", /^https:\/\//); // a store offer's source
  const criteria = best.getByRole("group").first(); // "Show all criteria": each value with its sources
  await criteria.locator("summary").click();
  await expect(criteria.getByRole("listitem").first()).toBeVisible();
  lap("research and results");

  console.log(`LIVE_SESSION ${sessionId}`);
  console.log(`LIVE_TIMINGS ${JSON.stringify(timings)}`);
});
