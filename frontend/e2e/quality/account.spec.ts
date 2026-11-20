/**
 * US-30 login screens, in mock mode: "Sign in" opens the sign-in page, and a signed-in shopper's initials open their
 * account menu (My searches, their profile, privacy, Sign out). My searches lists newest first with "Show more" and
 * opens a saved result; the profile shows who is signed in and their searches. D-80: a sign-in or a sign-out starts a
 * new session ID. Guests see what they saw before. Every screen passes axe at 390 and 1440 px, light and dark, and works
 * from the keyboard alone.
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
const accountMenu = (page: Page) => nav(page).getByRole("button", { name: /account menu$/ });

async function openHome(page: Page) {
  await page.goto("/");
  await waitForHydration(page, "#research-query");
}

/** Signs in through the sign-in page, from the wide navigation. */
async function signIn(page: Page) {
  await nav(page).getByRole("link", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/sign-in/);
  await page.getByRole("button", { name: "Continue with Google" }).click();
  await expect(accountMenu(page)).toBeVisible();
}

async function signOut(page: Page) {
  await accountMenu(page).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(nav(page).getByRole("link", { name: "Sign in" })).toBeVisible();
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
  test.use({ viewport: { width: 1440, height: 900 } });

  test("is the default: Sign in, and no My searches", async ({ page }) => {
    await openHome(page);
    await expect(nav(page).getByRole("link", { name: "Sign in" })).toBeVisible();
    await expect(nav(page).getByRole("link", { name: "My searches" })).toHaveCount(0);
    await expect(nav(page).getByRole("link", { name: "Research" })).toBeVisible(); // what guests had
  });

  test("Sign in opens the sign-in page, which comes back to the page after", async ({ page }) => {
    await page.goto("/privacy");
    await nav(page).getByRole("link", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/sign-in\?next=/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Sign in to ShopSense" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Continue with Google" }).click();
    await expect(page).toHaveURL(/\/privacy$/);
    await expect(accountMenu(page)).toBeVisible();
  });

  test("the sign-in page lets a guest carry on without signing in", async ({ page }) => {
    await page.goto("/sign-in");
    await page.getByRole("link", { name: "Search as a guest" }).click();
    await expect(page).toHaveURL(/\/research$/);
  });

  test("My searches and the profile ask a guest to sign in", async ({ page }) => {
    for (const [path, title] of [
      ["/my-searches", "My searches"],
      ["/account", "Your profile"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
      await expect(page.getByRole("main").getByRole("link", { name: "Sign in" })).toBeVisible();
    }
  });

  test("a failed sign-in says so, in our words, and the address is cleaned", async ({ page }) => {
    await page.goto("/?error=access_denied&error_description=%3Cb%3Eraw%3C%2Fb%3E");
    await expect(page.getByRole("alert")).toHaveText(SIGN_IN_FAILED);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText("raw")).toHaveCount(0);
    await page.getByRole("button", { name: "Close this message" }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("a failed sign-in coming back to the sign-in page says so there", async ({ page }) => {
    await page.goto("/sign-in?error=access_denied");
    await expect(page.getByRole("alert")).toContainText(SIGN_IN_FAILED);
  });

  test("an expired session falls back to a guest", async ({ page }) => {
    await page.addInitScript(() =>
      sessionStorage.setItem("shopsense.auth.mock", JSON.stringify({ expiresAt: Date.now() - 1 })),
    );
    await openHome(page);
    await expect(nav(page).getByRole("link", { name: "Sign in" })).toBeVisible();
  });
});

test.describe("signed in", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the account menu holds the shopper, their places and Sign out", async ({ page }) => {
    await openHome(page);
    await signIn(page);
    await expect(nav(page).getByRole("link", { name: "My searches" })).toBeVisible();
    await accountMenu(page).click();
    const menu = page.getByRole("menu");
    await expect(menu).toContainText("Asha");
    await expect(menu).toContainText("asha@example.test");
    await expect(menu.getByRole("menuitem")).toHaveText([
      "My searches",
      "Your profile",
      "Privacy",
      "Sign out",
    ]);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(accountMenu(page)).toBeFocused();
    await signOut(page);
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
    await page.getByRole("main").getByRole("link", { name: "My searches" }).click();
    await expect(page).toHaveURL(/\/my-searches$/);
  });

  test("My searches can be filtered by what was asked", async ({ page }) => {
    await openHome(page);
    await signIn(page);
    await page.goto("/my-searches");
    const list = page.getByRole("main").getByRole("listitem");
    await expect(list).toHaveCount(20);
    await page.getByRole("searchbox", { name: "Filter your searches" }).fill("air fryer");
    await expect(list).toHaveCount(5);
    await expect(list.first()).toContainText("Air fryer for a family of four");
    await page.getByRole("searchbox", { name: "Filter your searches" }).fill("no such thing");
    await expect(page.getByText("No saved search matches")).toBeVisible();
  });

  test("Show more while filtering moves focus to the first new search that matches", async ({
    page,
  }) => {
    await openHome(page);
    await signIn(page);
    await page.goto("/my-searches");
    const list = page.getByRole("main").getByRole("listitem");
    await expect(list).toHaveCount(20);
    await page.getByRole("searchbox", { name: "Filter your searches" }).fill("earbuds");
    await expect(list).toHaveCount(5);
    // The next page starts with a laptop, hidden by the filter; the earbuds after it gets focus.
    await page.getByRole("button", { name: "Show more" }).click();
    await expect(list).toHaveCount(6);
    await expect(list.nth(5).getByRole("link")).toBeFocused();
    await expect(list.nth(5)).toContainText("earbuds");
  });

  test("the profile shows who is signed in and their searches", async ({ page }) => {
    await openHome(page);
    await signIn(page);
    await accountMenu(page).click();
    await page.getByRole("menuitem", { name: "Your profile" }).click();
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByRole("heading", { level: 1, name: "Asha" })).toBeVisible();
    await expect(page.getByText("asha@example.test")).toBeVisible();
    await expect(page.getByText("20+")).toBeVisible();
    await page.getByRole("link", { name: /Open My searches/ }).click();
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

  test("the sign-in page knows the shopper is signed in", async ({ page }) => {
    await openHome(page);
    await signIn(page);
    await page.goto("/sign-in");
    await expect(page.getByRole("heading", { level: 1, name: "You're signed in" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Go to My searches/ })).toBeVisible();
  });
});

test.describe("a new session ID at sign-in and sign-out (D-80)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

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
    await signOut(page);
    expect(await searchKeys(page)).toEqual([]);
    const nextGuestSession = await reachCriteria(page);
    expect(nextGuestSession).not.toBe(accountSession);
  });

  test("signing in from a search's own page comes back home, as that search has ended", async ({
    page,
  }) => {
    await reachCriteria(page);
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
    await nav(page).getByRole("link", { name: "Sign in" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/sign-in/);
    await page.getByRole("button", { name: "Continue with Google" }).focus();
    await page.keyboard.press("Enter");
    await expect(accountMenu(page)).toBeVisible();
    // The account menu, from the keyboard: Down opens it on the first item.
    await accountMenu(page).focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "My searches" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/my-searches$/);
    await page.getByRole("button", { name: "Show more" }).focus();
    await page.keyboard.press("Enter");
    const list = page.getByRole("main").getByRole("listitem");
    await expect(list.nth(20).getByRole("link")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  });

  test("the small-screen menu holds My searches, the profile and Sign out when signed in", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openHome(page);
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("banner").getByRole("link", { name: "Sign in" }).click();
    await page.getByRole("button", { name: "Continue with Google" }).click();
    await page.getByRole("button", { name: "Open menu" }).click();
    const menu = page.getByRole("banner");
    await expect(menu.getByRole("link", { name: "My searches" })).toBeVisible();
    await expect(menu.getByRole("link", { name: "Your profile" })).toBeVisible();
    await expect(menu.getByRole("button", { name: "Sign out" })).toBeVisible();
  });
});

test.describe("axe, at 390 and 1440 px, light and dark", () => {
  test("the sign-in page, the guest prompts, the sign-in error", async ({ page }) => {
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/sign-in");
      await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
      await checkRow(page, `sign-in (${size.width} px)`);
      await page.goto("/sign-in?error=access_denied");
      await expect(page.getByRole("alert")).toBeVisible();
      await checkRow(page, `sign-in error (${size.width} px)`);
      await page.goto("/my-searches");
      await expect(page.getByRole("main").getByRole("link", { name: "Sign in" })).toBeVisible();
      await checkRow(page, `my searches, guest (${size.width} px)`);
      await page.goto("/?error=access_denied");
      await expect(page.getByRole("alert")).toBeVisible();
      await checkRow(page, `home, sign-in error (${size.width} px)`);
    }
  });

  test("signed in: My searches, a saved result, not found, the profile, and both menus", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openHome(page);
    await signIn(page);
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/my-searches");
      await expect(page.getByRole("main").getByRole("listitem")).toHaveCount(20);
      await checkRow(page, `my searches (${size.width} px)`);
      const filter = page.getByRole("searchbox", { name: "Filter your searches" });
      await filter.fill("no such thing");
      await expect(page.getByText(/^No saved search matches/)).toBeVisible();
      await checkRow(page, `my searches, nothing matches (${size.width} px)`);
      await filter.fill("");
      await page.getByRole("main").getByRole("listitem").first().getByRole("link").click();
      await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
      await checkRow(page, `saved result (${size.width} px)`);
      await page.goto(SOMEONE_ELSES);
      await expect(page.getByRole("heading", { level: 1, name: NOT_FOUND })).toBeVisible();
      await checkRow(page, `not found (${size.width} px)`);
      await page.goto("/account");
      await expect(page.getByRole("heading", { level: 1, name: "Asha" })).toBeVisible();
      await expect(page.getByText("20+")).toBeVisible();
      await checkRow(page, `profile (${size.width} px)`);
      await page.goto("/sign-in");
      await expect(page.getByRole("heading", { level: 1, name: "You're signed in" })).toBeVisible();
      await checkRow(page, `sign-in, signed in (${size.width} px)`);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await openHome(page);
    await accountMenu(page).click();
    await expect(page.getByRole("menu")).toBeVisible();
    await checkRow(page, "account menu (1440 px)");
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 390, height: 844 });
    await openHome(page);
    await page.getByRole("button", { name: "Open menu" }).click();
    await checkRow(page, "small-screen menu signed in (390 px)");
  });
});
