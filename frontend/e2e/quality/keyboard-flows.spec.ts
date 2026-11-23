/**
 * US-33 (S5-12 DECIDED 3): the interview, with a remembered answer's Change and Forget (US-06), the sliders, the
 * compare view and the memory page, each with the keyboard alone, in light and dark mode. Every focus stop on the way
 * keeps a visible indicator and never sits under a sticky bar, and every slider has a number box (DECIDED 4).
 * Mock mode, made-up requests only.
 */
import { expect, test, type Page } from "@playwright/test";
import {
  finishResearch,
  reachBrief,
  reachQuestions,
  signInTo,
  startScenario,
  type Theme,
} from "./helpers";
import { focusStop, walkFocus, type FocusStop } from "./layout";

const THEMES: Theme[] = ["light", "dark"];
const BATTERY = "Battery life: high importance";

/** The theme the app starts in, from its own setting, so every page loads in it. */
async function inTheme(page: Page, theme: Theme) {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("shopsense.theme", t);
    } catch {
      /* storage blocked */
    }
  }, theme);
}

function problems(stops: FocusStop[]): string[] {
  return stops.flatMap((s) => [
    ...(s.indicator ? [] : [`${s.name}: no visible focus indicator`]),
    ...(s.obscured === "no" ? [] : [`${s.name}: ${s.obscured} hidden under a sticky bar`]),
    ...(s.inView ? [] : [`${s.name}: focused but off screen`]),
  ]);
}

/** Presses a key until focus is on an element whose name matches, checking that stop; fails if never reached. */
async function focusBy(
  page: Page,
  matches: (name: string) => boolean,
  key: "Tab" | "Shift+Tab" = "Tab",
) {
  for (let i = 0; i < 150; i++) {
    await page.keyboard.press(key);
    const stop = await focusStop(page);
    if (matches(stop.name)) {
      expect(problems([stop]), stop.name).toEqual([]);
      return stop;
    }
  }
  throw new Error(`${key} never reached the element`);
}

/** Every stop on the page, forwards: each visible, none hidden, and Tab never stuck. */
async function everyStop(page: Page, label: string) {
  const walk = await walkFocus(page, "Tab");
  expect.soft(walk.escaped, `${label}: Tab should leave the page`).toBe(true);
  expect.soft([...new Set(problems(walk.stops))], `${label}: focus problems`).toEqual([]);
}

/** Every slider on the page has a number box beside it (US-33). */
async function slidersWithoutBox(page: Page): Promise<number> {
  return page.evaluate(() => {
    let missing = 0;
    for (const range of document.querySelectorAll('input[type="range"]')) {
      let box: Element | null = null;
      for (let up = range.parentElement, i = 0; up && i < 4 && !box; up = up.parentElement, i++)
        box = up.querySelector('input[type="number"]');
      if (!box) missing++;
    }
    return missing;
  });
}

/** Three questions answered with the number keys; the laptop's weight is remembered next (the mock's memory). */
async function toRemembered(page: Page) {
  await reachQuestions(page);
  for (let i = 0; i < 3; i++) {
    const replies = page.getByRole("group", { name: /Quick replies/ });
    await expect(replies.getByRole("button", { name: "High" })).toBeEnabled();
    await page.locator("body").focus();
    await page.keyboard.press("3"); // High
    await expect(page.getByRole("group", { name: /Quick replies/ })).toBeVisible();
  }
  await expect(page.locator("[data-remembered-from]")).toHaveText(
    /^Remembered from 14 Nov( 2026)?$/,
  );
}

test.describe("keyboard only, light and dark", () => {
  test.describe.configure({ mode: "parallel", timeout: 240_000 });

  for (const theme of THEMES) {
    test(`interview: a remembered answer forgotten with the keyboard stays gone after a reload (${theme})`, async ({
      page,
    }) => {
      await inTheme(page, theme);
      await toRemembered(page);
      await everyStop(page, `questions with a remembered answer (${theme})`);
      await page.locator("body").focus();
      await focusBy(page, (n) => n.includes('"Forget Weight"'));
      await page.keyboard.press("Enter");
      await expect(
        page.getByText("Forgotten: Weight. We won't use it again.", { exact: true }),
      ).toBeVisible();
      await expect(page.locator("#current-question")).toBeFocused();
      await expect(page.locator("#question-text")).toContainText("Weight");
      await expect(page.locator("[data-remembered-from]")).toHaveCount(0);
      await page.reload();
      await expect(page.locator("#question-text")).toContainText("Weight");
      await expect(page.locator("[data-remembered-from]")).toHaveCount(0);
    });

    test(`interview: a remembered answer changed with the keyboard is the shopper's own on the brief (${theme})`, async ({
      page,
    }) => {
      await inTheme(page, theme);
      await toRemembered(page);
      await page.locator("body").focus();
      await focusBy(page, (n) => n.includes('"Change Weight"'));
      await page.keyboard.press("Enter");
      await expect(page.locator("#question-text")).toContainText("Weight");
      await page.keyboard.press("1"); // Low
      await expect(page.locator("[data-remembered-from]")).toHaveCount(0);
      // The rest answered the same way, then the brief: Weight is the shopper's answer, no longer remembered.
      const review = page.getByRole("link", { name: /Review your brief/ });
      for (let i = 0; i < 8 && !(await review.isVisible()); i++) {
        await page.locator("body").focus();
        await page.keyboard.press("3");
        await page.waitForTimeout(50);
      }
      await review.click();
      await expect(page).toHaveURL(/\/brief$/);
      const weightRow = page.locator("li", { hasText: /^Weight/ }).first();
      await expect(weightRow).not.toContainText("Remembered");
    });

    test(`sliders: the brief's and "Change what matters", each with a number box (${theme})`, async ({
      page,
    }) => {
      await page.clock.install();
      await inTheme(page, theme);
      const id = await reachBrief(page);
      expect(await slidersWithoutBox(page), "sliders without a number box on the brief").toBe(0);
      const first = page.locator('input[type="range"]').first();
      const before = await first.inputValue();
      await page.locator("body").focus();
      const sliderId = await first.getAttribute("id");
      await focusBy(page, (n) => n.startsWith(`input#${sliderId}`));
      await page.keyboard.press(Number(before) >= 10 ? "ArrowLeft" : "ArrowRight");
      await expect(first).not.toHaveValue(before);

      await startScenario(page, id, "success");
      await finishResearch(page);
      await page.locator("body").focus();
      await focusBy(page, (n) => n.includes('"Change what matters"'));
      await page.keyboard.press("Enter");
      await expect(page.getByLabel(/, slide from 0 to 10$/).first()).toBeVisible();
      expect(await slidersWithoutBox(page), "sliders without a number box in the panel").toBe(0);
      const last = page.getByLabel(/, slide from 0 to 10$/).last();
      const lastId = await last.getAttribute("id");
      await focusBy(page, (n) => n.startsWith(`input#${lastId}`));
      await page.keyboard.press("End");
      await expect(
        page.getByText("Confidence was worked out for your original priorities."),
      ).toBeVisible();
    });

    test(`compare view, keyboard only (${theme})`, async ({ page }) => {
      await page.clock.install();
      await inTheme(page, theme);
      const id = await reachBrief(page);
      await startScenario(page, id, "success");
      await finishResearch(page);
      await page.locator("body").focus();
      for (const name of ["Sample laptop A", "Sample laptop B"]) {
        await focusBy(page, (n) => n.includes(`"Compare ${name}"`));
        await page.keyboard.press("Space");
      }
      await focusBy(page, (n) => n.includes('"Compare (2)"'), "Shift+Tab");
      await page.keyboard.press("Enter");
      await expect(page.getByRole("heading", { name: "Compare 2 products" })).toBeFocused();
      await focusBy(page, (n) => n.startsWith("div"));
      await expect(page.getByRole("region", { name: "Comparison table" })).toBeFocused();
      await focusBy(page, (n) => n.includes('"Close comparison"'), "Shift+Tab");
      await page.keyboard.press("Enter");
      await expect(page.getByRole("button", { name: "Compare (2)" })).toBeFocused();
    });

    test(`memory page: Forget and Undo, keyboard only (${theme})`, async ({ page }) => {
      await inTheme(page, theme);
      await signInTo(page, "/account");
      await everyStop(page, `memory page (${theme})`);
      await page.locator("body").focus();
      await focusBy(page, (n) => n.includes(`Forget "${BATTERY}"`));
      await page.keyboard.press("Enter");
      const undo = page.getByRole("button", { name: `Undo: keep "${BATTERY}"` });
      await expect(undo).toBeVisible();
      await expect(undo).toBeFocused(); // the page moves focus to Undo itself
      await page.keyboard.press("Enter");
      await expect(page.getByRole("button", { name: `Forget "${BATTERY}"` })).toBeFocused();
    });
  }
});
