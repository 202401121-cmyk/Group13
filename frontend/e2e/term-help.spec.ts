/**
 * "What does this mean?" under a question (US-07), in mock mode. The first laptop question (CPU performance)
 * explains a word; value for money explains none. Opening or closing an explanation is never an answer: the
 * question, its number and the progress stay the same, and nothing is sent.
 */
import { expect, test, type Page, type Request } from "@playwright/test";
import { checkRow, reachQuestions } from "./quality/helpers";
import { horizontalOverflow, MIN_TARGET, tabTo } from "./quality/layout";

const TOGGLE = "What does this mean?";
const CPU_QUESTION = "CPU performance: how much does it matter to you?";
const EXPLANATION = /The main chip that does the computer's work/;

const toggle = (page: Page) => page.getByRole("button", { name: TOGGLE });
const meaning = (page: Page) => page.getByRole("region", { name: "What CPU performance means" });

/** Everything on the page that shows where the interview is: if any of it moves, an answer was recorded. */
async function whereWeAre(page: Page) {
  return {
    question: await page.locator("#question-text").textContent(),
    number: await page.getByText(/^Question \d+/).textContent(),
    // The question column and the brief panel each show how many are settled.
    settled: await page.getByText(/\d+ of \d+ settled/).allTextContents(),
    progress: await page
      .getByRole("progressbar")
      .evaluateAll((bars) => bars.map((b) => b.getAttribute("aria-valuenow"))),
    answers: await page.locator("#answers-title").textContent(),
  };
}

test("keyboard alone: Tab reaches it, Enter opens it, Space closes it, and nothing is answered", async ({
  page,
}) => {
  await reachQuestions(page);
  await expect(page.getByRole("heading", { name: CPU_QUESTION })).toBeVisible();
  const before = await whereWeAre(page);

  const sent: Request[] = [];
  page.on("request", (r) => sent.push(r));

  const stop = await tabTo(page, (name) => name.includes(TOGGLE));
  expect(stop.indicator, "the button shows where focus is").toBe(true);
  await expect(toggle(page)).toBeFocused();
  await expect(toggle(page)).toHaveAttribute("aria-expanded", "false");
  await expect(meaning(page)).toBeHidden();

  await page.keyboard.press("Enter");
  await expect(toggle(page)).toHaveAttribute("aria-expanded", "true");
  await expect(meaning(page)).toBeVisible();
  await expect(meaning(page)).toContainText(EXPLANATION);
  await expect(meaning(page).locator("strong")).toHaveText("CPU performance");
  await expect(toggle(page)).toBeFocused();
  expect(await whereWeAre(page)).toEqual(before);

  await page.keyboard.press("Space");
  await expect(toggle(page)).toHaveAttribute("aria-expanded", "false");
  await expect(meaning(page)).toBeHidden();
  await expect(toggle(page)).toBeFocused();

  expect(await whereWeAre(page)).toEqual(before);
  await expect(page.locator("#answers-list li")).toHaveCount(0);
  const toApi = sent.filter((r) => new URL(r.url()).pathname.startsWith("/api/"));
  expect(toApi.map((r) => r.url())).toEqual([]);
});

test("a question without a word to explain has no control", async ({ page }) => {
  await reachQuestions(page);
  await expect(toggle(page)).toBeVisible();
  const replies = page.getByRole("group", { name: /Quick replies/ });
  for (const [reply, next] of [
    ["Low", /^Memory: how much/],
    ["Medium", /^Battery life: how much/],
    ["High", /^Display quality: how much/],
    ["Good enough", /^Value for money: how much/],
  ] as const) {
    await replies.getByRole("button", { name: reply }).click();
    await expect(page.getByRole("heading", { name: next })).toBeVisible();
  }
  await expect(page.getByRole("button", { name: /^What does .* mean\?$/ })).toHaveCount(0);
});

for (const width of [390, 1440]) {
  test(`at ${width} px: no axe violations open or closed, light or dark, and a 44 px target`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 900 });
    await reachQuestions(page);

    await checkRow(page, `questions, term help closed, ${width} px`);

    const box = await toggle(page).boundingBox();
    expect(box, "the button is on screen").not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(MIN_TARGET - 0.5);
    expect(box!.width).toBeGreaterThanOrEqual(MIN_TARGET - 0.5);

    await toggle(page).click();
    await expect(meaning(page)).toBeVisible();
    await checkRow(page, `questions, term help open, ${width} px`);

    expect(await horizontalOverflow(page)).toEqual([]);
    const region = await meaning(page).boundingBox();
    expect(region!.x + region!.width).toBeLessThanOrEqual(width);
  });
}
