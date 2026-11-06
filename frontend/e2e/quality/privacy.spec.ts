/**
 * F-18 and D-44: the privacy page states only what is true. The statements the old page got wrong must stay gone,
 * and the ones that replaced them must stay on the page. The other pages that speak about keeping results are
 * checked here too, so they can't drift apart.
 */
import { expect, test } from "@playwright/test";
import { checkRow, finishResearch, reachBrief, startScenario } from "./helpers";

test.describe("the privacy page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/privacy");
    await expect(page.getByRole("heading", { level: 1, name: "Privacy" })).toBeVisible();
  });

  test("no longer makes the old promises", async ({ page }) => {
    const text = await page.locator("main").innerText();
    for (const gone of [
      "Only the ShopSense server can read",
      "not in the database",
      "deleted 2 hours",
    ]) {
      expect(text, gone).not.toContain(gone);
    }
  });

  test("says what is saved, where, who can read it and for how long", async ({ page }) => {
    const main = page.locator("main");
    for (const said of [
      "what you typed, exactly as you typed it",
      "a session ID",
      "what we understood",
      "your priorities",
      "the whole result",
      "You don't need an account",
      "Supabase database in the Mumbai region",
      "the project team through the database dashboard",
      "have no deletion date yet",
      "2 hours after your last step",
      "Groq first, then Cerebras",
      "We never send your words to Gemini, Mistral or OpenRouter",
    ]) {
      await expect(main.getByText(said).first(), said).toBeVisible();
    }
  });

  test("passes axe at 390 and 1440 px, light and dark", async ({ page }) => {
    for (const size of [
      { width: 390, height: 844 },
      { width: 1440, height: 900 },
    ]) {
      await page.setViewportSize(size);
      await checkRow(page, `privacy (${size.width} px)`);
    }
  });
});

test.describe("what the results page says about keeping results", () => {
  test.beforeEach(async ({ page }) => {
    await page.clock.install();
  });

  test("a result that isn't saved says so", async ({ page }) => {
    const id = await reachBrief(page);
    await startScenario(page, id, "success");
    await finishResearch(page);
    await expect(
      page.getByText("This result isn't saved. It will be gone if you close this page."),
    ).toBeVisible();
  });

  test("after a reload it says what it can't do, and the brief is still there", async ({
    page,
  }) => {
    const id = await reachBrief(page);
    await startScenario(page, id, "success");
    await finishResearch(page);
    await page.reload();
    await expect(page.getByRole("heading", { name: /aren't here any more/ })).toBeVisible();
    await expect(page.getByText(/can't load results again/)).toBeVisible();
    await expect(page.getByText("Results are only kept while this page is open")).toHaveCount(0);
    await page.getByRole("link", { name: "Back to your brief" }).first().click();
    await expect(page).toHaveURL(new RegExp(`/s/${id}/brief$`));
  });
});
