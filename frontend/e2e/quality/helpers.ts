/**
 * Shared steps for the quality specs (FE-4). They drive the real app in mock mode, the same way a shopper
 * would, so every state in the audit matrix is reached through the screens and never faked in storage.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** WCAG 2.0, 2.1 and 2.2, levels A and AA: the tags the quality gate asks for. */
export const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

export const LAPTOP_REQUEST = "Laptop for coding under ₹70,000";

export type Theme = "light" | "dark";

/** Every scenario in the brief page's "Preview scenario" picker (src/lib/results/mockService.ts). */
export const RESULT_SCENARIOS = [
  "success",
  "tie",
  "disputed",
  "unknown",
  "estimated",
  "stale",
  "no-offer",
  "left-out",
  "couldnt-check",
  "partial",
  "relaxation",
  "empty",
] as const;

/** Waits until React has hydrated the element, so typing is not lost to the server-rendered copy. */
export async function waitForHydration(page: Page, selector: string) {
  await page.waitForFunction((sel) => {
    const el = document.querySelector(sel);
    return !!el && Object.keys(el).some((k) => k.startsWith("__reactFiber"));
  }, selector);
}

/** Switches the theme in place, the way the header toggle does, without leaving the current state. */
export async function setTheme(page: Page, theme: Theme) {
  await page.evaluate((t) => {
    document.documentElement.classList.toggle("dark", t === "dark");
    try {
      localStorage.setItem("shopsense.theme", t);
    } catch {
      /* storage blocked: the class alone is enough */
    }
  }, theme);
  // Let colour transitions settle (the specs run with reduced motion, so this is short).
  await page.waitForTimeout(50);
}

export interface AxeRow {
  row: string;
  theme: Theme;
  violations: string[];
}

/** Runs axe with the quality-gate tags and returns one line per violated rule. */
export async function axeViolations(page: Page): Promise<string[]> {
  const { violations } = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  return violations.map(
    (v) =>
      `${v.id} (${v.impact ?? "n/a"}): ${v.help} [${v.nodes
        .slice(0, 3)
        .map((n) => n.target.join(" "))
        .join(" | ")}]`,
  );
}

/**
 * Runs axe while the page clock is paused (to hold a short loading state on screen). axe waits on its own
 * timers, so the clock is nudged forward a millisecond at a time until axe is done.
 */
async function axeWhilePaused(page: Page): Promise<string[]> {
  let done = false;
  let failure: unknown = null;
  let violations: string[] = [];
  const run = axeViolations(page).then(
    (v) => {
      violations = v;
      done = true;
    },
    (e: unknown) => {
      failure = e;
      done = true;
    },
  );
  // 1 ms at a time, at most 150 ms per run, so a 600-700 ms mock wait is still on screen afterwards.
  // axe opens and closes a helper page of its own; a nudge that races with that close is harmless.
  let used = 0;
  for (; used < 150 && !done; used++) await page.clock.runFor(1).catch(() => undefined);
  await run;
  console.log(`[a11y] (axe ran with the clock paused; it needed ${used} ms of page time)`);
  if (failure) throw failure;
  return violations;
}

/**
 * Checks one matrix row in light and dark mode; soft, so every row is reported in one run. Pass
 * `paused: true` when the page clock is paused to hold a loading state.
 */
export async function checkRow(
  page: Page,
  row: string,
  { paused = false, themes = ["light", "dark"] }: { paused?: boolean; themes?: Theme[] } = {},
): Promise<AxeRow[]> {
  const out: AxeRow[] = [];
  for (const theme of themes) {
    await setTheme(page, theme);
    const violations = paused ? await axeWhilePaused(page) : await axeViolations(page);
    console.log(`[a11y] ${row} | ${theme} | ${violations.length ? violations.join("; ") : "pass"}`);
    expect.soft(violations, `axe: ${row} (${theme})`).toEqual([]);
    out.push({ row, theme, violations });
  }
  await setTheme(page, "light");
  return out;
}

/** The request page, submitted, up to "Here's what we understood". */
export async function reachUnderstood(page: Page, request = LAPTOP_REQUEST) {
  // The request page restores its last state from this tab; start from an empty box every time. The
  // saved state is cleared before the page loads, because the page saves it again once it has hydrated.
  if (page.url().startsWith("http")) {
    await page.evaluate(() => sessionStorage.removeItem("shopsense.request.v1"));
  }
  await page.goto("/research");
  await waitForHydration(page, "#request-text");
  await page.getByLabel("Your request").fill(request);
  await page.getByRole("button", { name: /^Continue/ }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Here's what we understood" }),
  ).toBeVisible();
}

/** Confirms the brief and waits for the criteria list; returns the session ID from the address. */
export async function reachCriteria(page: Page, request = LAPTOP_REQUEST): Promise<string> {
  await reachUnderstood(page, request);
  await page.getByRole("button", { name: /Looks right/ }).click();
  await expect(page).toHaveURL(/\/s\/sess_[0-9a-f]{24}\/criteria$/);
  await expect(page.getByRole("button", { name: /Start the questions/ })).toBeEnabled();
  return sessionIdOf(page);
}

export function sessionIdOf(page: Page): string {
  const m = /\/s\/(sess_[0-9a-f]{24})\//.exec(page.url());
  if (!m?.[1]) throw new Error(`No session ID in ${page.url()}`);
  return m[1];
}

/** Opens the questions and waits for the first question. */
export async function reachQuestions(page: Page, request = LAPTOP_REQUEST): Promise<string> {
  const id = await reachCriteria(page, request);
  await page.getByRole("button", { name: /Start the questions/ }).click();
  await expect(page).toHaveURL(/\/questions$/);
  await expect(page.getByRole("group", { name: /Quick replies/ })).toBeVisible();
  return id;
}

/** Answers every question with "High" until the brief is ready. */
export async function answerAll(page: Page) {
  const review = page.getByRole("link", { name: /Review your brief/ });
  const replies = page.getByRole("group", { name: /Quick replies/ });
  for (let i = 0; i < 12 && !(await review.isVisible()); i++) {
    await replies.getByRole("button", { name: /High/ }).click();
    await expect(review.or(replies.getByRole("button", { name: /High/ }))).toBeEnabled();
  }
  await expect(review).toBeVisible();
}

/** Goes all the way to the brief page with every question answered; returns the session ID. */
export async function reachBrief(page: Page, request = LAPTOP_REQUEST): Promise<string> {
  const id = await reachQuestions(page, request);
  await answerAll(page);
  await page.getByRole("link", { name: /Review your brief/ }).click();
  await expect(page).toHaveURL(/\/brief$/);
  await expect(page.getByRole("button", { name: /Start research/ })).toBeEnabled();
  return id;
}

/**
 * Starts mock research for one preview scenario from the brief page. The page clock must be installed
 * (page.clock.install()) before the first page load so the run can be fast-forwarded.
 */
export async function startScenario(page: Page, sessionId: string, scenario: string) {
  if (!page.url().endsWith(`/s/${sessionId}/brief`)) {
    await page.goto(`/s/${sessionId}/brief`);
  }
  await expect(page.getByRole("button", { name: /Start research/ })).toBeEnabled();
  await page.getByLabel(/Preview scenario/).selectOption(scenario);
  await page.getByRole("button", { name: /Start research/ }).click();
  await expect(page).toHaveURL(/\/results$/);
}

/** Fast-forwards a mock research run (4 stages of 1.4 s, then 0.5 s) to its result. */
export async function finishResearch(page: Page) {
  await page.clock.runFor(7_000);
}
