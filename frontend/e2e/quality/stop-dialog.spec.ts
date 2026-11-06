/**
 * F-15: leaving the results page during research opens "Stop research?", and closing it must give focus back to
 * the control that opened it, or to the page's main heading if that control is gone. Never the page body. At
 * 390 px the dialog is reached through the small-screen menu, where the link that was pressed closes with the menu.
 */
import { expect, test, type Page } from "@playwright/test";
import { reachBrief } from "./helpers";
import { focusStop } from "./layout";

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };

const dialog = (page: Page) => page.getByRole("alertdialog", { name: "Stop research?" });
const menuButton = (page: Page) => page.getByRole("button", { name: "Open menu" });
const navHome = (page: Page) =>
  page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Home", exact: true });
const heading = (page: Page) =>
  page.getByRole("heading", { level: 1, name: "Researching laptops for you" });

/** Starts research and holds the page clock, so it can't finish while the dialog is being tested. */
async function researching(page: Page) {
  await reachBrief(page);
  await page.getByRole("button", { name: /Start research/ }).click();
  await expect(page).toHaveURL(/\/results$/);
  await page.clock.runFor(1_500);
  await expect(heading(page)).toBeVisible();
  const now = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(now + 250);
}

async function openFromMenu(page: Page) {
  await menuButton(page).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Close menu" })).toBeVisible();
  await page.keyboard.press("Enter"); // focus starts on the menu's first link, "Home"
  await expect(dialog(page)).toBeVisible();
}

async function openFromNav(page: Page) {
  await navHome(page).focus();
  await page.keyboard.press("Enter");
  await expect(dialog(page)).toBeVisible();
}

const CLOSE: [string, (page: Page) => Promise<void>][] = [
  ["Escape", (page) => page.keyboard.press("Escape")],
  ["Keep researching", (page) => page.getByRole("button", { name: "Keep researching" }).click()],
];

/** The dialog gives focus back in a zero-delay timer, so the clock has to run again first. */
async function expectClosed(page: Page) {
  await expect(dialog(page)).toBeHidden();
  await page.clock.resume();
  await expect(page).toHaveURL(/\/results$/);
}

async function expectResearchGoesOn(page: Page) {
  await expect(page.getByRole("heading", { name: /What ShopSense is doing/ })).toBeVisible();
}

test.describe("focus after Stop research?", () => {
  test.describe.configure({ mode: "parallel", timeout: 120_000 });

  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  for (const [name, close] of CLOSE) {
    test(`at 390 px, ${name} gives focus back to the menu button`, async ({ page }) => {
      await page.setViewportSize(PHONE);
      await researching(page);
      await openFromMenu(page);
      await close(page);
      await expectClosed(page);
      await expect(menuButton(page)).toBeFocused();
      expect((await focusStop(page)).isBody).toBe(false);
      await expectResearchGoesOn(page);
    });

    test(`at 1440 px, ${name} gives focus back to the link that opened it`, async ({ page }) => {
      await page.setViewportSize(DESKTOP);
      await researching(page);
      await openFromNav(page);
      await close(page);
      await expectClosed(page);
      await expect(navHome(page)).toBeFocused();
      await expectResearchGoesOn(page);
    });

    test(`if the opener is gone, ${name} moves focus to the main heading`, async ({ page }) => {
      await page.setViewportSize(DESKTOP);
      await researching(page);
      await openFromNav(page);
      // The nav links exist only on wide screens: shrinking the window takes the opener away.
      await page.setViewportSize(PHONE);
      await expect(navHome(page)).toBeHidden();
      await close(page);
      await expectClosed(page);
      await expect(heading(page)).toBeFocused();
      const stop = await focusStop(page);
      expect(stop).toMatchObject({ isBody: false, obscured: "no", inView: true });
      await expectResearchGoesOn(page);
    });
  }

  test("focus stays in the dialog while it is open at 390 px", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await researching(page);
    await openFromMenu(page);
    await expect(page.getByRole("button", { name: "Keep researching" })).toBeFocused();
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press("Tab");
      const inside = await dialog(page).evaluate((d) => d.contains(document.activeElement));
      expect(inside, "focus stays in the dialog").toBe(true);
    }
  });
});
