/**
 * US-23 (S5-5) on the real page in mock mode: change what matters and the list re-ranks in the browser, within 1 s
 * for 50 products on a slowed CPU, with no request; the budget and must-haves re-filter at once; and up to 4
 * products are compared side by side, keyboard only, with the table scrolling in its own region at 390 px. axe in
 * light and dark. Set SHOTS_DIR to a folder outside the repository to save the screenshots for the pull request.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { checkRow, reachBrief, setTheme, startScenario, type Theme } from "./helpers";
import { focusStop } from "./layout";

const SHOTS = process.env["SHOTS_DIR"];
const BUDGET_MS = 1_000;
const CONFIDENCE_LINE = "Confidence was worked out for your original priorities.";
const NOT_SCORED = "Not scored for these priorities. Run the search again to score it.";
const COMPARE_LIMIT = "You can compare up to 4 products. Remove one to pick another.";

const SIZES = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
];

async function showResults(page: Page, sessionId: string, scenario: string) {
  await startScenario(page, sessionId, scenario);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible({
    timeout: 30_000,
  });
}

/**
 * A screenshot in each theme, when SHOTS_DIR is set: the whole page, or the part given. While "Change what matters"
 * is open, card bodies off screen are not drawn (content-visibility); a full-page capture draws them first.
 */
async function screenshot(page: Page, width: number, name: string, part?: Locator) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  const drawAll = part
    ? null
    : await page.addStyleTag({
        content: "[data-card-body] { content-visibility: visible !important; }",
      });
  for (const theme of ["light", "dark"] as Theme[]) {
    await setTheme(page, theme);
    const path = join(SHOTS, `${width}-${theme}-rerank-${name}.png`);
    if (part) {
      // From the top of the page, so the sticky header is not drawn over the part.
      await page.evaluate(() => window.scrollTo(0, 0));
      const clip = await part.evaluate((el) => {
        const r = el.getBoundingClientRect();
        return {
          x: r.left + scrollX - 8,
          y: r.top + scrollY - 8,
          width: r.width + 16,
          height: r.height + 16,
        };
      });
      await page.screenshot({ path, fullPage: true, clip, animations: "disabled" });
    } else await page.screenshot({ path, fullPage: true, animations: "disabled" });
  }
  await drawAll?.evaluate((el) => (el as Element).remove());
  await setTheme(page, "light");
}

async function openPanel(page: Page) {
  await page.getByRole("button", { name: "Change what matters" }).click();
  await expect(page.getByLabel(/, slide from 0 to 10$/).first()).toBeVisible();
}

const sliders = (page: Page) => page.getByLabel(/, slide from 0 to 10$/);
const boxes = (page: Page) => page.getByLabel(/, from 0 to 10$/);

/** The cards' product names, top to bottom. */
function order(page: Page): Promise<string[]> {
  return page
    .locator("article")
    .evaluateAll((els) =>
      els.map(
        (e) => document.getElementById(e.getAttribute("aria-labelledby") ?? "")?.textContent ?? "",
      ),
    );
}

/**
 * Moves a slider and times it in the page, from the input event until the frame showing the new order has been drawn:
 * what the shopper waits for. The change is seen at the start of a frame (its callbacks run before style, layout and
 * paint), so the time is read at the start of the next one.
 */
function timedMove(slider: Locator, value: string): Promise<number> {
  return slider.evaluate(async (el, v) => {
    const names = () =>
      [...document.querySelectorAll("article")]
        .map((a) => document.getElementById(a.getAttribute("aria-labelledby") ?? "")?.textContent)
        .join("|");
    const before = names();
    const input = el as HTMLInputElement;
    const t0 = performance.now();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, v);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    // Polled each frame rather than watched: a frame is what the shopper sees. Never waits past 10 s.
    const changedAt = await new Promise<number | null>((resolve) => {
      const step = () => {
        if (names() !== before) requestAnimationFrame(() => resolve(performance.now()));
        else if (performance.now() - t0 > 10_000) resolve(null);
        else requestAnimationFrame(step);
      };
      step();
    });
    if (changedAt === null) {
      console.log(`[rerank] no change: value ${input.value}, cards ${names().slice(0, 120)}`);
      return Number.POSITIVE_INFINITY;
    }
    return changedAt - t0;
  }, value);
}

/** Presses a key until focus is on an element whose name matches; fails if it never gets there. */
async function focusBy(page: Page, key: "Tab" | "Shift+Tab", matches: (name: string) => boolean) {
  for (let i = 0; i < 120; i++) {
    await page.keyboard.press(key);
    const stop = await focusStop(page);
    if (matches(stop.name)) return stop;
  }
  throw new Error(`${key} never reached the element`);
}

test.describe("re-rank and compare", () => {
  test.describe.configure({ mode: "parallel", timeout: 240_000 });

  test.describe("timing", () => {
    // One retry: on a full parallel run, other browsers share the machine's CPU on top of the 4x slowdown, and one
    // move can come out slow for that alone (about 400 ms run by itself; one move once took 1,453 ms in a full run).
    // A real slowdown fails both tries, and a retried pass is reported as flaky, never hidden.
    test.describe.configure({ retries: 1 });

    test("50 products re-rank within 1 s on a slowed CPU, with no request @timing", async ({
      page,
    }) => {
      const id = await reachBrief(page);
      await showResults(page, id, "many");
      await expect(page.locator("article")).toHaveCount(50);
      await openPanel(page); // its code loads once, here: a static file, never data
      const original = await order(page);

      const requests: string[] = [];
      page.on("request", (r) => requests.push(`${r.method()} ${r.url()}`));
      page.on("console", (m) => {
        if (m.text().startsWith("[rerank]")) console.log(m.text());
      });
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });

      // Everything on the last criterion: the order turns round.
      const n = await sliders(page).count();
      for (let i = 0; i < n - 1; i++) await boxes(page).nth(i).fill("0");
      await boxes(page)
        .nth(n - 1)
        .fill("10");
      await expect(page.getByText(CONFIDENCE_LINE)).toBeVisible();
      const turned = await order(page);
      expect(turned).not.toEqual(original);

      const times: number[] = [];
      times.push(await timedMove(sliders(page).first(), "10")); // first and last equal: back near the start
      times.push(await timedMove(sliders(page).first(), "0")); // round again
      times.push(await timedMove(sliders(page).first(), "10"));
      console.log(
        `[rerank] 50 products, CPU slowed 4x: ${times.map((t) => t.toFixed(0)).join(", ")} ms`,
      );
      for (const t of times) expect(t).toBeLessThanOrEqual(BUDGET_MS);

      // Each card says how far it moved, and none shows chance first or confidence.
      await expect(page.locator("[data-move]")).toHaveCount(50);
      await expect(page.getByText(/chance of ranking first/)).toHaveCount(0);

      // Back to the original priorities: decide's own result, chance first included.
      await page.getByRole("button", { name: "Back to my original priorities" }).click();
      expect(await order(page)).toEqual(original);
      await expect(page.getByText(CONFIDENCE_LINE)).toHaveCount(0);
      await expect(page.getByText(/chance of ranking first/).first()).toBeVisible();
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });

      expect(requests, "re-ranking made a request").toEqual([]);
    });
  });

  for (const size of SIZES) {
    test(`sliders, a moved card and the hidden-confidence line at ${size.width} px`, async ({
      page,
    }) => {
      await page.setViewportSize(size);
      const id = await reachBrief(page);
      await showResults(page, id, "success");
      await openPanel(page);
      const requests: string[] = [];
      page.on("request", (r) => requests.push(`${r.method()} ${r.url()}`));

      // Keyboard only: Tab to the last slider and press End (10), then the first and press Home (0).
      const n = await sliders(page).count();
      const last = sliders(page).nth(n - 1);
      const lastId = await last.getAttribute("id");
      await page.getByRole("button", { name: "Hide what matters" }).focus();
      await focusBy(page, "Tab", (name) => name.startsWith(`input#${lastId}`));
      await page.keyboard.press("End");
      const firstId = await sliders(page).first().getAttribute("id");
      await focusBy(page, "Shift+Tab", (name) => name.startsWith(`input#${firstId}`));
      await page.keyboard.press("Home");
      await expect(page.getByText(CONFIDENCE_LINE)).toBeVisible();
      await expect(page.locator("[data-move]").first()).toBeVisible();
      await expect(page.getByText(/chance of ranking first/)).toHaveCount(0);
      expect(requests, "re-ranking made a request").toEqual([]);

      await checkRow(page, `re-rank, moved (${size.width} px)`);
      await screenshot(page, size.width, "moved");
      await page.getByRole("button", { name: "Back to my original priorities" }).focus();
      await page.keyboard.press("Enter");
      await expect(page.getByText(/chance of ranking first/).first()).toBeVisible();
      await expect(page.getByRole("button", { name: "Hide what matters" })).toBeFocused();
    });

    test(`budget and must-haves re-filter at once at ${size.width} px`, async ({ page }) => {
      await page.setViewportSize(size);
      const id = await reachBrief(page);
      await showResults(page, id, "refilter");
      await expect(page.getByText("Left out (2)")).toBeVisible();
      await openPanel(page);
      const requests: string[] = [];
      page.on("request", (r) => requests.push(`${r.method()} ${r.url()}`));

      await page.getByLabel("Budget up to (₹)").fill("90000");
      await expect(page.getByText(NOT_SCORED)).toBeVisible();
      await expect(page.getByText("Left out (1)")).toBeVisible();
      await checkRow(page, `re-filter, let in (${size.width} px)`);
      await screenshot(page, size.width, "refilter-let-in");

      await page.getByLabel("Budget up to (₹)").fill("50000");
      await expect(page.getByText(/Removing your ₹50,000 budget would let/)).toBeVisible();
      // The page says it, and so does the announcement for screen readers: the visible one is the last.
      await expect(page.getByText("No option matches these must-haves.").last()).toBeVisible();
      await checkRow(page, `re-filter, nothing shown (${size.width} px)`);
      await screenshot(page, size.width, "refilter-none");

      await page.getByLabel("Budget up to (₹)").fill("80000");
      await page.getByLabel("Must have: with a touchscreen").check();
      await expect(page.getByText("Left out (3)")).toBeVisible();
      expect(requests, "re-filtering made a request").toEqual([]);
    });

    test(`compare 4 products, keyboard only, at ${size.width} px`, async ({ page }) => {
      await page.setViewportSize(size);
      const id = await reachBrief(page);
      await showResults(page, id, "many");
      // The products' own names, from their compare boxes (a card's heading also says its rank).
      const names = (
        await page
          .getByRole("checkbox", { name: /^Compare / })
          .evaluateAll((els) =>
            els.map((e) => e.getAttribute("aria-label")!.slice("Compare ".length)),
          )
      ).slice(0, 5);

      // Pick four with Space; a fifth is refused and says the limit.
      await page.getByRole("button", { name: "Change what matters" }).focus();
      for (const name of names) {
        await focusBy(page, "Tab", (n) => n.includes(`"Compare ${name}"`));
        await page.keyboard.press("Space");
      }
      await expect(page.getByText(COMPARE_LIMIT)).toBeVisible();
      await expect(page.getByLabel(`Compare ${names[4]}`)).not.toBeChecked();

      await focusBy(page, "Shift+Tab", (n) => n.includes('"Compare (4)"'));
      await page.keyboard.press("Enter");
      const heading = page.getByRole("heading", { name: "Compare 4 products" });
      await expect(heading).toBeFocused();
      expect((await focusStop(page)).obscured, "the sticky header hides the focused heading").toBe(
        "no",
      );
      const region = page.getByRole("region", { name: "Comparison table" });
      await expect(region.getByRole("columnheader")).toHaveText([
        "Criterion",
        ...names.slice(0, 4),
      ]);
      await expect(region.getByRole("button", { name: /^Sources for / }).first()).toBeVisible();

      // The region takes focus and scrolls with the arrow keys; the page itself never scrolls sideways.
      await focusBy(page, "Tab", (n) => n.startsWith("div"));
      await expect(region).toBeFocused();
      const sideways = await region.evaluate((el) => el.scrollWidth > el.clientWidth);
      if (size.width === 390) {
        expect(sideways, "the table scrolls inside its region at 390 px").toBe(true);
        for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowRight");
        expect(await region.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
      }
      const page390 = await page.evaluate(() => ({
        page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        x: window.scrollX,
      }));
      expect(page390).toEqual({ page: 0, x: 0 });

      await checkRow(page, `compare 4 (${size.width} px)`);
      await region.evaluate((el) => (el.scrollLeft = 0));
      await screenshot(
        page,
        size.width,
        "compare-4",
        heading.locator("xpath=ancestor::section[1]"),
      );

      await focusBy(page, "Shift+Tab", (n) => n.includes('"Close comparison"'));
      await page.keyboard.press("Enter");
      await expect(region).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Compare (4)" })).toBeFocused();
    });
  }
});
