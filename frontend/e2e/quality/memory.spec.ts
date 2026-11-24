/**
 * US-11 "What we remember" and deleting the account, on /account in mock mode: the list in plain words with each
 * category; changing a priority with its number box; Forget with 5 seconds to undo; the .json download with no
 * embedding; Forget everything; the empty state; a 503 with "Try again"; a 404 or 409 that refreshes the list; and the
 * delete dialog, which refuses anything but "delete", moves focus in and out correctly, and ends on a plain page,
 * signed out. Everything works from the keyboard alone, and axe finds nothing serious or critical at 390 and 1440 px,
 * light and dark. Set SHOTS_DIR to save the pull request's screenshots.
 *
 * The mock remembers seven things for the fake shopper, Asha (src/lib/account/memoryMock.ts). MOCK_MEMORY_KEY in the
 * tab picks the other states.
 */
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { checkRow, setTheme, signInTo, waitForHydration, type Theme } from "./helpers";

const SHOTS = process.env["SHOTS_DIR"];
const KEY = "shopsense.auth.mock.memory";
const DELETED = "shopsense.auth.deleted";
const EMPTY =
  "Nothing remembered yet. Finish an interview while signed in, and your priorities show here.";
const UNAVAILABLE =
  "What ShopSense remembers can't be reached right now. Searching still works. Please try again in a minute.";
const BATTERY = "Battery life: high importance";
const SIZES = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
];

const memory = (page: Page) => page.getByRole("region", { name: "What we remember" });
const items = (page: Page) =>
  memory(page).getByRole("list", { name: "What we remember" }).getByRole("listitem");
const item = (page: Page, text: string) => items(page).filter({ hasText: text });

/** Signs in and opens the profile, in a mock state if one is given, with the memory list drawn. */
async function openMemory(page: Page, state?: string) {
  if (state) await page.addInitScript(([k, v]) => sessionStorage.setItem(k!, v!), [KEY, state]);
  await signInTo(page, "/account");
  await expect(memory(page).getByText(/Loading what we remember/)).toHaveCount(0);
}

test.describe("what we remember", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("lists each item in plain words with its category, kind and weight", async ({ page }) => {
    await openMemory(page);
    await expect(items(page)).toHaveCount(7);
    await expect(item(page, BATTERY)).toContainText("Priority");
    await expect(item(page, BATTERY)).toContainText("Laptop");
    await expect(item(page, BATTERY)).toContainText("Weight 8 of 10");
    await expect(item(page, "Leave out: Zenith")).toContainText("Every search");
    await expect(item(page, "no viewing window")).toContainText("Air fryer");
    await expect(item(page, "IPX4")).toContainText("Switched off");
  });

  test("a priority changes with its number box, and the app's own words follow the weight", async ({
    page,
  }) => {
    await openMemory(page);
    await item(page, BATTERY)
      .getByRole("button", { name: `Change "${BATTERY}"` })
      .click();
    const box = page.getByRole("spinbutton", { name: "Weight, 0 to 10" });
    await expect(box).toBeFocused();
    await box.fill("5");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(item(page, "Battery life: medium importance")).toContainText("Weight 5 of 10");
    await expect(memory(page).getByRole("status")).toHaveText(
      "Saved: Battery life: medium importance",
    );
    await expect(
      page.getByRole("button", { name: 'Change "Battery life: medium importance"' }),
    ).toBeFocused();
  });

  test("Undo restores a forgotten item, and nothing is deleted", async ({ page }) => {
    await openMemory(page);
    await page.getByRole("button", { name: `Forget "${BATTERY}"` }).click();
    await expect(item(page, BATTERY)).toHaveCount(0);
    const undo = page.getByRole("button", { name: `Undo: keep "${BATTERY}"` });
    await expect(undo).toBeFocused();
    await undo.click();
    await expect(item(page, BATTERY)).toHaveCount(1);
    await expect(page.getByRole("button", { name: `Forget "${BATTERY}"` })).toBeFocused();
    await page.waitForTimeout(6_000); // past the 5 seconds: had the delete been sent, the item would now go
    await expect(item(page, BATTERY)).toHaveCount(1);
    await expect(items(page)).toHaveCount(7);
  });

  test("Forget deletes the item once the 5 seconds are up", async ({ page }) => {
    await openMemory(page);
    await page.getByRole("button", { name: `Forget "${BATTERY}"` }).click();
    await expect(page.getByRole("button", { name: /^Undo/ })).toHaveCount(0, { timeout: 8_000 });
    await expect(memory(page).getByRole("status")).toHaveText(`Forgotten: ${BATTERY}`);
    await expect(items(page)).toHaveCount(6);
  });

  test("Download my data saves shopsense-memory.json, with no embedding in it", async ({
    page,
  }) => {
    await openMemory(page);
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      memory(page).getByRole("button", { name: "Download my data" }).click(),
    ]);
    expect(download.suggestedFilename()).toBe("shopsense-memory.json");
    const text = readFileSync((await download.path())!, "utf-8");
    const file = JSON.parse(text) as { exported_at: string; items: { text: string }[] };
    expect(file.items).toHaveLength(7);
    expect(file.items.map((i) => i.text)).toContain(BATTERY);
    expect(text).not.toMatch(/embedding|user_id|email/);
  });

  test("Forget everything asks first: Keep them changes nothing, and the answer empties the list", async ({
    page,
  }) => {
    await openMemory(page);
    const opener = memory(page).getByRole("button", { name: "Forget everything" });
    await opener.click();
    const dialog = page.getByRole("alertdialog", { name: "Forget everything?" });
    await expect(dialog).toContainText("all 7 things");
    await expect(dialog.getByRole("button", { name: "Keep them" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
    await expect(items(page)).toHaveCount(7);
    await opener.click();
    await dialog.getByRole("button", { name: "Forget everything" }).click();
    await expect(memory(page).getByText(EMPTY)).toBeVisible();
    await expect(memory(page).getByRole("heading", { name: "What we remember" })).toBeFocused();
  });

  test("says plainly when nothing is remembered yet", async ({ page }) => {
    await openMemory(page, "empty");
    await expect(memory(page).getByText(EMPTY)).toBeVisible();
    await expect(memory(page).getByRole("button", { name: "Forget everything" })).toHaveCount(0);
  });

  test("a 503 says the memory is unavailable right now, and Try again works", async ({ page }) => {
    await openMemory(page, "unavailable");
    await expect(memory(page).getByRole("alert")).toContainText(UNAVAILABLE);
    await memory(page).getByRole("button", { name: "Try again" }).click();
    await expect(items(page)).toHaveCount(7);
  });

  test("a preference already gone refreshes the list and says why", async ({ page }) => {
    await openMemory(page, "gone");
    await page.getByRole("button", { name: `Forget "${BATTERY}"` }).click();
    await expect(memory(page).getByRole("alert")).toHaveText(
      "That preference was already gone, so we refreshed the list.",
      { timeout: 8_000 },
    );
    await expect(item(page, BATTERY)).toHaveCount(0);
    await expect(items(page)).toHaveCount(6);
  });

  test("a preference changed meanwhile refreshes the list and says why", async ({ page }) => {
    await openMemory(page, "changed");
    await page.getByRole("button", { name: `Change "${BATTERY}"` }).click();
    await page.getByRole("spinbutton", { name: "Weight, 0 to 10" }).fill("3");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(memory(page).getByRole("alert")).toHaveText(
      "This preference changed while you were editing it. Please look at it again and retry.",
    );
    await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
  });

  test("new words that couldn't be matched yet show the note", async ({ page }) => {
    await openMemory(page, "unmatched");
    await page.getByRole("button", { name: `Change "${BATTERY}"` }).click();
    await page.getByRole("textbox", { name: "In your words" }).fill("Lasts a whole college day");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(item(page, "Lasts a whole college day")).toContainText(
      "Your new words are saved, but we couldn't work out their meaning just now",
    );
  });

  test("a must-have switches off and on", async ({ page }) => {
    await openMemory(page);
    const rule = item(page, "at least 16 GB RAM");
    await expect(rule).toContainText("Filters your results");
    await rule.getByRole("button", { name: /^Switch off/ }).click();
    await expect(rule).toContainText("Switched off");
    await rule.getByRole("button", { name: /^Switch on/ }).click();
    await expect(rule).toContainText("Filters your results");
  });
});

test.describe("deleting the account", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the dialog lists what goes, refuses anything but delete, and gives focus back", async ({
    page,
  }) => {
    await openMemory(page);
    const opener = page.getByRole("button", { name: "Delete my account" });
    await opener.click();
    const dialog = page.getByRole("alertdialog", { name: "Delete your account?" });
    for (const what of [
      "every request and search saved with your account",
      "everything ShopSense remembers about you",
      "your profile",
      "your sign-in to ShopSense",
    ])
      await expect(dialog).toContainText(what);
    const box = dialog.getByRole("textbox", { name: "Type delete to confirm" });
    await expect(box).toBeFocused();
    for (const word of ["", "remove", "delet", "deleted"]) {
      await box.fill(word);
      await dialog.getByRole("button", { name: "Delete my account" }).click();
      await expect(dialog.getByRole("alert")).toHaveText('Type the word "delete" to confirm.');
      await expect(box).toHaveAttribute("aria-invalid", "true");
      await expect(page).toHaveURL(/\/account$/);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
    await opener.click();
    await dialog.getByRole("button", { name: "Keep my account" }).click();
    await expect(opener).toBeFocused();
  });

  test("with the word, the account goes, the shopper is signed out, and a plain page says so", async ({
    page,
  }) => {
    await openMemory(page);
    await page.getByRole("button", { name: "Delete my account" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Delete your account?" });
    await dialog.getByRole("textbox", { name: "Type delete to confirm" }).fill("delete");
    await dialog.getByRole("button", { name: "Delete my account" }).click();
    await expect(page).toHaveURL(/\/account\/deleted$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Your account is deleted" }),
    ).toBeVisible();
    await expect(
      page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Sign in" }),
    ).toBeVisible();
    await page.goto("/account");
    await expect(page.getByRole("main").getByRole("link", { name: "Sign in" })).toBeVisible();
  });

  test("when the backend can't delete it, the dialog says why and the shopper stays signed in", async ({
    page,
  }) => {
    await openMemory(page, "delete-fails");
    await page.getByRole("button", { name: "Delete my account" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Delete your account?" });
    await dialog.getByRole("textbox", { name: "Type delete to confirm" }).fill("delete");
    await dialog.getByRole("button", { name: "Delete my account" }).click();
    await expect(dialog.getByRole("alert")).toHaveText(
      "We couldn't delete your account just now. Nothing was deleted. Please try again in a minute.",
    );
    await expect(page).toHaveURL(/\/account$/);
  });
});

test.describe("what goes wrong", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a change that fails says so, and the must-have stays as it was", async ({ page }) => {
    await openMemory(page, "changes-fail");
    const rule = item(page, "at least 16 GB RAM");
    await rule.getByRole("button", { name: /^Switch off/ }).click();
    await expect(memory(page).getByRole("alert")).toHaveText(
      `We couldn't switch off "Must have: at least 16 GB RAM". ${UNAVAILABLE}`,
    );
    await expect(rule).toContainText("Filters your results");
  });

  test("a forget that fails puts the item back and says so", async ({ page }) => {
    await openMemory(page, "changes-fail");
    await page.getByRole("button", { name: `Forget "${BATTERY}"` }).click();
    await expect(memory(page).getByRole("alert")).toContainText(
      `We couldn't forget "${BATTERY}".`,
      { timeout: 8_000 },
    );
    await expect(item(page, BATTERY)).toHaveCount(1);
  });

  test("Forget everything that fails changes nothing", async ({ page }) => {
    await openMemory(page, "changes-fail");
    await memory(page).getByRole("button", { name: "Forget everything" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Forget everything" }).click();
    await expect(memory(page).getByRole("alert")).toContainText(
      "We couldn't forget everything. Nothing was changed.",
    );
    await expect(items(page)).toHaveCount(7);
  });

  test("a download that fails says so", async ({ page }) => {
    await openMemory(page, "changes-fail");
    await memory(page).getByRole("button", { name: "Download my data" }).click();
    await expect(memory(page).getByRole("alert")).toContainText("We couldn't make your file.");
  });

  test("a sign-in no longer accepted signs the shopper out, and the page asks them to sign in", async ({
    page,
  }) => {
    await page.addInitScript(([k, v]) => sessionStorage.setItem(k!, v!), [KEY, "signed-out"]);
    await page.goto(`/sign-in?next=${encodeURIComponent("/account")}`);
    const google = page.getByRole("button", { name: "Continue with Google" });
    await expect(google).toBeEnabled();
    await waitForHydration(page, "main button");
    await google.click();
    await expect(page.getByRole("main").getByRole("link", { name: "Sign in" })).toBeVisible();
  });

  test("a deletion not yet confirmed says so, and asking again finishes it", async ({ page }) => {
    await openMemory(page, "delete-unfinished");
    await page.getByRole("button", { name: "Delete my account" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Delete your account?" });
    await dialog.getByRole("textbox", { name: "Type delete to confirm" }).fill("delete");
    await dialog.getByRole("button", { name: "Delete my account" }).click();
    await expect(dialog.getByRole("alert")).toContainText(
      "Your account was deleted, but we couldn't yet confirm",
    );
    await dialog.getByRole("button", { name: "Delete my account" }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Your account is deleted" }),
    ).toBeVisible();
  });

  test("deleting isn't set up on this backend: the dialog says so", async ({ page }) => {
    await openMemory(page, "delete-unavailable");
    await page.getByRole("button", { name: "Delete my account" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Delete your account?" });
    await dialog.getByRole("textbox", { name: "Type delete to confirm" }).fill("delete");
    await dialog.getByRole("button", { name: "Delete my account" }).click();
    await expect(dialog.getByRole("alert")).toHaveText(
      "Deleting an account isn't available right now. Nothing was deleted. Please try again later.",
    );
  });

  test("Try again, from the keyboard, keeps focus in the section and shows the list", async ({
    page,
  }) => {
    await openMemory(page, "unavailable");
    await memory(page).getByRole("button", { name: "Try again" }).focus();
    await page.keyboard.press("Enter");
    await expect(memory(page).getByRole("heading", { name: "What we remember" })).toBeFocused();
    await expect(items(page)).toHaveCount(7);
  });
});

test.describe("keyboard only", () => {
  test("change, forget and undo, download, and delete the account, all from the keyboard", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openMemory(page);
    const change = page.getByRole("button", { name: `Change "${BATTERY}"` });
    await change.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("spinbutton", { name: "Weight, 0 to 10" })).toBeFocused();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.type("2");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("textbox", { name: "In your words" })).toBeFocused();
    await page.keyboard.press("Enter"); // Enter in the form saves it
    await expect(item(page, "Battery life: low importance")).toBeVisible();
    const changed = page.getByRole("button", { name: 'Change "Battery life: low importance"' });
    await expect(changed).toBeFocused();

    // Escape closes the form, and focus goes back to Change.
    await page.keyboard.press("Enter");
    await page.keyboard.press("Escape");
    await expect(changed).toBeFocused();

    // Tab reaches Forget; Enter forgets, focus lands on Undo, and Enter there keeps it.
    await page.keyboard.press("Tab");
    await expect(
      page.getByRole("button", { name: 'Forget "Battery life: low importance"' }),
    ).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: /^Undo: keep/ })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("button", { name: 'Forget "Battery life: low importance"' }),
    ).toBeFocused();

    // The must-have's switch, from the keyboard.
    const rule = item(page, "at least 16 GB RAM");
    await rule.getByRole("button", { name: /^Switch off/ }).focus();
    await page.keyboard.press("Enter");
    await expect(rule).toContainText("Switched off");

    // Download, from the keyboard.
    await memory(page).getByRole("button", { name: "Download my data" }).focus();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.keyboard.press("Enter"),
    ]);
    expect(download.suggestedFilename()).toBe("shopsense-memory.json");

    // Forget everything's dialog: Tab moves between its two buttons, Escape keeps everything.
    await memory(page).getByRole("button", { name: "Forget everything" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Keep them" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(
      page.getByRole("alertdialog").getByRole("button", { name: "Forget everything" }),
    ).toBeFocused();
    await page.keyboard.press("Escape");

    // The delete dialog: focus in the box, a wrong word refused, then the word.
    await page.getByRole("button", { name: "Delete my account" }).focus();
    await page.keyboard.press("Enter");
    const box = page.getByRole("textbox", { name: "Type delete to confirm" });
    await expect(box).toBeFocused();
    await page.keyboard.type("nope");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("alertdialog").getByRole("alert")).toBeVisible();
    await expect(box).toBeFocused();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.type("delete");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/account\/deleted$/);
  });
});

test.describe("axe, at 390 and 1440 px, light and dark", () => {
  test("the list, a change, Undo, the dialogs and the confirmation page", async ({ page }) => {
    // Time runs as usual, except while axe checks Undo (below).
    await page.clock.install();
    await page.setViewportSize({ width: 1440, height: 900 });
    await openMemory(page);
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/account");
      await expect(items(page)).toHaveCount(7);
      await checkRow(page, `memory list (${size.width} px)`);
      await page.getByRole("button", { name: `Change "${BATTERY}"` }).click();
      await page.getByRole("spinbutton", { name: "Weight, 0 to 10" }).fill("12");
      await page.getByRole("button", { name: "Save" }).click();
      await expect(page.getByText("Use a number from 0 to 10.")).toBeVisible();
      await checkRow(page, `memory change, with an error (${size.width} px)`);
      await page.getByRole("button", { name: "Cancel" }).click();
      await page.getByRole("button", { name: `Forget "${BATTERY}"` }).click();
      await expect(page.getByRole("button", { name: /^Undo/ })).toBeVisible();
      // Undo lasts 5 seconds; axe in two themes can take longer when the machine is busy. The clock is held still
      // while axe runs, so Undo is still there to press after it.
      await page.clock.pauseAt((await page.evaluate(() => Date.now())) + 50);
      await checkRow(page, `memory undo (${size.width} px)`, { paused: true });
      await page.getByRole("button", { name: /^Undo/ }).click();
      await page.clock.resume();
      await memory(page).getByRole("button", { name: "Forget everything" }).click();
      await expect(page.getByRole("alertdialog")).toBeVisible();
      await checkRow(page, `forget everything dialog (${size.width} px)`);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Delete my account" }).click();
      const dialog = page.getByRole("alertdialog", { name: "Delete your account?" });
      await dialog.getByRole("button", { name: "Delete my account" }).click();
      await expect(dialog.getByRole("alert")).toBeVisible();
      await checkRow(page, `delete dialog, word refused (${size.width} px)`);
      await page.keyboard.press("Escape");
    }
  });

  test("the empty state, and the confirmation page while signed in", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openMemory(page, "empty");
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/account");
      await expect(memory(page).getByText(EMPTY)).toBeVisible();
      await checkRow(page, `memory empty (${size.width} px)`);
      await page.goto("/account/deleted");
      await expect(
        page.getByRole("heading", { level: 1, name: "Your account is still here" }),
      ).toBeVisible();
      await checkRow(page, `account deleted, signed in (${size.width} px)`);
    }
  });

  test("the memory unavailable (503)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openMemory(page, "unavailable");
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/account"); // a fresh page: its first list answers 503 again
      await expect(memory(page).getByRole("alert")).toContainText(UNAVAILABLE);
      await checkRow(page, `memory unavailable (${size.width} px)`);
    }
  });

  test("the confirmation page as the deleted shopper sees it, and as anyone else does", async ({
    page,
  }) => {
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto("/account/deleted");
      await expect(
        page.getByRole("heading", { level: 1, name: "Nothing to confirm here" }),
      ).toBeVisible(); // no account was deleted in this tab: it never says one was
      await checkRow(page, `account deleted, nothing to confirm (${size.width} px)`);
      await page.evaluate((k) => sessionStorage.setItem(k, "1"), DELETED);
      await page.reload();
      await expect(
        page.getByRole("heading", { level: 1, name: "Your account is deleted" }),
      ).toBeVisible();
      await checkRow(page, `account deleted (${size.width} px)`);
      await page.evaluate((k) => sessionStorage.removeItem(k), DELETED);
    }
  });

  test("every failure's sentence: a 404, a 409, the note, a failed change and the delete's 503", async ({
    page,
  }) => {
    // Signed in once; each state is set in the tab, then a fresh page reads it (no init script to undo it).
    await page.setViewportSize({ width: 1440, height: 900 });
    await openMemory(page);
    for (const size of SIZES) {
      await page.setViewportSize(size);

      await page.evaluate((k) => sessionStorage.setItem(k!, "gone"), KEY);
      await page.goto("/account");
      await page.getByRole("button", { name: `Forget "${BATTERY}"` }).click();
      await expect(memory(page).getByRole("alert")).toBeVisible({ timeout: 8_000 });
      await checkRow(page, `memory 404 (${size.width} px)`);

      await page.evaluate((k) => sessionStorage.setItem(k!, "changed"), KEY);
      await page.goto("/account");
      await page.getByRole("button", { name: `Change "${BATTERY}"` }).click();
      await page.getByRole("spinbutton", { name: "Weight, 0 to 10" }).fill("3");
      await page.getByRole("button", { name: "Save" }).click();
      await expect(memory(page).getByRole("alert")).toBeVisible();
      await checkRow(page, `memory 409 (${size.width} px)`);

      await page.evaluate((k) => sessionStorage.setItem(k!, "unmatched"), KEY);
      await page.goto("/account");
      await page.getByRole("button", { name: `Change "${BATTERY}"` }).click();
      await page.getByRole("textbox", { name: "In your words" }).fill("Lasts a whole day");
      await page.getByRole("button", { name: "Save" }).click();
      await expect(item(page, "Lasts a whole day")).toContainText("Your new words are saved");
      await checkRow(page, `memory note (${size.width} px)`);

      await page.evaluate((k) => sessionStorage.setItem(k!, "changes-fail"), KEY);
      await page.goto("/account");
      await item(page, "at least 16 GB RAM")
        .getByRole("button", { name: /^Switch off/ })
        .click();
      await expect(memory(page).getByRole("alert")).toBeVisible();
      await checkRow(page, `memory change failed (${size.width} px)`);

      await page.evaluate((k) => sessionStorage.setItem(k!, "delete-fails"), KEY);
      await page.goto("/account");
      await page.getByRole("button", { name: "Delete my account" }).click();
      const dialog = page.getByRole("alertdialog", { name: "Delete your account?" });
      await dialog.getByRole("textbox", { name: "Type delete to confirm" }).fill("delete");
      await dialog.getByRole("button", { name: "Delete my account" }).click();
      await expect(dialog.getByRole("alert")).toContainText("We couldn't delete your account");
      await checkRow(page, `delete dialog, 503 (${size.width} px)`);
      await page.keyboard.press("Escape");
      await page.evaluate((k) => sessionStorage.removeItem(k!), KEY);
    }
  });
});

/** Screenshots for the pull request, in each size and theme. Runs only when SHOTS_DIR is set. */
for (const size of SIZES) {
  test(`screenshots of the memory screen at ${size.width} px`, async ({ page }) => {
    test.skip(!SHOTS, "SHOTS_DIR is not set");
    const dir = SHOTS!;
    mkdirSync(dir, { recursive: true });
    const shoot = async (name: string, fullPage = true) => {
      for (const theme of ["light", "dark"] as Theme[]) {
        await setTheme(page, theme);
        await page.screenshot({
          path: join(dir, `${size.width}-${theme}-${name}.png`),
          fullPage,
          animations: "disabled",
        });
      }
      await setTheme(page, "light");
    };
    await page.setViewportSize(size);
    await openMemory(page);
    await memory(page).scrollIntoViewIfNeeded();
    await shoot("list");
    await page.getByRole("button", { name: `Change "${BATTERY}"` }).click();
    await shoot("edit");
    await page.getByRole("button", { name: "Cancel" }).click();
    await page.getByRole("button", { name: `Forget "${BATTERY}"` }).click();
    await shoot("undo");
    await page.getByRole("button", { name: /^Undo/ }).click();
    await page.getByRole("button", { name: "Delete my account" }).click();
    await page.getByRole("textbox", { name: "Type delete to confirm" }).fill("delete");
    await shoot("delete-dialog", false);
    await page.keyboard.press("Escape");
    await page.evaluate((k) => sessionStorage.setItem(k, "empty"), KEY);
    await page.goto("/account");
    await expect(memory(page).getByText(EMPTY)).toBeVisible();
    await shoot("empty");
  });
}
