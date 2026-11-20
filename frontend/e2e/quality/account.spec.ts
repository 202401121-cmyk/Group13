/**
 * US-30 login screens, in mock mode: "Sign in" or the shopper's name with "Sign out", My searches (newest first,
 * "Show more", a saved result), and D-80 (a sign-in or a sign-out starts a new session ID). Guests see what they saw
 * before. Every screen passes axe at 390 and 1440 px, light and dark, and works from the keyboard alone.
 *
 * The mock signs in as a fake shopper, Asha, with no network (src/lib/auth/mockService.ts).
 */
import { expect, test, type Page } from "@playwright/test";
import { checkRow, reachCriteria, waitForHydration } from "./helpers";

const SIGN_IN_FAILED = "We couldn't sign you in with Google. Please try again.";
const NOT_FOUND = "We couldn't find that saved search.";
const SOMEONE_ELSES = "/my-searches/3f2a9c1e-8b7d-4e6f-a5c4-1d2e3f4a5b6c";
const SIZES = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
];

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });

async function openHome(page: Page) {
  await page.goto("/");
  await waitForHydration(page, "#research-query");
}

async function signIn(page: Page) {
  await nav(page).getByRole("button", { name: "Sign in" }).click();
  await expect(nav(page).getByRole("button", { name: /Sign out/ })).toBeVisible();
}

/** The keys of ours that belong to a search, in this tab: none after a sign-in or a sign-out (D-80). */
function searchKeys(page: Page) {
  return page.evaluate(() =>
    Object.keys(sessionStorage).filter(
      (k) => k.startsWith("shopsense.") && !k.startsWith("shopsense.auth."),
    ),
  );
}

test.describe("signed out", () => {
  test("is the default: Sign in, and no My searches", async ({ page }) => {
    await openHome(page);
    await expect(nav(page).getByRole("button", { name: "Sign in" })).toBeVisible();
    await expect(nav(page).getByRole("link", { name: "My searches" })).toHaveCount(0);
    await expect(nav(page).getByRole("link", { name: "Research" })).toBeVisible(); // what guests had
  });

  test("My searches asks a guest to sign in", async ({ page }) => {
    await page.goto("/my-searches");
    await expect(page.getByRole("heading", { level: 1, name: "My searches" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in with Google" })).toBeVisible();
  });

  test("a failed sign-in says so, in our words, and the address is cleaned", async ({ page }) => {
    await page.goto("/?error=access_denied&error_description=%3Cb%3Eraw%3C%2Fb%3E");
    await expect(page.getByRole("alert")).toHaveText(SIGN_IN_FAILED);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText("raw")).toHaveCount(0);
    await page.getByRole("button", { name: "Close this message" }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("an expired session falls back to a guest", async ({ page }) => {
    await page.addInitScript(() =>
      sessionStorage.setItem("shopsense.auth.mock", JSON.stringify({ expiresAt: Date.now() - 1 })),
    );
    await openHome(page);
    await expect(nav(page).getByRole("button", { name: "Sign in" })).toBeVisible();
  });
});

test.describe("signed in", () => {
  test("shows the name, Sign out, and My searches; Sign out goes back to a guest", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openHome(page);
    await signIn(page);
    await expect(nav(page).getByText("Asha", { exact: true })).toBeVisible();
    await expect(nav(page).getByRole("link", { name: "My searches" })).toBeVisible();
    await nav(page)
      .getByRole("button", { name: /Sign out/ })
      .click();
    await expect(nav(page).getByRole("button", { name: "Sign in" })).toBeVisible();
    await expect(nav(page).getByRole("link", { name: "My searches" })).toHaveCount(0);
  });

  test("My searches lists newest first, shows more, and opens a saved result", async ({ page }) => {
    await openHome(page);
    await signIn(page);
    await page.goto("/my-searches");
    const list = page.getByRole("main").getByRole("listitem");
    await expect(list).toHaveCount(20);
    const times = await list.evaluateAll((items) => items.map((li) => li.textContent ?? ""));
    expect(times[0]).toContain("20 Nov 2026");
    await page.getByRole("button", { name: "Show more" }).click();
    await expect(list).toHaveCount(23);
    await expect(page.getByRole("button", { name: "Show more" })).toHaveCount(0);
    // Focus moved to the first new search, so a keyboard user carries on from there.
    await expect(list.nth(20).getByRole("link")).toBeFocused();

    await list.first().getByRole("link").click();
    await expect(page).toHaveURL(/\/my-searches\/[0-9a-f-]{36}$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Results for your laptop" }),
    ).toBeVisible();
    await expect(page.getByText(/^Saved .*You asked:/)).toBeVisible();
    await expect(page.getByText("This result isn't saved.")).toHaveCount(0);
    await page.getByRole("link", { name: "My searches" }).first().click();
    await expect(page).toHaveURL(/\/my-searches$/);
  });

  test("an empty list says so", async ({ page }) => {
    await page.addInitScript(() => sessionStorage.setItem("shopsense.auth.mock.empty", "1"));
    await openHome(page);
    await signIn(page);
    await page.goto("/my-searches");
    await expect(page.getByText("No saved searches yet.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Start a search" })).toBeVisible();
  });

  test("someone else's saved search shows not found", async ({ page }) => {
    await openHome(page);
    await signIn(page);
    await page.goto(SOMEONE_ELSES);
    await expect(page.getByRole("heading", { level: 1, name: NOT_FOUND })).toBeVisible();
    await expect(page.getByRole("link", { name: "Go to My searches" })).toBeVisible();
  });
});

test.describe("a new session ID at sign-in and sign-out (D-80)", () => {
  test("a guest's search ends at sign-in, and the signed-in one ends at sign-out", async ({
    page,
  }) => {
    const guestSession = await reachCriteria(page);
    expect(await searchKeys(page)).not.toEqual([]);

    await openHome(page);
    await signIn(page);
    expect(await searchKeys(page)).toEqual([]);
    const accountSession = await reachCriteria(page);
    expect(accountSession).not.toBe(guestSession); // the next request went through intake again

    await openHome(page);
    await nav(page)
      .getByRole("button", { name: /Sign out/ })
      .click();
    await expect(nav(page).getByRole("button", { name: "Sign in" })).toBeVisible();
    expect(await searchKeys(page)).toEqual([]);
    const nextGuestSession = await reachCriteria(page);
    expect(nextGuestSession).not.toBe(accountSession);
  });

  test("signing in on a search's own page comes back home, as that search has ended", async ({
    page,
  }) => {
    await reachCriteria(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page);
    await expect(page).toHaveURL(/\/$/);
  });
});

test.describe("keyboard only", () => {
  test("sign in, open My searches, show more and open a search, all from the keyboard", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openHome(page);
    await nav(page).getByRole("button", { name: "Sign in" }).focus();
    await page.keyboard.press("Enter");
    await expect(nav(page).getByRole("button", { name: /Sign out/ })).toBeVisible();
    await nav(page).getByRole("link", { name: "My searches" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/my-searches$/);
    await page.getByRole("button", { name: "Show more" }).focus();
    await page.keyboard.press("Enter");
    const list = page.getByRole("main").getByRole("listitem");
    await expect(list.nth(20).getByRole("link")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  });

  test("the small-screen menu holds Sign out and My searches when signed in", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openHome(page);
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(page.getByRole("link", { name: "My searches" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  });
});

test.describe("axe, at 390 and 1440 px, light and dark", () => {
  test("the navigation signed in, My searches, a saved result, not found, and the guest prompt", async ({
    page,
  }) => {
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/my-searches");
      await expect(page.getByRole("button", { name: "Sign in with Google" })).toBeVisible();
      await checkRow(page, `my searches, guest (${size.width} px)`);
    }
    await openHome(page);
    await signIn(page);
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/my-searches");
      await expect(page.getByRole("main").getByRole("listitem")).toHaveCount(20);
      await checkRow(page, `my searches (${size.width} px)`);
      await page.getByRole("main").getByRole("listitem").first().getByRole("link").click();
      await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
      await checkRow(page, `saved result (${size.width} px)`);
      await page.goto(SOMEONE_ELSES);
      await expect(page.getByRole("heading", { level: 1, name: NOT_FOUND })).toBeVisible();
      await checkRow(page, `not found (${size.width} px)`);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await openHome(page);
    await page.getByRole("button", { name: "Open menu" }).click();
    await checkRow(page, "menu signed in (390 px)");
  });

  test("a failed sign-in's message", async ({ page }) => {
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/?error=access_denied");
      await expect(page.getByRole("alert")).toBeVisible();
      await checkRow(page, `sign-in error (${size.width} px)`);
    }
  });
});
