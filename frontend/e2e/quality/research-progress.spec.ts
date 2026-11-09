/**
 * The research screen in mock mode (US-12): a scripted run plays every state, so all of them can be seen and
 * checked without the backend. Set SHOTS_DIR to a folder outside the repository to save a screenshot of each
 * state, at each width, in each theme.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  LAPTOP_REQUEST,
  RESULTS_URL,
  checkRow,
  reachBrief,
  setTheme,
  startScenario,
  type Theme,
} from "./helpers";
import { smallTargets } from "./layout";

const WITH_SEARCH_ID = /\/results\?search=srch_[0-9a-f]{24}$/;
const UNKNOWN = `srch_${"0".repeat(24)}`;
const SHOTS = process.env["SHOTS_DIR"];

const stageRow = (page: Page, label: string) =>
  page.getByRole("listitem").filter({ hasText: label });
const FINDING = "Finding reviews, videos and discussions";
const READING = "Reading what each source says";
const WORKING_OUT = "Working out which products they mean";
const FILLING_IN = "Filling in missing details";
const SCORING = "Scoring the products on what matters to you";
const PRICES = "Checking prices and where to buy";

const stopDialog = (page: Page) => page.getByRole("alertdialog", { name: "Stop research?" });
const confirmStop = (page: Page) =>
  stopDialog(page).getByRole("button", { name: "Stop research" }).click();
const pageStopButton = (page: Page) => page.getByRole("button", { name: "Stop research" });
const heading = (page: Page, name: string | RegExp) =>
  page.getByRole("heading", { level: 1, name });

async function pauseClock(page: Page) {
  const now = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(now + 250);
}

/** From the brief, with the clock held, so each moment of the run stays on screen until moved on. */
async function startHeld(page: Page, sessionId: string, scenario: string) {
  await page.clock.resume();
  if (!page.url().endsWith(`/s/${sessionId}/brief`)) await page.goto(`/s/${sessionId}/brief`);
  await expect(page.getByRole("button", { name: /Start research/ })).toBeEnabled();
  await page.getByLabel(/Preview scenario/).selectOption(scenario);
  await pauseClock(page);
  await page.getByRole("button", { name: /Start research/ }).click();
  await page.clock.runFor(100); // the brief page opens the results page one frame after the tap
  await expect(page).toHaveURL(RESULTS_URL);
}

type Check = (name: string, options?: { paused?: boolean }) => Promise<void>;

/** Walks through every state of the research screen that mock mode can reach, checking each one. */
async function everyState(page: Page, check: Check) {
  const id = await reachBrief(page);

  await startHeld(page, id, "success");
  await expect(page.getByText("Starting your search")).toBeVisible();
  await check("starting", { paused: true });

  await page.clock.runFor(1_000);
  await expect(stageRow(page, READING).getByText("Running")).toBeVisible();
  await expect(stageRow(page, FINDING).getByText("Done")).toBeVisible();
  await expect(page.getByText("Review sites")).toBeVisible();
  await check("running, with source counts", { paused: true });

  await page.clock.runFor(3_000);
  await expect(stageRow(page, FILLING_IN).getByText("Skipped")).toBeVisible();
  await check("a skipped stage with its reason", { paused: true });

  await pageStopButton(page).click();
  await expect(stopDialog(page)).toBeVisible();
  await check("Stop research? dialog", { paused: true });

  await confirmStop(page);
  await expect(page.getByText("Stopping your search").first()).toBeVisible();
  await check("stopping", { paused: true });

  await page.clock.runFor(500);
  await expect(heading(page, "Search stopped")).toBeVisible();
  await page.clock.resume();
  await check("search stopped");

  await page.goto(`/s/${id}/results?search=${UNKNOWN}`);
  await expect(heading(page, "We can't find that search")).toBeVisible();
  await check("can't find that search");

  await startHeld(page, id, "stage-failed");
  await page.clock.runFor(6_250);
  await expect(stageRow(page, PRICES).getByText("Failed")).toBeVisible();
  await check("a failed stage with its reason", { paused: true });

  await startHeld(page, id, "connection-lost");
  await page.clock.runFor(1_000);
  await page.clock.runFor(61_000); // 60 seconds after the last event, which comes at about 1.8 s
  await expect(page.getByRole("button", { name: "Check for results" })).toBeVisible();
  await check("quiet for 60 seconds", { paused: true });

  await startHeld(page, id, "timeout");
  await page.clock.runFor(1_000);
  await page.clock.runFor(61_000); // 60 seconds after the last event, which comes at about 1.8 s
  await page.getByRole("button", { name: "Check for results" }).click();
  await expect(page.getByText("Checking for results.").first()).toBeVisible();
  await check("checking for results", { paused: true });

  // A reload with the clock held: the run is being read again.
  await page.reload();
  // Hydrating needs the page's own timers, so the clock moves a little at a time until the read has begun.
  await expect(async () => {
    await page.clock.runFor(20);
    await expect(page.getByText("Picking your search up again")).toBeVisible({ timeout: 100 });
  }).toPass();
  await check("re-attaching", { paused: true });

  await page.clock.runFor(110_000);
  await expect(page.getByText(/took too long/).first()).toBeVisible();
  await page.clock.resume();
  await check("timed out");

  await startHeld(page, id, "network-retry");
  await page.clock.runFor(2_000);
  await expect(page.getByText(/lost the connection/).first()).toBeVisible();
  await page.clock.resume();
  await check("lost connection");

  await startHeld(page, id, "invalid-event");
  await page.clock.runFor(2_000);
  await expect(page.getByText(/Something went wrong while researching/).first()).toBeVisible();
  await page.clock.resume();
  await check("clear error, unreadable progress");

  await startHeld(page, id, "busy");
  await page.clock.runFor(400);
  await expect(page.getByText(/busy with other searches/).first()).toBeVisible();
  await page.clock.resume();
  await check("busy");

  await startHeld(page, id, "stop-too-late");
  await page.clock.runFor(1_000);
  await pageStopButton(page).click();
  await expect(stopDialog(page)).toBeVisible();
  await confirmStop(page);
  await expect(page.getByText(/too late to stop it/).first()).toBeVisible();
  await check("a stop that came too late", { paused: true });

  await page.clock.runFor(6_000);
  await expect(heading(page, /Results for your/)).toBeVisible();
  await page.clock.resume();
  await expect(page.getByText(/had nearly finished when you stopped it/).first()).toBeVisible();
  await check("results after a stop that came too late");

  await page.goto(`/s/${id}/results`);
  await expect(heading(page, "These results aren't here any more")).toBeVisible();
  await check("no search to show");
}

async function screenshot(page: Page, width: number, name: string, themes: Theme[]) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  const file = name.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
  for (const theme of themes) {
    await setTheme(page, theme);
    await page.screenshot({
      path: join(SHOTS, `${width}-${theme}-${file}.png`),
      fullPage: true,
      animations: "disabled",
      timeout: 10_000,
    });
  }
  await setTheme(page, "light");
}

test.describe("the research screen", () => {
  test.describe.configure({ mode: "parallel", timeout: 180_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  test("a mock run shows every stage state and its counts", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "stage-failed");
    await expect(page.getByText("Starting your search")).toBeVisible();

    await page.clock.runFor(250);
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    await expect(stageRow(page, FINDING).getByText("Running")).toBeVisible();
    await expect(stageRow(page, READING).getByText("Waiting")).toBeVisible();
    await expect(page.getByText("Counted once ShopSense has finished looking.")).toBeVisible();

    await page.clock.runFor(800);
    await expect(stageRow(page, FINDING).getByText("Done")).toBeVisible();
    await expect(stageRow(page, FINDING).getByText("Found 50 sources.")).toBeVisible();
    await expect(stageRow(page, READING).getByText("Running")).toBeVisible();
    await expect(stageRow(page, WORKING_OUT).getByText("Waiting")).toBeVisible();
    const counts = page.locator("h3", { hasText: "Sources found" }).locator("..");
    await expect(counts).toContainText("Reddit 20");
    await expect(counts).toContainText("Web pages 20");
    await expect(counts).toContainText("Review sites 10");
    await expect(counts).not.toContainText("Videos");

    await page.clock.runFor(3_000);
    const skipped = stageRow(page, FILLING_IN);
    await expect(skipped.getByText("Skipped")).toBeVisible();
    await expect(skipped.getByText("took too long, so a simpler way was used")).toBeVisible();
    await expect(stageRow(page, SCORING).getByText("Running")).toBeVisible();

    await page.clock.runFor(2_200);
    const failed = stageRow(page, PRICES);
    await expect(failed.getByText("Failed")).toBeVisible();
    await expect(failed.getByText("some prices may be missing")).toBeVisible();

    await page.clock.runFor(600);
    await expect(heading(page, /Results for your/)).toBeVisible();
    await expect(heading(page, /Results for your/)).toBeFocused();
    await expect(page.getByText(/only two of the stores/)).toBeVisible();
  });

  test("a screen reader hears stage changes, not the clock", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "success");
    const live = page.locator('section [aria-live="polite"]').first();

    await page.clock.runFor(1_000);
    await expect(live).toHaveText(`${READING}: running.`);
    await page.clock.runFor(500); // the elapsed time ticks on, but no stage changes
    await expect(live).toHaveText(`${READING}: running.`);
    await page.clock.runFor(300);
    await expect(live).toHaveText(`${WORKING_OUT}: running.`);
  });

  test("a reload mid-run re-attaches", async ({ page }) => {
    const id = await reachBrief(page);
    await startScenario(page, id, "timeout");
    await expect(stageRow(page, READING).getByText("Done")).toBeVisible();
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    const before = page.url();

    await page.reload();
    await expect(stageRow(page, READING).getByText("Done")).toBeVisible();
    await expect(stageRow(page, WORKING_OUT).getByText("Running")).toBeVisible();
    expect(page.url(), "the same search").toBe(before);

    // The limit counts from when the run began. It began over 1.5 s ago, so a reload that restarted the limit
    // would still have time left here.
    await page.clock.runFor(168_500);
    await expect(page.getByText(/took too long/).first()).toBeVisible();
    expect(page.url(), "still the same search").toBe(before);
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(stageRow(page, FINDING)).toBeVisible();
  });

  test("a reload after the end shows the same result", async ({ page }) => {
    const id = await reachBrief(page);
    await startScenario(page, id, "success");
    await page.clock.runFor(7_000);
    await expect(heading(page, /Results for your/)).toBeVisible();
    await expect(page.getByText(/Sample laptop A/).first()).toBeVisible();
    const before = page.url();
    expect(before).toMatch(WITH_SEARCH_ID);

    await page.reload();
    await expect(heading(page, /Results for your/)).toBeVisible();
    await expect(page.getByText(/Sample laptop A/).first()).toBeVisible();
    expect(page.url()).toBe(before);
  });

  test("Stop research asks first, then shows Search stopped", async ({ page }) => {
    const id = await reachBrief(page);
    await startScenario(page, id, "timeout");
    await expect(pageStopButton(page)).toBeVisible();

    await pageStopButton(page).click();
    await expect(stopDialog(page)).toBeVisible();
    await expect(stopDialog(page)).toContainText("This ends the search.");
    await page.getByRole("button", { name: "Keep researching" }).click();
    await expect(stopDialog(page)).toBeHidden();
    await expect(pageStopButton(page)).toBeFocused();

    await pageStopButton(page).click();
    await confirmStop(page);
    await expect(heading(page, "Search stopped")).toBeVisible();
    await expect(heading(page, "Search stopped")).toBeFocused();
    await expect(page).toHaveURL(WITH_SEARCH_ID);

    // A reload shows the same outcome, with nothing started again.
    await page.reload();
    await expect(heading(page, "Search stopped")).toBeVisible();

    await page.getByRole("link", { name: "Back to your brief" }).first().click();
    await expect(page).toHaveURL(/\/brief$/);
    await expect(page.getByRole("button", { name: /Start research/ })).toBeEnabled();
  });

  test("leaving the page asks first, then stops the search", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const id = await reachBrief(page);
    await startScenario(page, id, "timeout");
    await expect(pageStopButton(page)).toBeVisible();
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Home", exact: true })
      .click();
    await expect(stopDialog(page)).toContainText("Leaving this page stops the research.");
    await confirmStop(page);
    await expect(page).not.toHaveURL(RESULTS_URL);
    await expect(page.getByText("Search stopped").first()).toBeVisible();

    // The run was let go on the way out: coming back reads it, and it has stopped.
    await page.goBack();
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    await expect(heading(page, "Search stopped")).toBeVisible();
  });

  test("going back to the brief lets the run go, and a new search starts clean", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "network-retry");
    await page.clock.runFor(2_000);
    await expect(page.getByText(/lost the connection/).first()).toBeVisible();
    const first = page.url();
    await page.clock.resume();
    await page.getByRole("button", { name: "Back to your brief" }).click();
    await expect(page).toHaveURL(/\/brief$/);

    await page.getByRole("button", { name: /Start research/ }).click();
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    expect(page.url(), "a new search, not the old one again").not.toBe(first);
    await page.clock.runFor(7_000);
    await expect(heading(page, /Results for your/)).toBeVisible();
  });

  test("a stop that comes too late shows the results, and says why", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "stop-too-late");
    await page.clock.runFor(1_000);
    await pageStopButton(page).click();
    await confirmStop(page);
    await expect(page.getByText(/too late to stop it/).first()).toBeVisible();
    await expect(pageStopButton(page)).toBeHidden();
    await page.clock.runFor(6_000);
    await expect(heading(page, /Results for your/)).toBeVisible();
    await expect(page.getByText(/had nearly finished when you stopped it/).first()).toBeVisible();
  });

  test("an unknown search ID shows a way back", async ({ page }) => {
    const id = await reachBrief(page);
    for (const search of [UNKNOWN, "not-a-search-id", "..%2F..%2Fetc", "12345"]) {
      await page.goto(`/s/${id}/results?search=${search}`);
      await expect(heading(page, "We can't find that search")).toBeVisible();
    }
    await page.getByRole("link", { name: "Back to your brief" }).first().click();
    await expect(page).toHaveURL(/\/brief$/);
  });

  test("60 quiet seconds say so, and checking shows what was missed", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "connection-lost");
    await page.clock.runFor(1_500);
    await expect(stageRow(page, READING).getByText("Running")).toBeVisible();
    await page.clock.runFor(61_000); // 60 seconds after the last event, which comes at about 1.8 s
    await expect(page.getByText(/haven't heard from ShopSense for a minute/).first()).toBeVisible();
    await expect(stageRow(page, FINDING).getByText("Done"), "progress stays").toBeVisible();
    await page.getByRole("button", { name: "Check for results" }).click();
    await page.clock.runFor(400);
    await expect(heading(page, /Results for your/)).toBeVisible();
  });

  test("a retry after a timeout keeps the brief", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "timeout");
    await page.clock.runFor(1_000);
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    const old = page.url();
    await page.clock.runFor(170_000);
    await expect(page.getByText(/took too long/).first()).toBeVisible();
    await expect(page.getByText(/Your brief is safe/).first()).toBeVisible();
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByText("Starting your search")).toBeVisible();
    await page.clock.runFor(400);
    await expect(stageRow(page, FINDING).getByText("Running")).toBeVisible();
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    expect(page.url(), "a new search").not.toBe(old);
    // The brief is the one the shopper wrote: it is still in the summary beside the new search.
    await page.getByText("Your brief", { exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Your brief" })).toContainText(
      "Budget: Up to ₹70,000",
    );
  });

  test("Back while a search starts, then Keep researching, keeps the address right", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "timeout");
    await page.goBack();
    await expect(stopDialog(page)).toBeVisible();
    await page.clock.runFor(500); // the run gets its ID while the question is open
    await page.getByRole("button", { name: "Keep researching" }).click();
    await page.clock.resume();
    await expect(stopDialog(page)).toBeHidden();
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    await page.reload();
    await expect(stageRow(page, FINDING).getByText("Done")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe(`/s/${id}/results`);

    // The brief's own entry in the history is still there, behind this page.
    await pageStopButton(page).click();
    await confirmStop(page);
    await expect(heading(page, "Search stopped")).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/s/${id}/brief$`));
  });

  test("Back while a search starts, then Stop research, lands on the brief", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "timeout");
    await page.goBack();
    await expect(stopDialog(page)).toBeVisible();
    await page.clock.runFor(500);
    await confirmStop(page);
    await page.clock.resume();
    await expect(page).toHaveURL(new RegExp(`/s/${id}/brief$`));
    await expect(page.getByRole("button", { name: /Start research/ })).toBeEnabled();
  });

  test("a stop on a quiet connection can be checked, and shows it stopped", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "connection-lost");
    await page.clock.runFor(2_000); // past the point where the stream goes silent
    await pageStopButton(page).click();
    await confirmStop(page);
    await expect(page.getByText("Stopping your search").first()).toBeVisible();
    await page.clock.runFor(1_000);
    await expect(page.getByText("Stopping your search").first()).toBeVisible();
    await page.clock.runFor(60_000);
    await page.getByRole("button", { name: "Check for results" }).click();
    await page.clock.runFor(400);
    await expect(heading(page, "Search stopped")).toBeVisible();
  });

  test("a screen reader hears a stop go in, and what became of it", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "stop-too-late");
    await page.clock.runFor(1_000);
    const live = page.locator('section [aria-live="polite"]').first();
    await live.evaluate((el) => el.setAttribute("data-same-region", "yes"));
    await pageStopButton(page).click();
    await confirmStop(page);
    await expect(live).toHaveText(/too late to stop it/);
    await expect(live, "the same region, so the change is read out").toHaveAttribute(
      "data-same-region",
      "yes",
    );
  });

  test("a question left open when time runs out doesn't come back after a retry", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "timeout");
    await page.clock.runFor(1_000);
    await pageStopButton(page).click();
    await expect(stopDialog(page)).toBeVisible();
    await page.clock.runFor(170_000);
    await expect(page.getByText(/took too long/).first()).toBeVisible();
    await expect(stopDialog(page)).toBeHidden();
    await page.getByRole("button", { name: "Try again" }).click();
    await page.clock.runFor(400);
    await expect(stageRow(page, FINDING).getByText("Running")).toBeVisible();
    await expect(stopDialog(page)).toBeHidden();
  });

  test("a search that ends behind the leave question shows its results", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const id = await reachBrief(page);
    await startHeld(page, id, "success");
    await page.clock.runFor(1_000);
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Home", exact: true })
      .click();
    await expect(stopDialog(page)).toBeVisible();
    await page.clock.runFor(6_000);
    await expect(heading(page, /Results for your/)).toBeVisible();
    await expect(stopDialog(page)).toBeHidden();
    await expect(page).toHaveURL(WITH_SEARCH_ID);
  });

  test("a new search can start while the old one is still stopping", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "timeout");
    await page.clock.runFor(1_000);
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    const old = page.url();
    await pageStopButton(page).click();
    await confirmStop(page);
    await expect(page.getByText("Stopping your search").first()).toBeVisible();
    await page.getByText("Your brief", { exact: true }).click();
    await page.getByRole("complementary", { name: "Your brief" }).getByRole("link").click();
    await expect(page).toHaveURL(/\/brief$/);
    await page.getByRole("button", { name: /Start research/ }).click();
    await page.clock.runFor(500);
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    expect(page.url(), "a new search").not.toBe(old);
    await expect(stageRow(page, FINDING).getByText("Running")).toBeVisible();
  });

  test("if the question can't be loaded, Stop and leaving still work", async ({ page }) => {
    await page.route(/StopResearchDialog.*\.js/, (route) => route.abort());
    await page.setViewportSize({ width: 1440, height: 900 });
    const id = await reachBrief(page);
    await startScenario(page, id, "timeout");
    await expect(pageStopButton(page)).toBeVisible();
    await pageStopButton(page).click();
    await expect(heading(page, "Search stopped")).toBeVisible();

    await startScenario(page, id, "timeout");
    await expect(pageStopButton(page)).toBeVisible();
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Home", exact: true })
      .click();
    await expect(page).not.toHaveURL(RESULTS_URL);
    await expect(page.getByText("Search stopped").first()).toBeVisible();
  });

  test("a lost connection's Try again picks the same search up", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "network-retry");
    await page.clock.runFor(2_000);
    await expect(page.getByText(/lost the connection/).first()).toBeVisible();
    const before = page.url();
    await page.getByRole("button", { name: "Try again" }).click();
    await page.clock.runFor(400);
    await expect(stageRow(page, FINDING).getByText("Done")).toBeVisible();
    expect(page.url(), "the same search").toBe(before);
    await page.clock.runFor(6_000);
    await expect(heading(page, /Results for your/)).toBeVisible();
    expect(page.url(), "still the same search").toBe(before);
  });

  test("a busy backend's own message shows, with a way to try again", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "busy");
    await page.clock.runFor(400);
    await expect(page.getByText(/busy with other searches right now/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  test("a failed run shows the clear error", async ({ page }) => {
    const id = await reachBrief(page);
    await startHeld(page, id, "run-failed");
    await page.clock.runFor(3_500);
    await expect(page.getByText(/Something went wrong while researching/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  test("the URL never holds the shopper's words", async ({ page }) => {
    const id = await reachBrief(page);
    // Only whole words: "000" from the price could turn up in an ID by chance.
    const words = LAPTOP_REQUEST.toLowerCase()
      .split(/[\s,]+/)
      .filter((word) => /^[a-z]+$/.test(word));
    expect(words).toContain("laptop");
    await startHeld(page, id, "success");
    await page.clock.runFor(500);
    await expect(page).toHaveURL(WITH_SEARCH_ID);
    await page.clock.runFor(7_000);
    await expect(heading(page, /Results for your/)).toBeVisible();
    const { pathname, search } = new URL(page.url());
    expect(pathname).toBe(`/s/${id}/results`);
    expect(search).toMatch(/^\?search=srch_[0-9a-f]{24}$/);
    for (const word of words) expect(page.url().toLowerCase()).not.toContain(word);
  });

  for (const width of [390, 1440]) {
    const viewport = { width, height: width === 390 ? 844 : 900 };

    test(`axe on every state, light and dark, at ${width} px`, async ({ page }) => {
      test.setTimeout(420_000);
      await page.setViewportSize(viewport);
      await everyState(page, async (name, options) => {
        await checkRow(page, `research, ${name} (${width} px)`, options);
        await screenshot(page, width, name, ["light", "dark"]);
      });
    });

    test(`targets are at least 44 px at ${width} px`, async ({ page }) => {
      test.setTimeout(300_000);
      await page.setViewportSize(viewport);
      await everyState(page, async (name) => {
        expect.soft(await smallTargets(page), `${name} at ${width} px`).toEqual([]);
      });
    });
  }
});
