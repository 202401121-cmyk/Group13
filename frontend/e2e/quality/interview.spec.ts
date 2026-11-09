/**
 * What the shopper sees when a reply comes too late. In live mode the backend answers 409 and the screen catches
 * up from the session (the adapter test covers that). The mock's "test a conflict" reply ends in the same state,
 * so the page itself can be checked here.
 */
import { expect, test } from "@playwright/test";
import { checkRow, reachQuestions } from "./helpers";

const BOX = "Your number, from 0 to 10";
const SLIDER = "Or slide to your number";

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

test("answers with all four buttons and an exact value", async ({ page }) => {
  await reachQuestions(page);
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
  await page.getByRole("button", { name: "Set an exact value" }).click();
  await page.getByLabel(BOX).fill("7.5");
  await page.getByRole("button", { name: "Send this number" }).click();
  await expect(page.getByRole("link", { name: /Review your brief/ })).toBeVisible();
  const answers = page.locator("#answers-list");
  for (const value of ["Low", "Medium", "High", "Good enough", "7.5"]) {
    await expect(answers.getByText(value, { exact: true })).toBeVisible();
  }
});

test("the exact box refuses 11, -1 and empty, and sends nothing", async ({ page }) => {
  await reachQuestions(page);
  await page.getByRole("button", { name: "Set an exact value" }).click();
  const box = page.getByLabel(BOX);
  for (const value of ["11", "-1", ""]) {
    await box.fill(value);
    await page.getByRole("button", { name: "Send this number" }).click();
    await expect(page.getByRole("alert")).toContainText("number from 0 to 10");
    await expect(page.getByRole("heading", { name: /^CPU performance: how much/ })).toBeVisible();
  }
  await box.fill("9");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await box.press("Enter");
  await expect(page.getByRole("heading", { name: /^Memory: how much/ })).toBeVisible();
});

test("switching to the exact box and back takes one click each way", async ({ page }) => {
  await reachQuestions(page);
  const replies = page.getByRole("group", { name: /Quick replies/ });
  await page.getByRole("button", { name: "Set an exact value" }).click();
  await expect(page.getByLabel(BOX)).toBeFocused();
  await expect(replies).toHaveCount(0);
  await page.getByRole("button", { name: "Use the buttons instead" }).click();
  await expect(page.getByRole("button", { name: "Set an exact value" })).toBeFocused();
  await replies.getByRole("button", { name: "High" }).click();
  await expect(page.getByRole("heading", { name: /^Memory: how much/ })).toBeVisible();
});

test("keyboard alone: Tab, Enter, Space and the slider's arrow keys", async ({ page }) => {
  await reachQuestions(page);
  await page.getByRole("button", { name: "Set an exact value" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel(BOX)).toBeFocused();

  await page.keyboard.press("Tab");
  const slider = page.getByLabel(SLIDER);
  await expect(slider).toBeFocused();
  const ring = await slider.evaluate((el) => {
    const style = getComputedStyle(el);
    return style.outlineStyle !== "none" || style.boxShadow !== "none";
  });
  expect(ring, "the slider shows where focus is").toBe(true);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByLabel(BOX)).toHaveValue("6");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByLabel(BOX)).toHaveValue("5.5");

  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Send this number" })).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page.getByRole("heading", { name: /^Memory: how much/ })).toBeVisible();
});

test("number keys pick a reply on the buttons, and do nothing while the exact box is open", async ({
  page,
}) => {
  await reachQuestions(page);
  await page.getByRole("button", { name: "Set an exact value" }).click();
  await page.getByRole("button", { name: "Send this number" }).focus();
  await page.keyboard.press("1");
  await expect(page.getByRole("heading", { name: /^CPU performance: how much/ })).toBeVisible();

  await page.getByRole("button", { name: "Use the buttons instead" }).click();
  await page.keyboard.press("4");
  await expect(page.getByRole("heading", { name: /^Memory: how much/ })).toBeVisible();
  await expect(
    page.locator("#answers-list").getByText("Good enough", { exact: true }),
  ).toBeVisible();
});

test("changing an earlier answer offers rough or exact", async ({ page }) => {
  await reachQuestions(page);
  await page.getByRole("button", { name: "Set an exact value" }).click();
  await page.getByLabel(BOX).fill("7.5");
  await page.getByLabel(BOX).press("Enter");
  await expect(page.getByRole("heading", { name: /^Memory: how much/ })).toBeVisible();

  await page.getByRole("button", { name: "Change CPU performance" }).click();
  await expect(page.getByLabel(BOX)).toHaveValue("7.5");
  await page.getByRole("button", { name: "Use the buttons instead" }).click();
  await page.getByRole("button", { name: "Good enough" }).click();
  await expect(
    page.locator("#answers-list").getByText("Good enough", { exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Change CPU performance" }).click();
  await expect(page.getByRole("button", { name: "Good enough" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByLabel(BOX)).toHaveCount(0);
});

for (const width of [390, 1440]) {
  test(`targets are at least 44 px at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await reachQuestions(page);
    const targets = [
      ...["Low", "Medium", "High", "Good enough", "Set an exact value", "Skip this question"].map(
        (name) => page.getByRole("button", { name }),
      ),
    ];
    for (const target of targets) {
      const box = await target.boundingBox();
      expect(box!.height, await target.innerText()).toBeGreaterThanOrEqual(44);
      expect(box!.width, await target.innerText()).toBeGreaterThanOrEqual(44);
    }
    await page.getByRole("button", { name: "Set an exact value" }).click();
    for (const target of [
      page.getByLabel(BOX),
      page.getByLabel(SLIDER),
      page.getByRole("button", { name: "Send this number" }),
      page.getByRole("button", { name: "Use the buttons instead" }),
    ]) {
      const box = await target.boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.width).toBeGreaterThanOrEqual(44);
    }
    const fits = await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    );
    expect(fits, "no sideways scrolling").toBe(true);
  });

  test(`the exact box passes axe in light and dark at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await reachQuestions(page);
    await checkRow(page, `questions, four buttons (${width} px)`);
    await page.getByRole("button", { name: "Set an exact value" }).click();
    await checkRow(page, `questions, exact box (${width} px)`);
    await page.getByRole("button", { name: "Send this number" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await checkRow(page, `questions, exact box refusing a value (${width} px)`);
  });
}
