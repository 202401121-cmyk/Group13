/**
 * The small-screen menu holds focus while it is open (WCAG 2.1.2, 2.4.3, 2.4.11): Tab goes round the menu and
 * nothing else, the page behind is inert, and closing it gives focus back to the menu button. The last test
 * checks that a focused element also stays clear of the sticky header when the menu is shut.
 */
import { expect, test, type Page } from "@playwright/test";
import { checkRow, waitForHydration } from "./helpers";
import { focusStop } from "./layout";

const PHONE = { width: 390, height: 844 };

/** What a screen reader would call the focused element. */
function focusedName(page: Page) {
  return page.evaluate(() => {
    const el = document.activeElement;
    return (el?.getAttribute("aria-label") ?? el?.textContent ?? "").trim();
  });
}

async function openHome(page: Page) {
  await page.setViewportSize(PHONE);
  await page.goto("/");
  await waitForHydration(page, "#research-query");
}

async function openMenu(page: Page) {
  await page.getByRole("button", { name: "Open menu" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Close menu" })).toBeVisible();
}

async function pressAndRead(page: Page, key: string, times: number) {
  const seen: string[] = [];
  for (let i = 0; i < times; i++) {
    await page.keyboard.press(key);
    seen.push(await focusedName(page));
  }
  return seen;
}

const isInert = (page: Page, selector: string) =>
  page.locator(selector).evaluate((el) => el.closest("[inert]") !== null);

test.describe("small-screen menu at 390 px", () => {
  test.beforeEach(async ({ page }) => {
    await openHome(page);
  });

  test("focus moves in, and Tab and Shift+Tab go round the menu", async ({ page }) => {
    await openMenu(page);
    expect(await focusedName(page)).toBe("Home");

    expect(await pressAndRead(page, "Tab", 5)).toEqual([
      "Research",
      "Get started",
      "Close menu",
      "Home",
      "Research",
    ]);
    expect(await pressAndRead(page, "Shift+Tab", 5)).toEqual([
      "Home",
      "Close menu",
      "Get started",
      "Research",
      "Home",
    ]);
  });

  test("every stop in the menu shows focus and is on screen", async ({ page }) => {
    await openMenu(page);
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press(i < 4 ? "Tab" : "Shift+Tab");
      const stop = await focusStop(page);
      expect(stop, stop.name).toMatchObject({ indicator: true, obscured: "no", inView: true });
    }
  });

  test("nothing behind the open menu can take focus", async ({ page }) => {
    await openMenu(page);
    for (const behind of ["main", "footer", 'header a[aria-label="ShopSense home"]']) {
      expect(await isInert(page, behind), behind).toBe(true);
    }
    expect(await isInert(page, "header button[aria-expanded]")).toBe(false);

    // The steps that found the fault: all the way down, then back up the page.
    const stops = [
      ...(await pressAndRead(page, "Tab", 40)),
      ...(await pressAndRead(page, "Shift+Tab", 40)),
    ];
    expect(new Set(stops)).toEqual(new Set(["Close menu", "Home", "Research", "Get started"]));

    // Even a script can't put focus on the page, the logo or the theme button: it stays where it was.
    const before = await focusedName(page);
    for (const selector of [
      "#research-query",
      'a[aria-label="ShopSense home"]',
      "button[aria-pressed]",
    ]) {
      await page
        .locator(`${selector}:visible`)
        .first()
        .evaluate((el: HTMLElement) => el.focus());
      expect(await focusedName(page), selector).toBe(before);
    }
  });

  test("Escape closes the menu and gives focus back", async ({ page }) => {
    await openMenu(page);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Close menu" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
    expect(await isInert(page, "main")).toBe(false);
    expect(await isInert(page, "footer")).toBe(false);
  });

  test("the close button closes the menu and gives focus back", async ({ page }) => {
    await openMenu(page);
    await page.keyboard.press("Shift+Tab");
    expect(await focusedName(page)).toBe("Close menu");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
    expect(await isInert(page, "main")).toBe(false);

    // The page takes focus again, in its usual order.
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest("main"))).toBe(true);
  });

  test("with a pointer, the close button closes the menu too", async ({ page }) => {
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("button", { name: "Close menu" }).click();
    await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
  });

  test("a menu link goes to its page and leaves nothing inert", async ({ page }) => {
    await openMenu(page);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/research$/);
    expect(await isInert(page, "main")).toBe(false);
  });

  test("a menu link to the page already open closes the menu and keeps focus", async ({ page }) => {
    await openMenu(page);
    await page.keyboard.press("Enter"); // "Home", and this is the home page
    await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
    expect(await isInert(page, "main")).toBe(false);
  });

  test("if the screen grows past the menu's size, the page is not left inert", async ({ page }) => {
    await openMenu(page);
    await page.setViewportSize({ width: 1280, height: 720 });
    await expect.poll(() => isInert(page, "main")).toBe(false);
    expect(await isInert(page, "footer")).toBe(false);
  });

  test("a screen too short for the menu scrolls it, so every link can be reached", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 200 });
    await openMenu(page);
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press("Tab");
      const stop = await focusStop(page);
      expect(stop, stop.name).toMatchObject({ inView: true });
    }
  });

  test("axe with the menu open, light and dark", async ({ page }) => {
    await openMenu(page);
    await checkRow(page, "menu open (390 px)");
  });
});

for (const size of [PHONE, { width: 1440, height: 900 }]) {
  test(`scroll padding clears the sticky header at ${size.width} px`, async ({ page }) => {
    await page.setViewportSize(size);
    await page.goto("/privacy");
    await page.evaluate(() => window.scrollTo({ top: 400, behavior: "instant" }));
    const { headerBottom, padding } = await page.evaluate(() => ({
      headerBottom: document.querySelector("header")!.getBoundingClientRect().bottom,
      padding: parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop),
    }));
    expect(headerBottom).toBeGreaterThan(0);
    expect(padding).toBeGreaterThanOrEqual(headerBottom);
  });
}
