/**
 * The result states that carry a promise to the shopper, checked on the real page in mock mode: what each one
 * says, and axe (WCAG 2.0, 2.1 and 2.2, A and AA) at 390 and 1440 px, light and dark. Set SHOTS_DIR to a folder
 * outside the repository to save a full-page screenshot of each state in each size and theme.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  checkRow,
  finishResearch,
  reachBrief,
  setTheme,
  startScenario,
  type Theme,
} from "./helpers";
import { walkFocus } from "./layout";

const SHOTS = process.env["SHOTS_DIR"];

const SIZES = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
];

const STATES = [
  "tie",
  "tie-first",
  "stale",
  "no-offer",
  "disputed",
  "disputed-no-sources",
  // Sprint 4 (task #59)
  "success",
  "tie-three",
  "over-budget",
  "offer-24h",
  "llm-unavailable",
];

/** A full-page screenshot in each theme, when SHOTS_DIR is set. */
async function screenshot(page: Page, width: number, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  for (const theme of ["light", "dark"] as Theme[]) {
    await setTheme(page, theme);
    await page.screenshot({
      path: join(SHOTS, `${width}-${theme}-results-${name}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
  await setTheme(page, "light");
}

/** Presses Tab until the named button has focus (keyboard only), failing if it is never reached. */
async function tabTo(page: Page, name: string) {
  for (let i = 0; i < 80; i++) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? "");
    if (focused === name) return;
  }
  throw new Error(`Tab never reached "${name}"`);
}

/** Runs one mock scenario to its results, with every section open. */
async function showScenario(page: Page, sessionId: string, scenario: string) {
  await startScenario(page, sessionId, scenario);
  await finishResearch(page);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  await page.evaluate(() => {
    for (const d of document.querySelectorAll("details")) d.open = true;
  });
}

test.describe("result states", () => {
  test.describe.configure({ mode: "parallel", timeout: 240_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  for (const size of SIZES) {
    test(`axe on every changed state at ${size.width} px`, async ({ page }) => {
      await page.setViewportSize(size);
      const id = await reachBrief(page);
      for (const scenario of STATES) {
        await showScenario(page, id, scenario);
        await checkRow(page, `results, ${scenario} (${size.width} px)`);
        await screenshot(page, size.width, scenario);
      }
    });
  }

  test("a tie for second has one card and the rest of the page is unchanged", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "tie");
    const tie = page.getByRole("region", { name: "Too close to call" });
    await expect(tie).toHaveCount(1);
    await expect(tie).toContainText("Sample laptop B and Sample laptop C are tied for 2nd.");
    await expect(tie).toContainText("What separates them: CPU performance");
    await expect(page.getByText("Tied for 2nd", { exact: true })).toHaveCount(2);
    await expect(page.getByText(/#=/)).toHaveCount(0);
    await expect(page.getByRole("list", { name: "Best match" }).getByRole("article")).toHaveCount(
      1,
    );
    await expect(
      page.getByRole("list", { name: "Other options" }).getByRole("article"),
    ).toHaveCount(2);
  });

  test("old prices say so, from their as-of time", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "stale");
    await expect(page.getByText("May have changed")).toHaveCount(3);
    await expect(page.getByText(/^Old price/)).toHaveCount(0);
    await expect(page.getByText("We don't know when this price was checked")).toHaveCount(1);
    await expect(page.getByText(/^Price as of /)).toHaveCount(5);

    // Every other scenario's prices are recent, so none carries the warning.
    await showScenario(page, id, "success");
    await expect(page.getByText("May have changed")).toHaveCount(0);
  });

  test("no offer says so and links to a store search", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "no-offer");
    const b = page.getByRole("region", { name: "Store offers for Sample laptop B" });
    await expect(b).toContainText("No price found");
    const search = b.getByRole("link", { name: /Search stores/ });
    await expect(search).toHaveAttribute("target", "_blank");
    await expect(search).toHaveAttribute("rel", "noopener noreferrer");
    await expect(search).toHaveAttribute("href", /^https:\/\/example\.com\/store-search\?q=/);
  });

  test("a disputed value shows its sources, or says they aren't available yet", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "disputed");
    await expect(page.getByText("Sample Store One listing:")).toContainText("16 GB");
    await expect(page.getByText("Sample maker spec sheet:")).toContainText("8 GB");
    await expect(page.getByText("Source details aren't available yet.")).toHaveCount(0);

    await showScenario(page, id, "disputed-no-sources");
    await expect(page.getByText("Sources disagree", { exact: true })).toHaveCount(1);
    await expect(page.getByText("Source details aren't available yet.")).toHaveCount(1);
    await expect(page.getByText("Sample Store One listing:")).toHaveCount(0);
  });

  test("at 390 px, focus is never hidden under the sticky header", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const id = await reachBrief(page);
    for (const scenario of ["tie", "no-offer"]) {
      await showScenario(page, id, scenario);
      const stops = [
        ...(await walkFocus(page, "Tab")).stops,
        ...(await walkFocus(page, "Shift+Tab")).stops,
      ];
      expect(stops.length).toBeGreaterThan(10);
      const hidden = stops.filter((s) => s.obscured !== "no" || !s.inView || !s.indicator);
      expect(hidden.map((s) => s.name)).toEqual([]);
    }
  });

  test("a tie for first names no single best match", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "tie-first");
    const best = page.getByRole("list", { name: "Best match" });
    await expect(best.getByRole("article")).toHaveCount(2);
    await expect(best.getByRole("region", { name: "Too close to call" })).toContainText(
      "Sample laptop A and Sample laptop B are tied for 1st.",
    );
    await expect(best.getByText("Tied for 1st", { exact: true })).toHaveCount(2);
    await expect(best.getByText("Best match", { exact: true })).toHaveCount(0);
    await expect(
      page.getByRole("list", { name: "Other options" }).getByRole("article"),
    ).toHaveCount(1);
    await expect(page.getByText("are too close to call.")).toHaveCount(1);
  });

  test("an LLM outage says so plainly, and Try again runs the same search to its results", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "llm-unavailable");
    const alert = page.getByRole("alert").filter({ hasText: "couldn't reach the AI services" });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(
      "We couldn't reach the AI services that read the sources, so this search found nothing. Please try again.",
    );
    // Never "nothing matched"; the backend's note still shows, with anything else it says.
    await expect(page.getByText(/No option matched/)).toHaveCount(0);
    await expect(page.getByText(/^The AI services we use were all unavailable/)).toBeVisible();
    await expect(page).toHaveTitle("Search problem | ShopSense");
    await expect(page.getByText(/^Prices checked /)).toHaveCount(0);

    // Keyboard only: Tab to Try again and press Enter.
    await tabTo(page, "Try again");
    await page.keyboard.press("Enter");
    await finishResearch(page);
    await expect(page.getByRole("list", { name: "Best match" }).getByRole("article")).toHaveCount(
      1,
    );
    await expect(alert).toHaveCount(0);
    await expect(page.getByText(/^Prices checked /)).toHaveCount(1);
    expect(page.url()).toContain(`/s/${id}/results`);
  });

  test("a tie of three names all three and what separates them", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "tie-three");
    const best = page.getByRole("list", { name: "Best match" });
    await expect(best.getByRole("article")).toHaveCount(3);
    const tie = best.getByRole("region", { name: "Too close to call" });
    await expect(tie).toContainText(
      "Sample laptop A, Sample laptop B and Sample laptop C are tied for 1st.",
    );
    await expect(tie).toContainText("What separates them: CPU performance");
    await expect(best.getByText("Tied for 1st", { exact: true })).toHaveCount(3);
  });

  test("each card gives its chance of ranking first and its confidence in words", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "success");
    await expect(page.getByText("62% chance of ranking first")).toBeVisible();
    await expect(page.getByText("High confidence").first()).toBeVisible();
    await expect(page.getByText(/% chance of ranking first/)).toHaveCount(3);
  });

  test("an offer above the budget is flagged; stock is said in words", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "over-budget");
    const a = page.getByRole("region", { name: "Store offers for Sample laptop A" });
    await expect(a.getByText("Above your budget")).toBeVisible();
    const b = page.getByRole("region", { name: "Store offers for Sample laptop B" });
    await expect(b).toContainText("Out of stock");
    await expect(page.getByText("Above your budget")).toHaveCount(1);
  });

  test("a price exactly 24 hours old hasn't changed yet; a minute older may have", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "offer-24h");
    const a = page.getByRole("region", { name: "Store offers for Sample laptop A" });
    await expect(a.getByText(/^Price as of /)).toHaveCount(2);
    await expect(a.getByText("May have changed")).toHaveCount(1);
  });

  test("an explanation's sentence opens its sources in the drawer, with no new request", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "success");
    const card = page.getByRole("list", { name: "Best match" }).getByRole("article");
    await expect(card.getByText(/^One trade-off: /)).toBeVisible();
    const sources = card.getByRole("button", {
      name: /^Sentence 1 sources: Sample explanation: A /,
    });
    // Time stands still: a request for the sources would stay on "Loading the sources...".
    const now = await page.evaluate(() => Date.now());
    await page.clock.pauseAt(now + 50);
    await sources.focus();
    await page.keyboard.press("Enter");
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("link").first()).toHaveAttribute("rel", "noopener noreferrer");
    await expect(drawer.getByText("Loading the sources...")).toHaveCount(0);
    // A sentence has no value state, so no "Known" badge.
    await expect(drawer.getByText("Known", { exact: true })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(sources).toBeFocused();
  });
});
