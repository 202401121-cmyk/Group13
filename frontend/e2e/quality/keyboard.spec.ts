/**
 * Check 2 of the quality gate: keyboard only. One shopper goes from the request to the results with Tab,
 * Shift+Tab, Enter, Space, Escape, the arrow keys and the number keys. On the way, every focus stop must show
 * a focus indicator and must not sit under the sticky header or the fixed composer (WCAG 2.4.7, 2.4.11).
 * Escape closes every overlay and gives focus back; Tab never gets stuck (2.1.2).
 */
import { expect, test, type Page } from "@playwright/test";
import { focusStop, tabTo, walkFocus, type FocusStop } from "./layout";
import {
  finishResearch,
  LAPTOP_REQUEST,
  reachBrief,
  reachQuestions,
  reachUnderstood,
  waitForHydration,
} from "./helpers";

function problems(stops: FocusStop[]): string[] {
  return stops.flatMap((s) => [
    ...(s.indicator ? [] : [`${s.name}: no visible focus indicator`]),
    ...(s.obscured === "no" ? [] : [`${s.name}: ${s.obscured} hidden under a sticky bar`]),
    ...(s.inView ? [] : [`${s.name}: focused but off screen`]),
  ]);
}

/** Tabs forwards and then backwards through the whole page, checking every stop. */
async function walkBothWays(page: Page, label: string) {
  const forward = await walkFocus(page, "Tab");
  expect.soft(forward.escaped, `${label}: Tab should leave the page, never get stuck`).toBe(true);
  const backward = await walkFocus(page, "Shift+Tab");
  expect.soft(backward.escaped, `${label}: Shift+Tab should leave the page`).toBe(true);
  const found = [...new Set([...problems(forward.stops), ...problems(backward.stops)])];
  console.log(
    `[keyboard] ${label}: ${forward.stops.length} stops, ${found.length ? found.join("; ") : "pass"}`,
  );
  expect.soft(found, `${label}: focus problems`).toEqual([]);
}

test.describe("keyboard only", () => {
  // The tests are independent (each starts its own search), so they can run side by side.
  test.describe.configure({ mode: "parallel", timeout: 180_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  test("a shopper goes from the request to the results without a mouse", async ({ page }) => {
    const seen: FocusStop[] = [];
    const tab = async (match: RegExp) => {
      const stop = await tabTo(page, (name) => match.test(name));
      seen.push(stop);
      return stop;
    };

    await page.goto("/");
    await waitForHydration(page, "#research-query");
    await tab(/#research-query/);
    await page.keyboard.type(LAPTOP_REQUEST);
    await tab(/Start my research/);
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/research$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Here's what we understood" }),
    ).toBeFocused();
    await tab(/Looks right/);
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/criteria$/);
    await expect(page.getByRole("button", { name: /Start the questions/ })).toBeEnabled();
    await tab(/Start the questions/);
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/questions$/);
    const review = page.getByRole("link", { name: /Review your brief/ });
    const replies = page.getByRole("group", { name: /Quick replies/ });
    await expect(replies).toBeVisible();
    for (let i = 0; i < 12 && !(await review.isVisible()); i++) {
      await expect(replies.getByRole("button").first()).toBeEnabled();
      await page.keyboard.press("3"); // the number keys pick a quick reply: 3 is "High"
      await expect(review.or(replies.getByRole("button").first())).toBeEnabled();
    }
    await expect(review).toBeVisible();
    await tab(/Review your brief/);
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/brief$/);
    await tab(/#scenario/);
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowUp");
    await expect(page.locator("#scenario")).toHaveValue("success");
    await tab(/Start research/);
    await page.keyboard.press("Space");

    await expect(page).toHaveURL(/\/results$/);
    await finishResearch(page);
    const h1 = page.getByRole("heading", { level: 1, name: /Results for your/ });
    await expect(h1).toBeFocused();

    const found = problems(seen);
    console.log(`[keyboard] request to results: ${found.length ? found.join("; ") : "pass"}`);
    expect.soft(found).toEqual([]);

    // The results page, the longest screen, both ways.
    await walkBothWays(page, "results page (1280 px)");
  });

  for (const size of [
    { width: 1280, height: 720 },
    { width: 390, height: 844 },
    { width: 844, height: 390 },
  ]) {
    const px = `${size.width}x${size.height}`;
    test(`focus is visible and never hidden on each screen at ${px}`, async ({ page }) => {
      await page.setViewportSize(size);
      await page.goto("/");
      await walkBothWays(page, `home (${px})`);
      await page.goto("/privacy");
      await walkBothWays(page, `privacy (${px})`);
      await reachUnderstood(page);
      await walkBothWays(page, `what we understood (${px})`);
      await reachQuestions(page);
      await walkBothWays(page, `questions (${px})`);
    });
  }

  test("escape closes every overlay and gives focus back", async ({ page }) => {
    // The inline editor on "what we understood".
    await reachUnderstood(page);
    await tabTo(page, (n) => /Edit budget/.test(n));
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("From")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByLabel("From")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit budget" })).toBeFocused();

    // The menu on small screens.
    await page.setViewportSize({ width: 390, height: 844 });
    await tabTo(page, (n) => /Open menu/.test(n));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Close menu" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
    await page.setViewportSize({ width: 1280, height: 720 });

    // The "Stop research?" dialog when leaving the results page while research runs.
    await reachBrief(page);
    await tabTo(page, (n) => /Start research/.test(n));
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/results$/);
    await page.clock.runFor(1_500);
    const home = page.getByRole("navigation", { name: "Main" }).getByRole("link", {
      name: "Home",
      exact: true,
    });
    await home.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("alertdialog", { name: "Stop research?" });
    await expect(dialog).toBeVisible();
    await expect(page.getByRole("button", { name: "Keep researching" })).toBeFocused();
    // Tab stays inside the dialog while it is open.
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press("Tab");
      const inside = await dialog.evaluate((d) => d.contains(document.activeElement));
      expect(inside, "focus stays in the dialog").toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(/\/results$/);
    await expect(home).toBeFocused();
    const stop = await focusStop(page);
    expect(stop.isBody).toBe(false);
    // Research carried on.
    await expect(page.getByRole("heading", { name: /What ShopSense is doing/ })).toBeVisible();
  });
});
