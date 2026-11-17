/**
 * What the shopper sees when a reply comes too late. In live mode the backend answers 409 and the screen catches
 * up from the session (the adapter test covers that). The mock's "test a conflict" reply ends in the same state,
 * so the page itself can be checked here.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { answerAll, checkRow, reachQuestions, setTheme, type Theme } from "./helpers";

const SHOTS = process.env["SHOTS_DIR"];

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

test("every reply kind reads as the backend writes it, and a skipped question can be answered now", async ({
  page,
}) => {
  await reachQuestions(page);
  const heading = page.locator("#question-text");
  const note = page.locator("[data-step-note]");
  const cpu = "CPU performance: how much does it matter to you?";
  await expect(heading).toHaveText(cpu);
  await expect(note).toHaveCount(0);
  await expect(page.getByText(/^Why this question:/)).toBeVisible();

  const composer = page.getByLabel("Or type your answer");
  // A question back: the answer above, then the same question.
  await composer.fill("Is this about compile speed?");
  await composer.press("Enter");
  await expect(note).toContainText(/^Good question\./);
  await expect(heading).toHaveText(cpu);

  // Unclear: asked another way, with the same buttons.
  await composer.fill("not sure");
  await composer.press("Enter");
  await expect(page.getByText("Asked another way")).toBeVisible();
  await expect(heading).toContainText(
    "Let me ask another way: should CPU performance be low, medium or high on your list?",
  );
  const replies = page.getByRole("group", { name: /Quick replies/ });
  for (const reply of ["Low", "Medium", "High", "Good enough"]) {
    await expect(replies.getByRole("button", { name: reply })).toBeVisible();
  }

  // Unclear again: skipped and marked, and the next question opens with the note.
  await composer.fill("not sure");
  await composer.press("Enter");
  await expect(note).toHaveText("Let's skip that one for now. You can answer it later.");
  await expect(heading).toHaveText(/^Memory: how much/);

  // Off-topic: one line back to the question.
  await composer.fill("what about the price");
  await composer.press("Enter");
  await expect(note).toContainText("use the budget from your brief");
  await expect(heading).toHaveText(/^Memory: how much/);

  // Keyboard only: Tab to "Answer now" for the skipped question and press Enter.
  await tabTo(page, "Answer now");
  await page.keyboard.press("Enter");
  await expect(heading).toHaveText(cpu);
  await replies.getByRole("button", { name: "High" }).click();
  await expect(page.getByRole("button", { name: "Answer now: CPU performance" })).toHaveCount(0);

  // Skip one with the button, finish, and the finished card lists it with "Answer now".
  await expect(heading).toHaveText(/^Memory: how much/);
  await page.getByRole("button", { name: /^Skip/ }).click();
  await expect(heading).toHaveText(/^Battery life: how much/);
  await answerAll(page);
  await expect(page.getByText("Thanks, that's everything I need.", { exact: true })).toBeVisible();
  const skipped = page.getByRole("region", { name: "Skipped questions" });
  await expect(skipped).toContainText("Memory");
  await checkRow(page, "questions, finished with a skipped question");
  await tabTo(page, "Answer now", skipped);
  await page.keyboard.press("Enter");
  await expect(heading).toHaveText(/^Memory: how much/);
});

/**
 * Presses Tab until a button with this text has focus (keyboard only), inside `within` when given, failing if it
 * is never reached.
 */
async function tabTo(page: Page, name: string, within?: Locator) {
  for (let i = 0; i < 80; i++) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? "");
    const inside = within
      ? await within.evaluate((el) => el.contains(document.activeElement))
      : true;
    if (focused === name && inside) return;
  }
  throw new Error(`Tab never reached "${name}"`);
}

/** Screenshots of every step kind for the pull request, in each size and theme. Runs only when SHOTS_DIR is set. */
for (const size of [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
]) {
  test(`screenshots of every step kind at ${size.width} px`, async ({ page }) => {
    test.skip(!SHOTS, "SHOTS_DIR is not set");
    const dir = SHOTS!;
    mkdirSync(dir, { recursive: true });
    const shoot = async (name: string) => {
      for (const theme of ["light", "dark"] as Theme[]) {
        await setTheme(page, theme);
        await page.screenshot({
          path: join(dir, `${size.width}-${theme}-questions-${name}.png`),
          fullPage: true,
          animations: "disabled",
        });
      }
      await setTheme(page, "light");
    };
    await page.setViewportSize(size);
    await reachQuestions(page);
    const heading = page.locator("#question-text");
    const note = page.locator("[data-step-note]");
    const composer = page.getByLabel("Or type your answer");
    await shoot("question");

    await composer.fill("Is this about compile speed?");
    await composer.press("Enter");
    await expect(note).toContainText(/^Good question\./);
    await shoot("answer-then-repeat");

    await composer.fill("not sure");
    await composer.press("Enter");
    await expect(page.getByText("Asked another way")).toBeVisible();
    await shoot("rephrase");

    await composer.fill("not sure");
    await composer.press("Enter");
    await expect(note).toHaveText("Let's skip that one for now. You can answer it later.");
    if (size.width < 1024) await page.getByRole("button", { name: /Answers so far/ }).click();
    await expect(page.getByRole("button", { name: "Answer now: CPU performance" })).toBeVisible();
    await shoot("skipped");

    await composer.fill("what about the price");
    await composer.press("Enter");
    await expect(note).toContainText("use the budget from your brief");
    await expect(heading).toHaveText(/^Memory: how much/);
    await shoot("redirect");

    await answerAll(page);
    await expect(page.getByRole("region", { name: "Skipped questions" })).toBeVisible();
    await shoot("finished-with-skipped");
  });
}

test("the smart order says why it asks, stops once settled, and the bar reaches 100%", async ({
  page,
}) => {
  await reachQuestions(page, "Camera for travel under ₹60,000");
  await expect(
    page.getByText(
      "Sensor size: it usually matters a lot and we don't know your view yet, so we ask now.",
      { exact: false },
    ),
  ).toBeVisible();
  const bar = page.getByRole("progressbar", { name: "Criteria settled" });
  await expect(bar).toHaveAttribute("aria-valuemax", "9");
  const replies = page.getByRole("group", { name: /Quick replies/ });
  const review = page.getByRole("link", { name: /Review your brief/ });
  for (let asked = 1; asked <= 6; asked++) {
    await expect(page.locator("#question-text")).toBeVisible();
    await replies.getByRole("button", { name: "High" }).click();
    if (asked < 6) await expect(bar).toHaveAttribute("aria-valuenow", String(asked));
  }
  // Six answers settle it: the interview ends with three criteria never asked.
  await expect(review).toBeVisible();
  await expect(page.getByText("Thanks, that's everything I need.", { exact: true })).toBeVisible();
  await expect(bar).toHaveAttribute("aria-valuenow", "9");
  await checkRow(page, "questions, finished early");
  if (SHOTS) {
    for (const theme of ["light", "dark"] as Theme[]) {
      await setTheme(page, theme);
      await page.screenshot({
        path: join(SHOTS, `1280-${theme}-questions-finished-early.png`),
        fullPage: true,
      });
    }
    await setTheme(page, "light");
  }
});
