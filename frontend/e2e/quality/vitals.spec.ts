/**
 * Check 9 of the quality gate, Core Web Vitals, measured with Google's web-vitals library under 4x CPU
 * slowdown: LCP 2.5 s or less and CLS 0.1 or less on the home page; INP 200 ms or less while a shopper types in
 * the composer and presses quick replies. Also: every press paints a response within 100 ms (without the
 * slowdown), and every wait over 400 ms shows a status message.
 * These are lab numbers on a fast local server, not field (p75) data.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { LAPTOP_REQUEST, reachBrief, reachQuestions, waitForHydration } from "./helpers";

const WEB_VITALS = fileURLToPath(
  new URL("../../node_modules/web-vitals/dist/web-vitals.iife.js", import.meta.url),
);

interface Vitals {
  LCP?: number;
  CLS?: number;
  INP?: number;
}

async function recordVitals(page: Page) {
  // Init scripts run inside a function, so the library's global has to be set on window by hand.
  await page.addInitScript({
    content: `${readFileSync(WEB_VITALS, "utf8")};window.webVitals = webVitals;`,
  });
  await page.addInitScript(() => {
    const w = window as unknown as {
      __vitals: Record<string, number>;
      webVitals: Record<
        string,
        (cb: (m: { name: string; value: number }) => void, o?: object) => void
      >;
    };
    w.__vitals = {};
    const keep = (m: { name: string; value: number }) => {
      w.__vitals[m.name] = m.value;
    };
    for (const fn of ["onLCP", "onCLS", "onINP"])
      w.webVitals[fn]?.(keep, { reportAllChanges: true });
    // Which elements moved, for the report: every counted layout shift and the nodes it moved.
    const shifts: { value: number; at: number; nodes: string[] }[] = [];
    (window as unknown as { __shifts: typeof shifts }).__shifts = shifts;
    new PerformanceObserver((list) => {
      for (const e of list.getEntries() as (PerformanceEntry & {
        value: number;
        hadRecentInput: boolean;
        sources?: { node?: Node | null }[];
      })[]) {
        if (e.hadRecentInput) continue;
        const nodes = (e.sources ?? []).map((src) => {
          const el = src.node as HTMLElement | null;
          if (!el || !el.tagName) return "text";
          const label = el.id ? `#${el.id}` : (el.getAttribute("aria-labelledby") ?? el.className);
          return `${el.tagName.toLowerCase()}${label ? ` ${String(label).slice(0, 40)}` : ""}`;
        });
        shifts.push({ value: e.value, at: e.startTime, nodes });
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

const vitalsOf = (page: Page) =>
  page.evaluate(() => (window as unknown as { __vitals: Vitals }).__vitals);

/** The largest counted layout shifts (after `since`, in page time) and what moved, one line each. */
const shiftsOf = (page: Page, since = 0) =>
  page.evaluate(
    (from) =>
      (window as unknown as { __shifts: { value: number; at: number; nodes: string[] }[] }).__shifts
        .filter((s) => s.value >= 0.005 && s.at >= from)
        .sort((a, b) => b.value - a.value)
        .slice(0, 5)
        .map((s) => `${s.value.toFixed(3)} ${s.nodes.join(" + ")}`),
    since,
  );

/** Total of the counted layout shifts after `since` (page time): the CLS of that part of the visit. */
const shiftTotal = (page: Page, since: number) =>
  page.evaluate(
    (from) =>
      (window as unknown as { __shifts: { value: number; at: number }[] }).__shifts
        .filter((s) => s.at >= from)
        .reduce((sum, s) => sum + s.value, 0),
    since,
  );

async function slowCpu(page: Page, rate: number) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate });
}

test.describe("web vitals", () => {
  test.describe.configure({ timeout: 180_000 });
  // Real visitors: motion on (the typing effect runs), a mid-size phone screen.
  test.use({
    viewport: { width: 412, height: 915 },
    isMobile: true,
    hasTouch: true,
    reducedMotion: "no-preference",
  });

  test("home page: LCP and CLS under 4x CPU slowdown", async ({ page }) => {
    await recordVitals(page);
    await slowCpu(page, 4);
    await page.goto("/");
    await waitForHydration(page, "#research-query");
    await page.waitForTimeout(1_500);
    // A tap ends LCP measurement, as a real visit would.
    await page.locator("#research-query").tap();
    await page.waitForTimeout(300);
    const v = await vitalsOf(page);
    console.log(`[vitals] home: LCP ${Math.round(v.LCP ?? -1)} ms, CLS ${(v.CLS ?? 0).toFixed(3)}`);
    expect(v.LCP, "LCP was measured").toBeDefined();
    expect.soft(v.LCP!).toBeLessThanOrEqual(2_500);
    expect.soft(v.CLS ?? 0).toBeLessThanOrEqual(0.1);
  });

  test("questions: INP while typing and pressing quick replies under 4x CPU slowdown", async ({
    page,
  }) => {
    await recordVitals(page);
    await reachQuestions(page);
    // CLS counts from the first question on; the request and criteria pages before it are not part of this.
    const start = await page.evaluate(() => performance.now());
    await slowCpu(page, 4);
    const composer = page.getByLabel("Or type your answer");
    await composer.tap();
    await page.keyboard.type("Is it about how fast it builds code?", { delay: 40 });
    await composer.press("Enter");
    await expect(page.getByText(/^Good question/).first()).toBeVisible();
    const replies = page.getByRole("group", { name: /Quick replies/ });
    for (let i = 0; i < 3; i++) {
      await expect(replies.getByRole("button", { name: "High" })).toBeEnabled();
      await replies.getByRole("button", { name: "High" }).tap();
      await expect(
        replies.getByRole("button", { name: "High" }).or(page.getByText("Your brief is ready")),
      ).toBeEnabled();
    }
    // INP is reported once the page is hidden or after interactions; hiding it flushes the last value.
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const v = await vitalsOf(page);
    const cls = await shiftTotal(page, start);
    console.log(
      `[vitals] questions: INP ${Math.round(v.INP ?? -1)} ms, CLS ${cls.toFixed(3)}; shifts: ${(await shiftsOf(page, start)).join("; ") || "none"}`,
    );
    expect(v.INP, "INP was measured").toBeDefined();
    expect.soft(v.INP!).toBeLessThanOrEqual(200);
    expect.soft(cls).toBeLessThanOrEqual(0.1);
  });
});

test.describe("results arriving", () => {
  test.describe.configure({ timeout: 120_000 });
  test.use({ viewport: { width: 412, height: 915 }, isMobile: true, hasTouch: true });

  test("the page does not jump when the results replace the progress list (CLS)", async ({
    page,
  }) => {
    await recordVitals(page);
    await reachBrief(page);
    // Only what happens from here on counts: the interview before it is measured in its own test.
    const start = await page.evaluate(() => performance.now());
    await page.getByRole("button", { name: /Start research/ }).tap();
    // Real time on purpose: shifts within 0.5 s of a tap do not count, and research takes about 6 s.
    await expect(page.getByRole("heading", { name: "Best match" })).toBeVisible({
      timeout: 30_000,
    });
    await page.waitForTimeout(500);
    const cls = await shiftTotal(page, start);
    console.log(
      `[vitals] results arriving: CLS ${cls.toFixed(3)}; shifts: ${(await shiftsOf(page, start)).join("; ") || "none"}`,
    );
    expect(cls).toBeLessThanOrEqual(0.1);
  });
});

test.describe("slow phone", () => {
  test.describe.configure({ timeout: 240_000 });
  test.use({ viewport: { width: 412, height: 823 }, isMobile: true, hasTouch: true });

  test("session pages and the start of research do not jump on a slow connection (CLS)", async ({
    page,
  }) => {
    await recordVitals(page);
    const id = await reachBrief(page);
    // Lighthouse's mobile settings: slow 4G (150 ms, 1.6 Mbit/s down) and a 4x slower CPU.
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: 150,
      downloadThroughput: (1.6 * 1024 * 1024) / 8,
      uploadThroughput: (750 * 1024) / 8,
    });
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    const rows: string[] = [];
    for (const route of ["criteria", "questions", "brief", "results"]) {
      await page.goto(`/s/${id}/${route}`, { waitUntil: "networkidle" });
      await page.waitForTimeout(1_500);
      const cls = await shiftTotal(page, 0);
      rows.push(
        `${route} ${cls.toFixed(3)}${cls > 0.005 ? ` (${(await shiftsOf(page)).join("; ")})` : ""}`,
      );
      expect.soft(cls, `CLS loading /s/[id]/${route}`).toBeLessThanOrEqual(0.1);
    }
    // From the brief page, measured from the tap on "Start research" to the results.
    await page.goto(`/s/${id}/brief`, { waitUntil: "networkidle" });
    const start = await page.evaluate(() => performance.now());
    await page.getByRole("button", { name: /Start research/ }).tap();
    await expect(page.getByRole("heading", { name: "Best match" })).toBeVisible({
      timeout: 60_000,
    });
    await page.waitForTimeout(500);
    const cls = await shiftTotal(page, start);
    rows.push(`start research to results ${cls.toFixed(3)}`);
    console.log(`[vitals] slow phone, CLS per page load: ${rows.join("; ")}`);
    expect.soft(cls, "CLS from Start research to the results").toBeLessThanOrEqual(0.1);
  });
});

test.describe("feedback", () => {
  test.describe.configure({ timeout: 120_000 });

  test("every press paints a response within 100 ms", async ({ page }) => {
    await page.addInitScript(() => {
      const w = window as unknown as { __events: { name: string; duration: number }[] };
      w.__events = [];
      new PerformanceObserver((list) => {
        for (const e of list.getEntries() as PerformanceEventTiming[]) {
          if ((e as PerformanceEventTiming & { interactionId?: number }).interactionId)
            w.__events.push({ name: e.name, duration: e.duration });
        }
      }).observe({
        type: "event",
        durationThreshold: 16,
        buffered: true,
      } as PerformanceObserverInit);
    });
    await page.goto("/research");
    await waitForHydration(page, "#request-text");
    await page.getByLabel("Your request").fill(LAPTOP_REQUEST);
    await page.getByRole("button", { name: /^Continue/ }).click();
    await page.getByRole("button", { name: /Looks right/ }).click();
    await page.getByRole("button", { name: /Start the questions/ }).click();
    const replies = page.getByRole("group", { name: /Quick replies/ });
    for (let i = 0; i < 3; i++) {
      await replies.getByRole("button", { name: "Medium" }).click();
      await expect(replies.getByRole("button", { name: "Medium" })).toBeEnabled();
    }
    await page.getByRole("button", { name: "Why this question?" }).click();
    await page.waitForTimeout(300);
    const events = await page.evaluate(
      () => (window as unknown as { __events: { name: string; duration: number }[] }).__events,
    );
    const worst = events.reduce((m, e) => Math.max(m, e.duration), 0);
    console.log(
      `[feedback] ${events.length} slow-ish events; longest input to next paint: ${Math.round(worst)} ms`,
    );
    expect(worst).toBeLessThanOrEqual(100);
  });

  test("waits over 400 ms show a status message", async ({ page }) => {
    await page.clock.install();
    await page.goto("/research");
    await waitForHydration(page, "#request-text");
    await page.getByLabel("Your request").fill(LAPTOP_REQUEST);
    const now = await page.evaluate(() => Date.now());
    await page.clock.pauseAt(now + 250);
    await page.getByRole("button", { name: /^Continue/ }).click();
    await page.clock.runFor(400);
    await expect(
      page.getByRole("status").filter({ hasText: /Reading your request/ }),
    ).toBeVisible();
    await page.clock.resume();

    await reachQuestions(page);
    const t = await page.evaluate(() => Date.now());
    await page.clock.pauseAt(t + 250);
    await page
      .getByRole("group", { name: /Quick replies/ })
      .getByRole("button", { name: "High" })
      .click();
    await page.clock.runFor(450);
    await expect(page.getByText("Thinking about your answer").first()).toBeVisible();
    await page.clock.resume();
  });
});
