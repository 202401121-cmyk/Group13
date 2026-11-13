/**
 * US-21, US-18 (criterion 6) and US-17 on the real results page in mock mode, by keyboard only: each main reason
 * shows its state and sources, "Where this comes from" opens with Enter and closes with Escape, a disputed value
 * shows both sides, a quote that wasn't kept says why, and the left-out, can't-tell and relaxation states read
 * right. axe at 390 and 1440 px, light and dark. Set SHOTS_DIR to save full-page screenshots of each state.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  checkRow,
  finishResearch,
  reachBrief,
  setTheme,
  startScenario,
  type Theme,
} from "./quality/helpers";
import { MIN_TARGET, tabTo } from "./quality/layout";

const SHOTS = process.env["SHOTS_DIR"];
const SIZES = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
];
const REDDIT_WITHHELD =
  "Reddit's words aren't kept in saved results. Open the link to read them on Reddit.";

/** Runs one mock scenario to its results. Sections stay as the page draws them. */
async function showScenario(page: Page, sessionId: string, scenario: string) {
  await startScenario(page, sessionId, scenario);
  await finishResearch(page);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
}

const productA = (page: Page) => page.getByRole("article", { name: /Sample laptop A/ });
const reasonsOf = (card: Locator) => card.getByRole("list", { name: /^Main reasons for / });
const drawer = (page: Page) => page.getByRole("dialog", { name: /: where this comes from$/ });

/** Moves focus to a control and presses Enter, as a keyboard user who has tabbed there would. */
async function pressEnterOn(target: Locator) {
  await target.focus();
  await target.press("Enter");
}

/**
 * A full-page screenshot in each theme. With the drawer open, the screen as the shopper sees it is saved too: a
 * full-page capture stretches the window, so the drawer, fixed to it, isn't where it would be.
 */
async function screenshot(page: Page, width: number, name: string, drawerOpen: boolean) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  for (const theme of ["light", "dark"] as Theme[]) {
    await setTheme(page, theme);
    for (const fullPage of drawerOpen ? [true, false] : [true]) {
      await page.screenshot({
        path: join(
          SHOTS,
          `${width}-${theme}-results-sources-${name}${fullPage ? "" : "-screen"}.png`,
        ),
        fullPage,
        animations: "disabled",
        timeout: 10_000,
      });
    }
  }
  await setTheme(page, "light");
}

test.describe("where each result comes from", () => {
  test.describe.configure({ mode: "parallel", timeout: 240_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  test("each card lists its main reasons, with state and source links", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "success");
    const reasons = reasonsOf(productA(page));
    await expect(reasons.getByRole("listitem")).toHaveCount(3);
    await expect(reasons.getByRole("listitem").first()).toContainText("Known");
    await expect(reasons.getByRole("listitem").first()).toContainText("points");
    const links = reasons.getByRole("link");
    expect(await links.count()).toBeGreaterThanOrEqual(3);
    for (const link of await links.all()) {
      await expect(link).toHaveAttribute("target", "_blank");
      await expect(link).toHaveAttribute("rel", "noopener noreferrer");
      await expect(link).toHaveAccessibleName(/\(opens in a new tab\)$/);
      expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(MIN_TARGET);
    }
    await expect(reasons.getByRole("button", { name: /^Where this comes from: \S/ })).toHaveCount(
      3,
    );
    // Every value on the card: none is ever shown as 0 points.
    await expect(reasons.getByText(/^\+0 points$/)).toHaveCount(0);

    // The explanation's sentence links to the source it rests on.
    const sentenceLink = productA(page).locator("[data-explanation]").getByRole("link").first();
    await expect(sentenceLink).toBeVisible();
    await expect(sentenceLink).toHaveAttribute("target", "_blank");
    await expect(sentenceLink).toHaveAttribute("rel", "noopener noreferrer");
    await expect(sentenceLink).toHaveAccessibleName(/\(opens in a new tab\)$/);
    await expect(sentenceLink).toHaveAttribute("href", /^https:\/\/example\.com\//);
  });

  test("an answer for a drawer that was closed never shows in the next one", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "disputed");
    const reasons = reasonsOf(productA(page));
    const first = reasons.getByRole("button").first();
    const disputed = reasons
      .getByRole("listitem")
      .filter({ hasText: "Disputed" })
      .getByRole("button");
    // Hold time still, so each answer (250 ms in the mock) arrives exactly when the test lets it.
    await page.clock.pauseAt((await page.evaluate(() => Date.now())) + 1_000);

    await pressEnterOn(first);
    await expect(drawer(page)).toBeVisible();
    await page.clock.runFor(100);
    await page.keyboard.press("Escape");
    await expect(drawer(page)).toHaveCount(0);
    await pressEnterOn(disputed);
    const d = drawer(page);
    const name = (await disputed.getAttribute("aria-label"))!.replace(
      "Where this comes from: ",
      "",
    );
    await expect(d).toHaveAccessibleName(`${name}: where this comes from`);

    // The first opening's answer is due now; the second's is not.
    await page.clock.runFor(200);
    await expect(d.getByText(/Loading the sources/)).toBeVisible();
    await expect(d.locator("blockquote")).toHaveCount(0);
    await expect(d).not.toContainText("Sample Tech Reviews");

    await page.clock.runFor(200);
    await expect(d.getByRole("region", { name: "What supports it" })).toContainText(
      "Sample Store One listing",
    );
    await expect(d).not.toContainText("Sample Tech Reviews");
  });

  test("Enter opens the sources with the exact quote; Escape closes them and gives focus back", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "success");
    const stop = await tabTo(page, (name) => /Where this comes from/.test(name));
    expect(stop.indicator, "the button shows it has focus").toBe(true);
    const opener = reasonsOf(productA(page)).getByRole("button").first();
    await expect(opener).toBeFocused();
    const name = (await opener.getAttribute("aria-label"))!.replace("Where this comes from: ", "");

    await page.keyboard.press("Enter");
    const d = drawer(page);
    await expect(d).toBeVisible();
    await expect(d).toHaveAccessibleName(`${name}: where this comes from`);
    await expect(d.getByRole("button", { name: "Close" })).toBeFocused();
    await expect(d.locator("blockquote").first()).toContainText("out of 10");
    await expect(d).toContainText("Review site");
    await expect(d).toContainText(/Published \d{1,2} \w+ \d{4}/);
    await expect(d).toContainText("Known");
    const open = d.getByRole("link", { name: /^Open source/ }).first();
    await expect(open).toHaveAttribute("target", "_blank");
    await expect(open).toHaveAttribute("rel", "noopener noreferrer");
    await expect(open).toHaveAccessibleName(/\(opens in a new tab\)$/);

    // While it is open, Tab never reaches the page behind it.
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press("Tab");
      const where = await d.evaluate((el) =>
        el.contains(document.activeElement)
          ? "drawer"
          : document.activeElement === document.body
            ? "browser"
            : "page",
      );
      expect(where, "focus never goes behind the drawer").not.toBe("page");
    }

    await page.keyboard.press("Escape");
    await expect(d).toHaveCount(0);
    await expect(opener).toBeFocused();

    // The Close button does the same.
    await page.keyboard.press("Enter");
    await expect(drawer(page)).toBeVisible();
    await pressEnterOn(drawer(page).getByRole("button", { name: "Close" }));
    await expect(drawer(page)).toHaveCount(0);
    await expect(opener).toBeFocused();
  });

  test("a disputed value shows every source on both sides", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "disputed");
    const disputed = reasonsOf(productA(page))
      .getByRole("listitem")
      .filter({ hasText: "Disputed" });
    await expect(disputed).toHaveCount(1);
    await expect(disputed.getByRole("link", { name: /Sample Store One listing/ })).toBeVisible();
    await expect(disputed.getByRole("link", { name: /Sample maker spec sheet/ })).toBeVisible();
    await pressEnterOn(disputed.getByRole("button", { name: /Where this comes from/ }));

    const d = drawer(page);
    await expect(d).toContainText("Disputed");
    const supports = d.getByRole("region", { name: "What supports it" });
    const disagrees = d.getByRole("region", { name: "What disagrees" });
    await expect(supports).toContainText("Sample Store One listing");
    await expect(supports).toContainText("16 GB");
    await expect(supports.locator("blockquote")).toContainText("16 GB of memory");
    await expect(disagrees).toContainText("Sample maker spec sheet");
    await expect(disagrees).toContainText("8 GB");
    await expect(disagrees.locator("blockquote")).toContainText("8 GB");
    await page.keyboard.press("Escape");
    await expect(d).toHaveCount(0);
  });

  test("a quote that wasn't kept says why, and keeps the link", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "withheld-quote");
    const first = reasonsOf(productA(page)).getByRole("listitem").first();
    await expect(first.getByRole("link", { name: /r\/SampleShoppers/ })).toBeVisible();
    await pressEnterOn(first.getByRole("button", { name: /Where this comes from/ }));
    const d = drawer(page);
    await expect(d).toContainText(REDDIT_WITHHELD);
    await expect(d).toContainText("Reddit");
    await expect(
      d.getByRole("link", { name: /^Open source from r\/SampleShoppers / }),
    ).toHaveAttribute("href", /^https:\/\/www\.reddit\.com\//);
  });

  test("an estimated value says no source gave it, and quotes nothing", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "estimated");
    const estimated = reasonsOf(productA(page))
      .getByRole("listitem")
      .filter({ hasText: "Estimated" });
    await expect(estimated).toHaveCount(1);
    await expect(estimated.getByRole("link")).toHaveCount(0);
    await pressEnterOn(estimated.getByRole("button", { name: /Where this comes from/ }));
    const d = drawer(page);
    await expect(d).toContainText("Estimated from similar products; no source gave it directly.");
    await expect(d.locator("blockquote")).toHaveCount(0);
  });

  test("an unknown value is never shown as 0", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "unknown");
    const card = productA(page);
    await pressEnterOn(card.locator("summary", { hasText: "Show all criteria" }));
    await expect(card.getByText("Not verified")).toBeVisible();
    await expect(reasonsOf(card).getByText(/^\+?0 points$/)).toHaveCount(0);
  });

  test("left out stays folded away, with its reason inside", async ({ page }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "left-out");
    const details = page.locator("details").filter({ hasText: "Left out (2)" });
    await expect(details).not.toHaveAttribute("open", "");
    const reason = page.getByText("Over your budget at every store checked.");
    await expect(reason).toBeHidden();
    await pressEnterOn(details.locator("summary"));
    await expect(reason).toBeVisible();
  });

  test("products we couldn't check have their own section and a way to research them", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "couldnt-check");
    const heading = page.getByRole("heading", { level: 2, name: "Couldn't check (1)" });
    await expect(heading).toBeVisible();
    const section = page.getByRole("region", { name: "Couldn't check (1)" });
    await expect(section).toContainText("Sample laptop F");
    await expect(section).toContainText("Store pages didn't list its specs.");
    const link = section.getByRole("link", { name: /^Research it/ });
    await expect(link).toBeVisible();
    await expect(link).toHaveAccessibleName("Research it for Sample laptop F (opens in a new tab)");
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  test("with nothing left, the relaxation hint says which must-have to loosen", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await showScenario(page, id, "relaxation");
    await expect(page.getByText(/^Sample hint: no option met every must-have/)).toBeVisible();
    await expect(page.getByText(/^Must-have: /)).toBeVisible();
    await expect(page.getByRole("link", { name: "Change must-haves" })).toBeVisible();
  });

  for (const size of SIZES) {
    test(`axe and screenshots at ${size.width} px`, async ({ page }) => {
      await page.setViewportSize(size);
      const id = await reachBrief(page);
      const states: [string, string, ((p: Page) => Promise<void>) | null][] = [
        ["success", "reasons", null],
        [
          "success",
          "drawer-known",
          (p) => pressEnterOn(reasonsOf(productA(p)).getByRole("button").first()),
        ],
        [
          "disputed",
          "drawer-disputed",
          (p) =>
            pressEnterOn(
              reasonsOf(productA(p))
                .getByRole("listitem")
                .filter({ hasText: "Disputed" })
                .getByRole("button"),
            ),
        ],
        [
          "withheld-quote",
          "drawer-withheld",
          (p) => pressEnterOn(reasonsOf(productA(p)).getByRole("button").first()),
        ],
        [
          "estimated",
          "drawer-estimated",
          (p) =>
            pressEnterOn(
              reasonsOf(productA(p))
                .getByRole("listitem")
                .filter({ hasText: "Estimated" })
                .getByRole("button"),
            ),
        ],
        ["left-out", "left-out", null],
        ["couldnt-check", "couldnt-check", null],
        ["relaxation", "relaxation", null],
      ];
      for (const [scenario, name, open] of states) {
        await showScenario(page, id, scenario);
        if (open) {
          await open(page);
          const d = drawer(page);
          await expect(d.getByText(/Loading the sources/)).toHaveCount(0);
          await expect(d.getByRole("button", { name: "Close" })).toBeVisible();
          for (const control of await d.locator("a, button").all()) {
            const box = (await control.boundingBox())!;
            expect(box.height, "drawer targets are at least 44 px").toBeGreaterThanOrEqual(
              MIN_TARGET,
            );
          }
          const width = await d.evaluate((el) => el.getBoundingClientRect().width);
          expect(width).toBeLessThanOrEqual(size.width);
        }
        await checkRow(page, `results sources, ${name} (${size.width} px)`);
        await screenshot(page, size.width, name, open !== null);
        if (open) {
          await page.keyboard.press("Escape");
          await expect(drawer(page)).toHaveCount(0);
        }
      }
    });
  }
});
