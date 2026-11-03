/**
 * Check 9 of the quality gate, first-load JavaScript: what the browser downloads to show each route when it
 * is opened directly, measured gzipped, against the F0 budget of 170 KB for the home page. The same budget is
 * used for every route. The live-mode schema checker (AJV) must not be part of a first load in mock mode.
 */
import { gzipSync } from "node:zlib";
import { expect, test } from "@playwright/test";

const BUDGET_KB = 170;
const MISSING = "sess_000000000000000000000000";

const ROUTES = [
  "/",
  "/research",
  "/privacy",
  `/s/${MISSING}/criteria`,
  `/s/${MISSING}/questions`,
  `/s/${MISSING}/brief`,
  `/s/${MISSING}/results`,
];

test("first-load JavaScript stays within the budget on every route", async ({
  browser,
  baseURL,
}) => {
  const rows: string[] = [];
  for (const route of ROUTES) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const scripts = new Map<string, Buffer>();
    page.on("response", async (res) => {
      const url = res.url();
      if (!url.startsWith(baseURL ?? "") || !/\.m?js(\?|$)/.test(new URL(url).pathname)) return;
      try {
        scripts.set(url, await res.body());
      } catch {
        /* the page moved on; the file is counted on its next request */
      }
    });
    await page.goto(route, { waitUntil: "networkidle" });
    let gz = 0;
    let raw = 0;
    let ajv = false;
    for (const body of scripts.values()) {
      raw += body.length;
      gz += gzipSync(body, { level: 9 }).length;
      // AJV's own error text; it only appears when the schema checker is bundled in.
      if (body.includes("strict mode: unknown keyword")) ajv = true;
    }
    const kb = gz / 1024;
    rows.push(
      `${route}: ${scripts.size} files, ${(raw / 1024).toFixed(1)} KB raw, ${kb.toFixed(1)} KB gzipped${ajv ? ", includes AJV" : ""}`,
    );
    expect.soft(kb, `${route} first-load JS (KB gzipped)`).toBeLessThanOrEqual(BUDGET_KB);
    expect.soft(ajv, `${route} loads the live-mode schema checker in mock mode`).toBe(false);
    await context.close();
  }
  console.log(`[bundle] ${rows.join("; ")}`);
});
