/**
 * The rubric editor on the brief page (US-09, US-10), in mock mode, as a shopper uses it: switch a must-have, set a
 * weight, rule something out in words, and find all three still there after a reload, a step back and a rebuild.
 */
import { expect, test, type Page } from "@playwright/test";
import { answerAll, checkRow, reachBrief } from "./quality/helpers";
import { smallTargets } from "./quality/layout";

const REQUEST = "Laptop for coding under ₹70,000, at least 16 GB RAM";
const RULE = "at least 16 GB RAM";
const CPU_BOX = "CPU performance, from 0 to 10";

const rule = (page: Page) => page.getByRole("group", { name: RULE });
const cpu = (page: Page) => page.getByRole("spinbutton", { name: CPU_BOX });
const ruleOut = (page: Page) => page.getByRole("textbox", { name: "Rule something out" });
const leaveOut = (page: Page) => page.getByRole("list", { name: "Things to leave out" });

/** Waits for the mock's saves to land, as a page reload would otherwise beat the last one. */
async function savedEdits(page: Page, sessionId: string, count: number) {
  await expect
    .poll(() =>
      page.evaluate(
        (id) =>
          (JSON.parse(sessionStorage.getItem(`shopsense.rubric.edits.${id}`) ?? "[]") as unknown[])
            .length,
        sessionId,
      ),
    )
    .toBe(count);
}

async function expectAllThree(page: Page) {
  await expect(rule(page).getByRole("radio", { name: "Preference" })).toBeChecked();
  await expect(cpu(page)).toHaveValue("3");
  await expect(leaveOut(page).getByText("Xiaomi", { exact: true })).toBeVisible();
}

test.describe("rubric editor", () => {
  test("keyboard only: switch a rule, edit a weight, add and remove an exclusion", async ({
    page,
  }) => {
    const id = await reachBrief(page, REQUEST);

    await rule(page).getByRole("radio", { name: "Must-have" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(rule(page).getByRole("radio", { name: "Preference" })).toBeChecked();

    await cpu(page).focus();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.type("3");
    await page.keyboard.press("Tab"); // leaving the box saves it, and Tab goes on to its slider
    await expect(cpu(page)).toHaveValue("3");
    const slider = page.getByRole("slider", { name: /CPU performance, slide/ });
    await expect(slider).toBeFocused();
    await page.keyboard.press("ArrowRight"); // the slider by keyboard: 3.5, saved when the key comes up
    await expect(cpu(page)).toHaveValue("3.5");

    await ruleOut(page).focus();
    await page.keyboard.type("no Xiaomi");
    await page.keyboard.press("Enter");
    await expect(leaveOut(page).getByText("Xiaomi", { exact: true })).toBeVisible();
    await expect(ruleOut(page)).toHaveValue("");

    const always = page.getByRole("checkbox", { name: "Always leave out Xiaomi" });
    await always.focus();
    await page.keyboard.press("Space");
    await expect(always).toBeChecked();
    await expect(always).toBeFocused(); // the chip stays where it was

    await page.getByRole("button", { name: "Remove Xiaomi" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Remove Xiaomi" })).toHaveCount(0);
    await expect(ruleOut(page)).toBeFocused(); // with no chip left, focus goes to the box, not the page
    await savedEdits(page, id, 7); // switch, two weights, add, Always (remove + add, one save), remove: all saved
  });

  test("edits survive a reload and a step back", async ({ page }) => {
    const id = await reachBrief(page, REQUEST);
    await rule(page).getByRole("radio", { name: "Preference" }).check();
    await cpu(page).fill("3");
    await cpu(page).blur();
    await ruleOut(page).fill("no Xiaomi");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expectAllThree(page);
    await savedEdits(page, id, 3);

    await page.reload();
    await expectAllThree(page);

    // A step back to the questions and forward again.
    await page.getByRole("link", { name: "Back to the questions" }).click();
    await expect(page).toHaveURL(/\/questions$/);
    await page.getByRole("link", { name: /Review your brief/ }).click();
    await expect(page).toHaveURL(/\/brief$/);
    await expectAllThree(page);

    // A rebuild: changing the CPU answer rebuilds the weights, and the shopper's own weight still wins.
    await page.getByRole("button", { name: "Change CPU performance" }).click();
    await expect(page).toHaveURL(/\/questions$/);
    await answerAll(page);
    await page.getByRole("link", { name: /Review your brief/ }).click();
    await expect(page).toHaveURL(/\/brief$/);
    await expectAllThree(page);
  });

  test("axe: no serious or critical violations, light and dark", async ({ page }) => {
    await reachBrief(page, REQUEST);
    await ruleOut(page).fill("no Xiaomi or Zorblax");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.getByText(/We couldn't read “Zorblax”/)).toBeVisible();
    await expect(leaveOut(page).getByText("Xiaomi", { exact: true })).toBeVisible();
    await checkRow(page, "brief, rubric editor with a leave-out and words not read");
    expect(await smallTargets(page)).toEqual([]); // every radio, checkbox and button is 44 px or more
  });
});
