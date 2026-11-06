/**
 * The app's own fonts (F-16). Every font comes from our own /assets: a font service would hold up the first paint and
 * see each shopper's IP address, so no page may load anything from another address. Prices use the rupee sign, so
 * this also proves ₹ is drawn by the app's font and not by a system font standing in for it.
 */
import { expect, test, type Page } from "@playwright/test";
import {
  axeViolations,
  finishResearch,
  reachBrief,
  reachUnderstood,
  setTheme,
  startScenario,
  waitForHydration,
} from "./helpers";

const MISSING = "sess_000000000000000000000000";

const PAGES = [
  "/",
  "/research",
  "/about",
  "/contact",
  "/faq",
  "/features",
  "/privacy",
  "/criteria",
  "/no-such-page",
  `/s/${MISSING}/criteria`,
  `/s/${MISSING}/questions`,
  `/s/${MISSING}/brief`,
  `/s/${MISSING}/results`,
];

/** Every address the page asks the network for that is not the app itself, kept as the visit goes on. */
function outsideRequests(page: Page, baseURL: string | undefined): string[] {
  const own = new URL(baseURL ?? "").origin;
  const outside: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin !== own) outside.push(request.url());
  });
  return outside;
}

/** The fonts Chrome really used to draw an element's text, as it reports them. */
async function fontsDrawing(page: Page, selector: string) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("DOM.enable");
  await cdp.send("CSS.enable");
  const { root } = await cdp.send("DOM.getDocument");
  const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector });
  const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
  await cdp.detach();
  return fonts;
}

/** Marks the first element whose own text matches, so the browser can be asked about it by selector. */
async function markText(page: Page, pattern: string) {
  const found = await page.evaluate((source) => {
    const re = new RegExp(source);
    const el = [...document.querySelectorAll("main *")].find(
      (e) => e.children.length === 0 && re.test(e.textContent ?? ""),
    );
    el?.setAttribute("data-font-probe", "");
    return el?.textContent ?? null;
  }, pattern);
  expect(found, `text matching ${pattern}`).not.toBeNull();
  return "[data-font-probe]";
}

test("no page loads a font, a stylesheet or anything else from another address", async ({
  page,
  baseURL,
}) => {
  const outside = outsideRequests(page, baseURL);
  for (const path of PAGES) {
    await page.goto(path, { waitUntil: "networkidle" });
    const links = await page
      .locator('link[href*="fonts.googleapis.com"], link[href*="fonts.gstatic.com"]')
      .count();
    expect(links, `${path} links to a font service`).toBe(0);
  }
  expect(outside).toEqual([]);
});

test("the whole search, up to the results, stays on the app's own address", async ({
  page,
  baseURL,
}) => {
  const outside = outsideRequests(page, baseURL);
  await page.clock.install();
  const id = await reachBrief(page);
  await startScenario(page, id, "success");
  await finishResearch(page);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  expect(outside).toEqual([]);
});

test("all three fonts load, and each one covers the rupee sign", async ({ page }) => {
  await page.goto("/");
  for (const family of ["Fraunces", "Geist", "Geist Mono"]) {
    const result = await page.evaluate(async (name) => {
      const font = `16px "${name}"`;
      const faces = await document.fonts.load(font, "₹");
      return {
        loaded: faces.filter((face) => face.status === "loaded").length,
        check: document.fonts.check(font, "₹"),
      };
    }, family);
    expect(result.loaded, `${family}: a face that holds ₹ loaded`).toBeGreaterThan(0);
    expect(result.check, `${family}: document.fonts.check`).toBe(true);
  }
});

test("a price is drawn in Fraunces and a budget in Geist, ₹ included", async ({ page }) => {
  await page.clock.install();
  await reachUnderstood(page);
  await page.evaluate(() => document.fonts.ready);
  const budget = await fontsDrawing(page, await markText(page, "₹"));
  expect(budget.map((f) => f.familyName)).toEqual(["Geist"]);
  expect(budget.every((f) => f.isCustomFont)).toBe(true);

  const id = await reachBrief(page);
  await startScenario(page, id, "success");
  await finishResearch(page);
  await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const price = await fontsDrawing(page, await markText(page, "^₹[\\d,]+$"));
  expect(price.map((f) => f.familyName)).toEqual(["Fraunces"]);
  expect(price.every((f) => f.isCustomFont)).toBe(true);
});

test("the first screen downloads only the small font files", async ({ page }) => {
  const fetched = new Map<string, number>();
  page.on("response", (response) => {
    const name = response.url().split("/").pop() ?? "";
    if (name.endsWith(".woff2")) {
      fetched.set(name, Number(response.headers()["content-length"] ?? 0));
    }
  });
  await page.goto("/", { waitUntil: "networkidle" });
  await waitForHydration(page, "#research-query");
  await page.evaluate(() => document.fonts.ready);
  const names = [...fetched.keys()];
  expect(names.length).toBeGreaterThan(0);
  for (const name of names) expect(name).toMatch(/-(core|rupee|hero-italic)-/);
  const total = [...fetched.values()].reduce((sum, bytes) => sum + bytes, 0);
  expect(total, "font bytes on the home page").toBeLessThan(60_000);
});

test("a letter outside the small files still gets the app's font", async ({ page }) => {
  await page.goto("/");
  await waitForHydration(page, "#research-query");
  await page.evaluate(() => {
    const probe = document.createElement("span");
    probe.id = "accent-probe";
    probe.className = "font-serif";
    probe.textContent = "é";
    document.querySelector("main")?.append(probe);
    return document.fonts.ready;
  });
  await expect
    .poll(async () => (await fontsDrawing(page, "#accent-probe")).map((f) => f.familyName))
    .toEqual(["Fraunces"]);
});

test("the page still shows its text if the fonts never arrive", async ({ page }) => {
  await page.route("**/*.woff2", (route) => route.abort());
  await page.goto("/");
  await waitForHydration(page, "#research-query");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("with evidence.");
  await expect(page.getByLabel("Describe what you're looking for")).toBeVisible();
});

for (const width of [390, 1440]) {
  for (const theme of ["light", "dark"] as const) {
    test(`home page with its fonts loaded passes axe at ${width} px, ${theme}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await waitForHydration(page, "#research-query");
      await setTheme(page, theme);
      await page.evaluate(() => document.fonts.ready);
      expect(await axeViolations(page)).toEqual([]);
    });
  }
}
