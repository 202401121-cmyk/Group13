/**
 * F-13: contrast from the first frame. The audit matrix (a11y-matrix.spec.ts) runs with reduced motion, so pages are
 * fully drawn when axe reads them. This spec does the opposite: motion on, axe the moment each state shows, on every
 * route and state, at 390 and 1440 px, light and dark. It never waits for an animation to end. Each config loads in
 * its own theme, so dark mode gets a first frame too. At each state it also samples what is animating, and fails if
 * anything that holds text is changing opacity. No axe rule is turned off.
 */
import { expect, test, type Page } from "@playwright/test";
import {
  RESULTS_URL,
  answerAll,
  axeViolations,
  checkRow,
  finishResearch,
  reachBrief,
  reachCriteria,
  reachQuestions,
  reachUnderstood,
  setTheme,
  startScenario,
  waitForHydration,
  type Theme,
} from "./helpers";

const MISSING = "sess_000000000000000000000000";

const SIZES = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
];

/** Every scenario in the brief page's picker that ends in a results page. */
const SCENARIOS = [
  ["success", "tie", "tie-first", "disputed", "disputed-no-sources"],
  ["unknown", "estimated", "stale", "no-offer", "left-out"],
  ["couldnt-check", "partial", "relaxation", "empty", "withheld-quote"],
];

/** Anything that holds text and is changing opacity right now, by what it says. */
function fadingText(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    document.getAnimations().flatMap((animation) => {
      const effect = animation.effect as KeyframeEffect | null;
      const text = effect?.pseudoElement ? "" : (effect?.target?.textContent ?? "").trim();
      const fades = effect?.getKeyframes().some((frame) => "opacity" in frame);
      return text && fades ? [text.slice(0, 40)] : [];
    }),
  );
}

/**
 * One state, read the moment it shows. Always from the top of the page: a click leaves the page scrolled, and axe
 * counts a 44 px button that happens to be passing under the sticky header as a small target, with or without
 * motion. Moving there takes no time and waits for nothing, and axe still reads the whole page.
 */
async function firstFrame(page: Page, row: string) {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  const [fading, violations] = await Promise.all([fadingText(page), axeViolations(page)]);
  console.log(`[first frame] ${row} | ${violations.length ? violations.join("; ") : "pass"}`);
  expect.soft(violations, `axe: ${row}`).toEqual([]);
  expect.soft(fading, `text fading: ${row}`).toEqual([]);
}

/** A state held on screen with the page clock paused; axe needs that clock nudged along. */
async function heldFrame(page: Page, row: string, theme: Theme) {
  expect.soft(await fadingText(page), `text fading: ${row}`).toEqual([]);
  await checkRow(page, row, { paused: true, themes: [theme] });
  await setTheme(page, theme); // checkRow leaves the page in light mode
}

async function pauseClock(page: Page) {
  const now = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(now + 250);
}

async function headerHome(page: Page, width: number) {
  if (width < 768) await page.getByRole("button", { name: "Open menu" }).click();
  await page.locator("header").getByRole("link", { name: "Home", exact: true }).click();
}

for (const size of SIZES) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`first frame, motion on, ${size.width} px, ${theme}`, () => {
      test.describe.configure({ mode: "parallel", timeout: 300_000 });
      test.use({ reducedMotion: "no-preference", viewport: size });

      const row = (name: string) => `${name} (${size.width} px, ${theme})`;

      test.beforeEach(async ({ page }) => {
        await page.addInitScript((t) => {
          try {
            localStorage.setItem("shopsense.theme", t);
          } catch {
            /* the first blank page has no storage */
          }
        }, theme);
        await page.clock.install();
      });

      test("information pages", async ({ page }) => {
        for (const [path, name] of [
          ["/", "home"],
          ["/about", "about"],
          ["/contact", "contact"],
          ["/faq", "faq"],
          ["/features", "features"],
          ["/privacy", "privacy"],
          ["/criteria", "old criteria link, no brief"],
          ["/no-such-page", "page not found"],
        ] as const) {
          await page.goto(path);
          await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark)/);
          await firstFrame(page, row(name));
        }

        await page.goto("/");
        await waitForHydration(page, "#research-query");
        await page.getByRole("button", { name: /Start my research/ }).click();
        await expect(page.getByText(/Tell us a little about/)).toBeVisible();
        await firstFrame(page, row("home, empty request error"));

        if (size.width < 768) {
          await page.getByRole("button", { name: "Open menu" }).click();
          await expect(page.getByRole("button", { name: "Close menu" })).toBeVisible();
          await firstFrame(page, row("home, menu open"));
        }
      });

      test("request page states", async ({ page }) => {
        await page.goto("/research");
        await firstFrame(page, row("research, empty"));
        await waitForHydration(page, "#request-text");

        await page.getByRole("button", { name: /^Continue/ }).click();
        await expect(page.getByText("Type what you're looking for first.")).toBeVisible();
        await firstFrame(page, row("research, empty request error"));

        await page.getByLabel("Your request").fill("Laptop for coding under ₹70,000");
        await pauseClock(page);
        await page.getByRole("button", { name: /^Continue/ }).click();
        await expect(page.getByText(/Reading your request/).first()).toBeVisible();
        await heldFrame(page, row("research, reading the request"), theme);
        await page.clock.resume();

        await expect(
          page.getByRole("heading", { level: 1, name: "Here's what we understood" }),
        ).toBeVisible();
        await firstFrame(page, row("research, understood"));

        await page.getByRole("button", { name: "Edit budget" }).click();
        await page.getByLabel("From").fill("90000");
        await firstFrame(page, row("research, editing the budget"));
        await page.getByRole("button", { name: "Save" }).click();
        await expect(page.getByText("From can't be more than Up to.")).toBeVisible();
        await firstFrame(page, row("research, budget error"));
        await page.getByRole("button", { name: "Cancel" }).click();

        await page.getByRole("button", { name: "Add exclusions" }).click();
        await page.getByLabel("Add something to leave out").fill("refurbished");
        await page.getByRole("button", { name: "Add", exact: true }).click();
        await firstFrame(page, row("research, editing leave-out list"));
        await page.getByRole("button", { name: "Save" }).click();
        await expect(page.getByRole("button", { name: "Edit exclusions" })).toBeVisible();

        await page.getByRole("button", { name: "Start over" }).click();
        await expect(page.getByText("Discard your edits and start over?")).toBeVisible();
        await firstFrame(page, row("research, discard edits?"));
        await page.getByRole("button", { name: "Discard and start over" }).click();

        await reachUnderstood(page, "laptop under 50k");
        await expect(page.getByText(/backup reader/).first()).toBeVisible();
        await firstFrame(page, row("research, understood by the backup reader"));

        await page.getByRole("button", { name: "Start over" }).click();
        await page.getByLabel("Your request").fill("a gift for my mom");
        await page.getByRole("button", { name: /^Continue/ }).click();
        await expect(page.getByRole("group", { name: "Product type" })).toBeVisible();
        await firstFrame(page, row("research, follow-up question"));
        await page.getByRole("button", { name: "Something else" }).click();
        await page.getByRole("button", { name: /^Continue/ }).click();
        await firstFrame(page, row("research, follow-up, something else"));

        await page.getByRole("button", { name: "Change my request instead" }).click();
        await page.getByLabel("Your request").fill("blue and shiny please");
        await page.getByRole("button", { name: /^Continue/ }).click();
        await expect(page.getByText(/couldn't tell what kind of product/).first()).toBeVisible();
        await firstFrame(page, row("research, examples"));

        await page.getByRole("button", { name: "Describe it in my own words" }).click();
        await page.getByLabel("Your request").fill("laptop network error test");
        await page.getByRole("button", { name: /^Continue/ }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await firstFrame(page, row("research, network error"));
      });

      test("criteria page states", async ({ page }) => {
        await reachUnderstood(page);
        await pauseClock(page);
        await page.getByRole("button", { name: /Looks right/ }).click();
        await expect(page.getByRole("list", { name: "Loading criteria" })).toBeVisible();
        await heldFrame(page, row("criteria, loading"), theme);
        await page.clock.resume();
        await expect(page.getByRole("button", { name: /Start the questions/ })).toBeEnabled();
        await firstFrame(page, row("criteria, ready"));

        await reachCriteria(page, "TV for the living room under ₹40,000");
        await expect(page.getByText(/standard list/).first()).toBeVisible();
        await firstFrame(page, row("criteria, standard list"));

        await reachUnderstood(page);
        await page.getByRole("button", { name: "Edit product" }).click();
        await page.getByLabel("Product").fill("Test error");
        await page.getByRole("button", { name: "Save" }).click();
        await page.getByRole("button", { name: /Looks right/ }).click();
        await expect(page.getByRole("alert")).toContainText("couldn't load the criteria");
        await firstFrame(page, row("criteria, could not load"));

        await page.goto(`/s/${MISSING}/criteria`);
        await firstFrame(page, row("criteria, search not found"));
      });

      test("question page states", async ({ page }) => {
        const id = await reachQuestions(page);
        await firstFrame(page, row("questions, first question"));

        await page.getByRole("button", { name: "Why this question?" }).click();
        await firstFrame(page, row("questions, why this question open"));

        const composer = page.getByLabel("Or type your answer");
        await composer.fill("Is this about compile speed?");
        await composer.press("Enter");
        await expect(page.getByText(/^Good question/).first()).toBeVisible();
        await firstFrame(page, row("questions, answer then repeat"));

        await composer.fill("not sure");
        await composer.press("Enter");
        await expect(page.getByText("Asked another way")).toBeVisible();
        await firstFrame(page, row("questions, asked another way"));

        await composer.fill("what about the price");
        await composer.press("Enter");
        await expect(page.getByText(/use the budget from your brief/).first()).toBeVisible();
        await firstFrame(page, row("questions, redirect"));

        await composer.fill("test a conflict");
        await composer.press("Enter");
        await expect(page.getByText(/already answered/).first()).toBeVisible();
        await firstFrame(page, row("questions, reply came too late"));

        const replies = page.getByRole("group", { name: /Quick replies/ });
        await pauseClock(page);
        await replies.getByRole("button", { name: "High" }).click();
        await page.clock.runFor(410);
        await expect(page.getByText("Thinking about your answer").first()).toBeVisible();
        await heldFrame(page, row("questions, thinking"), theme);
        await page.clock.resume();
        await expect(page.getByText(/^Memory: how much/).first()).toBeVisible();
        await replies.getByRole("button", { name: "High" }).click();
        await expect(page.getByText(/^Battery life: how much/).first()).toBeVisible();

        // Memory depends on CPU and is answered now: changing CPU means memory has to be asked again.
        if (size.width < 1024) await page.getByRole("button", { name: /Answers so far/ }).click();
        await firstFrame(page, row("questions, answers list"));
        await page.getByRole("button", { name: "Change CPU performance" }).click();
        await replies.getByRole("button", { name: "Low" }).click();
        await expect(page.getByText("Later questions were updated").first()).toBeVisible();
        await firstFrame(page, row("questions, later questions updated"));

        await page.goto(`/s/${id}/brief`);
        await firstFrame(page, row("brief, part-way through the questions"));

        await page.goto(`/s/${id}/questions`);
        await answerAll(page);
        await firstFrame(page, row("questions, finished"));

        await page.goto(`/s/${MISSING}/questions`);
        await firstFrame(page, row("questions, search not found"));
      });

      test("brief and waiting states", async ({ page }) => {
        await reachBrief(page);
        await firstFrame(page, row("brief, ready"));

        await pauseClock(page);
        await page.getByRole("button", { name: /Start research/ }).click();
        await page.clock.runFor(100); // the brief page opens the results page one frame after the tap
        await expect(page).toHaveURL(RESULTS_URL);
        await page.clock.runFor(1_400);
        await expect(page.getByRole("heading", { name: /What ShopSense is doing/ })).toBeVisible();
        await heldFrame(page, row("results, researching"), theme);

        await headerHome(page, size.width);
        await expect(page.getByRole("alertdialog", { name: "Stop research?" })).toBeVisible();
        await heldFrame(page, row("results, leave page dialog"), theme);
        await page.getByRole("button", { name: "Keep researching" }).click();

        await page.getByRole("button", { name: "Stop research" }).click();
        await page
          .getByRole("alertdialog", { name: "Stop research?" })
          .getByRole("button", { name: "Stop research" })
          .click();
        await page.clock.resume();
        await expect(page.getByRole("heading", { level: 1, name: "Search stopped" })).toBeVisible();
        await firstFrame(page, row("results, search stopped"));
      });

      for (const group of SCENARIOS) {
        test(`results: ${group.join(", ")}`, async ({ page }) => {
          const id = await reachBrief(page);
          for (const scenario of group) {
            await startScenario(page, id, scenario);
            await finishResearch(page);
            await expect(
              page.getByRole("heading", { level: 1, name: /Results for your/ }),
            ).toBeVisible();
            // Every section open, as the report measured it.
            await page.evaluate(() => {
              for (const d of document.querySelectorAll("details")) d.open = true;
            });
            await firstFrame(page, row(`results, ${scenario}`));
          }
        });
      }

      test("results: errors, timeout and gone", async ({ page }) => {
        const id = await reachBrief(page);
        await startScenario(page, id, "network-retry");
        await page.clock.runFor(3_000);
        await expect(page.getByText(/lost the connection/).first()).toBeVisible();
        await firstFrame(page, row("results, network error"));
        await page.getByRole("button", { name: "Try again" }).click();
        await finishResearch(page);
        await expect(
          page.getByRole("heading", { level: 1, name: /Results for your/ }),
        ).toBeVisible();

        await startScenario(page, id, "timeout");
        await page.clock.runFor(171_000);
        await expect(page.getByText(/took too long/).first()).toBeVisible();
        await firstFrame(page, row("results, research timed out"));

        await page.goto(`/s/${id}/results`);
        await expect(page.getByRole("heading", { name: /aren't here any more/ })).toBeVisible();
        await firstFrame(page, row("results, no search to show"));

        await page.goto(`/s/${MISSING}/results`);
        await firstFrame(page, row("results, search not found"));
      });
    });
  }
}

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  /** Everything still moving or fading on the page, by what it is. */
  const moving = (page: Page) =>
    page.evaluate(() =>
      document
        .getAnimations()
        .filter((a) => a.playState === "running")
        .map(
          (a) => `${(a.effect as KeyframeEffect | null)?.target?.nodeName} ${a.constructor.name}`,
        ),
    );

  test("nothing moves on the way from the home page to the results", async ({ page }) => {
    await page.clock.install();
    await page.goto("/");
    await waitForHydration(page, "#research-query");
    expect(await moving(page), "home").toEqual([]);

    await reachUnderstood(page);
    await pauseClock(page);
    await page.getByRole("button", { name: /Looks right/ }).click();
    await expect(page.getByRole("list", { name: "Loading criteria" })).toBeVisible();
    expect(await moving(page), "loading skeletons").toEqual([]);
    await page.clock.resume();

    await expect(page.getByRole("button", { name: /Start the questions/ })).toBeEnabled();
    await page.getByRole("button", { name: /Start the questions/ }).click();
    await expect(page.getByRole("group", { name: /Quick replies/ })).toBeVisible();
    await answerAll(page);
    expect(await moving(page), "questions finished").toEqual([]);
  });

  test("the results, the dialog and the toast appear without moving", async ({ page }) => {
    await page.clock.install();
    const id = await reachBrief(page);
    await startScenario(page, id, "tie");
    await finishResearch(page);
    await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
    expect(await moving(page), "results").toEqual([]);
    const shifted = await page
      .locator("[data-reveal]")
      .evaluateAll((els) =>
        els.filter((e) => !["none", "0px"].includes(getComputedStyle(e).translate)),
      );
    expect(shifted, "no section starts out of place").toEqual([]);

    await page.goto(`/s/${id}/brief`);
    await expect(page.getByRole("button", { name: /Start research/ })).toBeEnabled();
    await pauseClock(page);
    await page.getByRole("button", { name: /Start research/ }).click();
    await page.clock.runFor(100);
    await expect(page).toHaveURL(RESULTS_URL);
    await page.clock.runFor(1_400);
    await headerHome(page, 1280);
    await expect(page.getByRole("alertdialog", { name: "Stop research?" })).toBeVisible();
    expect(await moving(page), "dialog").toEqual([]);

    await page.getByRole("button", { name: "Stop research" }).last().click();
    await page.clock.resume();
    await expect(page.getByText("Search stopped").first()).toBeVisible();
    expect(await moving(page), "toast").toEqual([]);
  });
});
