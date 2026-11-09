/**
 * Check 1 of the quality gate: axe (WCAG 2.0, 2.1 and 2.2, A and AA) on every row of the audit matrix,
 * in light and dark mode. A row is one route in one state; every state is reached through the screens.
 * No axe rule is disabled.
 */
import { expect, test, type Page } from "@playwright/test";
import {
  RESULTS_URL,
  answerAll,
  checkRow,
  finishResearch,
  reachBrief,
  reachCriteria,
  reachQuestions,
  reachUnderstood,
  RESULT_SCENARIOS,
  startScenario,
  waitForHydration,
} from "./helpers";

const MISSING = "sess_000000000000000000000000";

/** Freezes page time so a short wait (a loading state) stays on screen while axe runs. */
async function pauseClock(page: Page) {
  const now = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(now + 250);
}

test.describe("audit matrix: axe in light and dark mode", () => {
  // The tests are independent (each starts its own search), so they can run side by side.
  test.describe.configure({ mode: "parallel", timeout: 240_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  test("information pages", async ({ page }) => {
    for (const [path, row] of [
      ["/", "home"],
      ["/about", "about (coming soon)"],
      ["/contact", "contact (coming soon)"],
      ["/faq", "faq (coming soon)"],
      ["/features", "features (coming soon)"],
      ["/privacy", "privacy"],
      ["/criteria", "old criteria link, no brief"],
      ["/no-such-page", "page not found"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await checkRow(page, row);
    }

    await page.goto("/");
    await waitForHydration(page, "#research-query");
    await page.getByRole("button", { name: /Start my research/ }).click();
    await expect(page.getByText(/Tell us a little about/)).toBeVisible();
    await checkRow(page, "home, empty request error");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(page.getByRole("button", { name: "Close menu" })).toBeVisible();
    await checkRow(page, "home, mobile menu open (390 px)");
  });

  test("request page states", async ({ page }) => {
    await page.goto("/research");
    await waitForHydration(page, "#request-text");
    await checkRow(page, "research, empty");

    await page.getByRole("button", { name: /^Continue/ }).click();
    await expect(page.getByText("Type what you're looking for first.")).toBeVisible();
    await checkRow(page, "research, empty request error");

    await page.getByLabel("Your request").fill("Laptop for coding under ₹70,000");
    await pauseClock(page);
    await page.getByRole("button", { name: /^Continue/ }).click();
    await expect(page.getByText(/Reading your request/).first()).toBeVisible();
    await checkRow(page, "research, reading the request", { paused: true });
    await page.clock.resume();

    await expect(
      page.getByRole("heading", { level: 1, name: "Here's what we understood" }),
    ).toBeVisible();
    await checkRow(page, "research, understood");

    await page.getByRole("button", { name: "Edit budget" }).click();
    await page.getByLabel("From").fill("90000");
    await checkRow(page, "research, editing the budget");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("From can't be more than Up to.")).toBeVisible();
    await checkRow(page, "research, budget error");
    await page.getByRole("button", { name: "Cancel" }).click();

    await page.getByRole("button", { name: "Add exclusions" }).click();
    await page.getByLabel("Add something to leave out").fill("refurbished");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await checkRow(page, "research, editing leave-out list");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("button", { name: "Edit exclusions" })).toBeVisible();

    await page.getByRole("button", { name: "Start over" }).click();
    await expect(page.getByText("Discard your edits and start over?")).toBeVisible();
    await checkRow(page, "research, discard edits?");
    await page.getByRole("button", { name: "Discard and start over" }).click();

    await reachUnderstood(page, "laptop under 50k");
    await expect(page.getByText(/backup reader/).first()).toBeVisible();
    await checkRow(page, "research, understood by the backup reader");

    await page.getByRole("button", { name: "Start over" }).click();
    await page.getByLabel("Your request").fill("a gift for my mom");
    await page.getByRole("button", { name: /^Continue/ }).click();
    await expect(page.getByRole("group", { name: "Product type" })).toBeVisible();
    await checkRow(page, "research, follow-up question");
    await page.getByRole("button", { name: "Something else" }).click();
    await page.getByRole("button", { name: /^Continue/ }).click();
    await checkRow(page, "research, follow-up, something else");

    await page.getByRole("button", { name: "Change my request instead" }).click();
    await page.getByLabel("Your request").fill("blue and shiny please");
    await page.getByRole("button", { name: /^Continue/ }).click();
    await expect(page.getByText(/couldn't tell what kind of product/).first()).toBeVisible();
    await checkRow(page, "research, examples");

    await page.getByRole("button", { name: "Describe it in my own words" }).click();
    await page.getByLabel("Your request").fill("laptop network error test");
    await page.getByRole("button", { name: /^Continue/ }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await checkRow(page, "research, network error");
  });

  test("criteria page states", async ({ page }) => {
    await reachUnderstood(page);
    await pauseClock(page);
    await page.getByRole("button", { name: /Looks right/ }).click();
    await expect(page.getByRole("list", { name: "Loading criteria" })).toBeVisible();
    await checkRow(page, "criteria, loading", { paused: true });
    await page.clock.resume();
    await expect(page.getByRole("button", { name: /Start the questions/ })).toBeEnabled();
    await checkRow(page, "criteria, ready");

    await reachCriteria(page, "TV for the living room under ₹40,000");
    await expect(page.getByText(/standard list/).first()).toBeVisible();
    await checkRow(page, "criteria, standard list");

    await reachUnderstood(page);
    await page.getByRole("button", { name: "Edit product" }).click();
    await page.getByLabel("Product").fill("Test error");
    await page.getByRole("button", { name: "Save" }).click();
    await page.getByRole("button", { name: /Looks right/ }).click();
    await expect(page.getByRole("alert")).toContainText("couldn't load the criteria");
    await checkRow(page, "criteria, could not load");

    await page.goto(`/s/${MISSING}/criteria`);
    await expect(page.getByRole("heading", { name: "We couldn't find this search" })).toBeVisible();
    await checkRow(page, "criteria, search not found");
  });

  test("question page states", async ({ page }) => {
    const id = await reachQuestions(page);
    await checkRow(page, "questions, first question");

    await page.getByRole("button", { name: "Why this question?" }).click();
    await checkRow(page, "questions, why this question open");

    const composer = page.getByLabel("Or type your answer");
    await composer.fill("Is this about compile speed?");
    await composer.press("Enter");
    await expect(page.getByText(/^Good question/).first()).toBeVisible();
    await checkRow(page, "questions, answer then repeat");

    await composer.fill("not sure");
    await composer.press("Enter");
    await expect(page.getByText("Asked another way")).toBeVisible();
    await checkRow(page, "questions, asked another way");

    await composer.fill("what about the price");
    await composer.press("Enter");
    await expect(page.getByText(/use the budget from your brief/).first()).toBeVisible();
    await checkRow(page, "questions, redirect");

    // "Thinking about your answer" shows from 400 ms until the mock reply at 600 ms: too short for two axe
    // runs, so it is checked in light mode on one answer and in dark mode on the next.
    const replies = page.getByRole("group", { name: /Quick replies/ });
    for (const [theme, next] of [
      ["light", /Memory: how much does it matter to you/],
      ["dark", /Battery life: how much does it matter to you/],
    ] as const) {
      await pauseClock(page);
      await replies.getByRole("button", { name: "High" }).click();
      await page.clock.runFor(410);
      await expect(page.getByText("Thinking about your answer").first()).toBeVisible();
      await checkRow(page, "questions, thinking", { paused: true, themes: [theme] });
      await page.clock.resume();
      await expect(page.getByText(next).first()).toBeVisible();
    }
    // Memory depends on CPU and is answered now: changing CPU means memory has to be asked again.
    await page.getByRole("button", { name: "Change CPU performance" }).click();
    await replies.getByRole("button", { name: "Low" }).click();
    await expect(page.getByText("Later questions were updated").first()).toBeVisible();
    await checkRow(page, "questions, later questions updated");

    // Part-way through: in mock mode the brief already shows weights from the answers so far. (The
    // "Finish the questions first" notice is live mode only, so it can't be reached here.)
    await page.goto(`/s/${id}/brief`);
    await expect(page.getByRole("heading", { level: 1, name: /Your brief for a/ })).toBeVisible();
    await checkRow(page, "brief, part-way through the questions");

    await page.goto(`/s/${id}/questions`);
    await answerAll(page);
    await checkRow(page, "questions, finished");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: /Answers so far/ }).click();
    await checkRow(page, "questions, answers list open (390 px)");

    await page.goto(`/s/${MISSING}/questions`);
    await expect(page.getByRole("heading", { name: "We couldn't find this search" })).toBeVisible();
    await checkRow(page, "questions, search not found");
  });

  test("brief and waiting states", async ({ page }) => {
    await reachBrief(page);
    await checkRow(page, "brief, ready");

    // Waiting, then the leave-page dialog, then stopping. The clock is paused so research keeps running.
    await pauseClock(page);
    await page.getByRole("button", { name: /Start research/ }).click();
    await page.clock.runFor(100); // the brief page opens the results page one frame after the tap
    await expect(page).toHaveURL(RESULTS_URL);
    await page.clock.runFor(1_400);
    await expect(page.getByRole("heading", { name: /What ShopSense is doing/ })).toBeVisible();
    await checkRow(page, "results, researching", { paused: true });
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Home", exact: true })
      .click();
    await expect(page.getByRole("alertdialog", { name: "Stop research?" })).toBeVisible();
    await checkRow(page, "results, leave page dialog", { paused: true });
    await page.getByRole("button", { name: "Keep researching" }).click();
    await page.getByRole("button", { name: "Stop research" }).click();
    await page
      .getByRole("alertdialog", { name: "Stop research?" })
      .getByRole("button", { name: "Stop research" })
      .click();
    await page.clock.resume();
    await expect(page.getByRole("heading", { level: 1, name: "Search stopped" })).toBeVisible();
    await checkRow(page, "results, search stopped");
  });

  // The twelve result scenarios, in three groups so each test stays short.
  for (const group of [
    RESULT_SCENARIOS.slice(0, 4),
    RESULT_SCENARIOS.slice(4, 8),
    RESULT_SCENARIOS.slice(8),
  ]) {
    test(`results: ${group.join(", ")}`, async ({ page }) => {
      const id = await reachBrief(page);
      for (const scenario of group) {
        await startScenario(page, id, scenario);
        await finishResearch(page);
        await expect(
          page.getByRole("heading", { level: 1, name: /Results for your/ }),
        ).toBeVisible();
        await checkRow(page, `results, ${scenario}`);
        if (scenario === "disputed") {
          await page.getByText("Show all criteria").first().click();
          await checkRow(page, "results, disputed, all criteria open");
        }
        if (scenario === "left-out") {
          await page.getByText(/Left out \(/).click();
          await checkRow(page, "results, left-out list open");
        }
        if (scenario === "success") {
          await page.getByText("Your brief", { exact: true }).last().click();
          await checkRow(page, "results, brief summary open");
        }
      }
    });
  }

  test("results: errors, timeout and gone", async ({ page }) => {
    const id = await reachBrief(page);
    await startScenario(page, id, "network-retry");
    await page.clock.runFor(3_000);
    await expect(page.getByText(/lost the connection/).first()).toBeVisible();
    await checkRow(page, "results, network error");
    await page.getByRole("button", { name: "Try again" }).click();
    await finishResearch(page);
    await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();

    await startScenario(page, id, "timeout");
    await page.clock.runFor(171_000);
    await expect(page.getByText(/took too long/).first()).toBeVisible();
    await checkRow(page, "results, research timed out");

    await page.goto(`/s/${id}/results`);
    await expect(page.getByRole("heading", { name: /aren't here any more/ })).toBeVisible();
    await checkRow(page, "results, no search to show");

    await page.goto(`/s/${MISSING}/results`);
    await expect(page.getByRole("heading", { name: "We couldn't find this search" })).toBeVisible();
    await checkRow(page, "results, search not found");
  });
});
