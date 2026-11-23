/**
 * US-33 (S5-12 DECIDED 2): our own words on every page are at about Grade 8. Each page and state is reached in mock
 * mode as a shopper would; every block of text of 8 words or more (a paragraph, a list item, a heading, a label, a
 * cell) is graded with the Flesch-Kincaid formula the explanations use (src/lib/readability.ts, S4-8). The median and
 * every block above grade 9 are reported, and any above 9 fails. Under 8 words a grade means little, so short labels
 * are left out. Sample product data (names, sample explanations) is the mock's, not our copy, so it is left out too.
 */
import { expect, test, type Page } from "@playwright/test";
import { grade, wordCount } from "../../src/lib/readability";
import { finishResearch, reachBrief, reachCriteria, signInTo, startScenario } from "./helpers";

const LIMIT = 9;
const MIN_WORDS = 8;

const BLOCKS =
  "h1, h2, h3, h4, p, li, dt, dd, label, legend, summary, th, td, figcaption, blockquote, [role=alert], [role=status]";

/** Every graded block on the page now, as text. A block holding another block is left to the inner one. */
async function blocks(page: Page): Promise<string[]> {
  return page.evaluate(
    ({ selector }) => {
      const out: string[] = [];
      for (const el of document.querySelectorAll<HTMLElement>(selector)) {
        if (el.querySelector(selector)) continue;
        // A list of names ("Not verified: Battery life, Weight ...") is data: a reading grade means nothing for it.
        if (el.closest("[data-explanation], [data-names], [aria-hidden='true'], script, style"))
          continue;
        // What a shopper reads, sight or screen reader: rendered text, never a copy hidden from both (aria-hidden).
        const parts: string[] = [];
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          const parent = n.parentElement;
          if (!parent || parent.closest("[aria-hidden='true']")) continue;
          if (!parent.checkVisibility()) continue;
          parts.push(n.textContent ?? "");
        }
        const text = parts.join(" ").replace(/\s+/g, " ").trim();
        if (!text || /\bSample\b/.test(text)) continue;
        out.push(text);
      }
      return [...new Set(out)];
    },
    { selector: BLOCKS },
  );
}

test("our own words are at about Grade 8 on every page", async ({ page }) => {
  test.setTimeout(300_000);
  const found = new Map<string, string>(); // text -> where
  const visit = async (where: string) => {
    for (const text of await blocks(page)) if (!found.has(text)) found.set(text, where);
  };

  for (const path of ["/", "/about", "/features", "/faq", "/contact", "/privacy", "/sign-in"]) {
    await page.goto(path);
    await visit(path);
  }
  await page.goto("/no-such-page");
  await visit("not found");

  // Signed in, before the clock is installed: it would hold the sign-in page's timers.
  await signInTo(page, "/account");
  await visit("account");
  await page.goto("/my-searches"); // signed in already
  await expect(page.getByRole("main").getByRole("listitem").first()).toBeVisible();
  await visit("my searches");

  await page.clock.install();
  const id = await reachCriteria(page);
  await visit("criteria");
  await page.getByRole("button", { name: /Start the questions/ }).click();
  await expect(page.getByRole("group", { name: /Quick replies/ })).toBeVisible();
  await visit("questions");
  await reachBrief(page);
  await visit("brief");
  for (const scenario of ["success", "relaxation", "partial", "not-ranked", "llm-unavailable"]) {
    await startScenario(page, id, scenario);
    await finishResearch(page);
    await page.evaluate(() => {
      for (const d of document.querySelectorAll("details")) d.open = true;
    });
    await visit(`results (${scenario})`);
  }

  const graded = [...found]
    .filter(([text]) => wordCount(text) >= MIN_WORDS)
    .map(([text, where]) => ({ text, where, grade: grade(text) }));
  const sorted = graded.map((g) => g.grade).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
  const over = graded.filter((g) => g.grade > LIMIT).sort((a, b) => b.grade - a.grade);
  console.log(
    `[reading] ${graded.length} blocks of ${MIN_WORDS}+ words; median grade ${median.toFixed(1)}; above ${LIMIT}: ${over.length}`,
  );
  for (const o of over)
    console.log(`[reading] ${o.grade.toFixed(1)} | ${o.where} | ${o.text.slice(0, 200)}`);
  expect(over.map((o) => `${o.where}: ${o.text.slice(0, 120)}`)).toEqual([]);
});
