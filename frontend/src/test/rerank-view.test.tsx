/**
 * US-23 (S5-5) on the results page: change what matters and the list re-ranks at once, each card says how far it
 * moved, chance of being first and confidence are hidden until the shopper goes back, the budget and must-haves
 * re-filter from the loaded result, and up to 4 products can be compared side by side. Made-up products only (the
 * mock scenarios). Nothing here may call the server.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ResultsView } from "@/components/results/ResultsView";
import { ALL_ZERO_NOTE } from "@/lib/interview/rubricEdits";
import { buildMockResult } from "@/lib/results/mockResults";
import searchExample from "@contracts/examples/search_result.json";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import type { SearchResult } from "@/types/contracts/SearchResult";
import type { ResearchInput, ResearchResult } from "@/lib/results/types";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a href="/" className={className}>
      {children}
    </a>
  ),
}));

const input: ResearchInput = {
  sessionId: "sess_000000000000000000000000",
  category: "laptop",
  rubric: [
    { criterionId: "cpu", title: "CPU speed", weight: 8 },
    { criterionId: "battery", title: "Battery life", weight: 6 },
    { criterionId: "keys", title: "Key comfort", weight: 4 },
  ],
  mustHaves: [],
  exclusions: [],
};

const CONFIDENCE_LINE = "Confidence was worked out for your original priorities.";
const NOT_SCORED = "Not scored for these priorities. Run the search again to score it.";
const COMPARE_LIMIT = "You can compare up to 4 products. Remove one to pick another.";

let root: Root | null = null;
let fetchSpy: ReturnType<typeof vi.fn>;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

beforeEach(() => {
  fetchSpy = vi.fn(() => Promise.reject(new Error("no request may be made")));
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  // Re-ranking is worked out in the browser: no request, ever (P-42).
  expect(fetchSpy).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});

function render(result: ResearchResult) {
  const host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  act(() =>
    root!.render(
      <ResultsView result={result} heading="Results for your laptop" sessionId={input.sessionId} />,
    ),
  );
}

/** Waits for something a lazily loaded part shows. */
async function until<T>(find: () => T | null | undefined): Promise<T> {
  for (let i = 0; i < 300; i++) {
    const found = find();
    if (found) return found;
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
  throw new Error("it never appeared");
}

const button = (name: string) =>
  [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === name);

function click(name: string) {
  const target = button(name);
  if (!target) throw new Error(`no button named ${name}`);
  act(() => target.click());
}

function field(label: string): HTMLInputElement | null {
  const l = [...document.querySelectorAll("label")].find((x) => x.textContent?.trim() === label);
  return l ? (document.getElementById(l.htmlFor) as HTMLInputElement | null) : null;
}

/** Types into a field the way a browser does, so React sees the change. */
function type(label: string, value: string) {
  const target = field(label);
  if (!target) throw new Error(`no field labelled ${label}`);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(target, value);
    target.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

const cards = () => [...document.querySelectorAll("article")];
const nameOf = (card: Element) =>
  document.getElementById(card.getAttribute("aria-labelledby") ?? "")?.textContent ?? "";
const order = () => cards().map(nameOf);
const moveOf = (card: Element) =>
  card.querySelector("[data-move]")?.textContent?.replace("Since your original priorities: ", "");
const checkbox = (name: string) =>
  document.querySelector<HTMLInputElement>(`input[aria-label="Compare ${name}"]`)!;

async function openPanel() {
  click("Change what matters");
  await until(() => field("CPU speed, from 0 to 10"));
}

describe("re-rank", () => {
  it("re-orders at once, says each move, hides chance first and confidence, and going back restores it exactly", async () => {
    const result = buildMockResult(input, "success");
    const [a, b, c] = result.products.map((p) => p.name);
    render(result);
    const closed = cards().map((x) => x.outerHTML);
    expect(document.body.textContent).toContain("62% chance of ranking first");
    expect(document.querySelector("[data-original-confidence]")).toBeNull();

    await openPanel();
    // Open, card bodies off screen wait to be drawn (content-visibility); nothing else changes.
    const before = cards().map((x) => x.outerHTML);
    type("CPU speed, from 0 to 10", "0");
    type("Battery life, from 0 to 10", "0");
    type("Key comfort, from 0 to 10", "10");

    expect(order().map((n) => [a, b, c].find((x) => n.includes(x!)))).toEqual([c, b, a]);
    expect(cards().map(moveOf)).toEqual(["Up 2", "Same", "Down 2"]);
    for (const card of cards()) {
      expect(card.textContent).not.toMatch(/chance of ranking first/);
      expect(card.textContent).not.toMatch(/confidence/i);
    }
    expect(document.querySelector("[data-original-confidence]")?.textContent).toBe(CONFIDENCE_LINE);
    expect(document.querySelector('[aria-live="polite"]')?.textContent).toContain(
      `List changed. ${c} is first.`,
    );

    click("Back to my original priorities");
    expect(cards().map((x) => x.outerHTML)).toEqual(before);
    expect(document.querySelector("[data-original-confidence]")).toBeNull();
    expect(document.body.textContent).toContain("62% chance of ranking first");
    expect(field("CPU speed, from 0 to 10")?.value).toBe("8");
    expect(document.activeElement).toBe(button("Hide what matters"));
    click("Hide what matters");
    expect(cards().map((x) => x.outerHTML)).toEqual(closed);
  });

  it("with every weight at 0, says so and keeps decide's order and scores", async () => {
    const result = buildMockResult(input, "success");
    render(result);
    const before = order();
    const scores = result.products.map((p) => `Score ${p.score}`);
    await openPanel();
    for (const w of input.rubric) type(`${w.title}, from 0 to 10`, "0");
    expect(document.querySelector("[data-rerank-note]")?.textContent).toBe(ALL_ZERO_NOTE);
    expect(order()).toEqual(before);
    expect(cards().map(moveOf)).toEqual(["Same", "Same", "Same"]);
    cards().forEach((card, i) => expect(card.textContent).toContain(scores[i]));
  });

  it("refuses a weight outside 0 to 10 and keeps the last good one", async () => {
    render(buildMockResult(input, "success"));
    await openPanel();
    type("CPU speed, from 0 to 10", "12");
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(
      "Enter a number from 0 to 10.",
    );
    expect(document.querySelector("[data-original-confidence]")).toBeNull();
  });

  it("has no controls on a result with nothing to re-rank", () => {
    const result = buildMockResult(input, "success");
    result.rerank = null;
    render(result);
    expect(button("Change what matters")).toBeUndefined();
    expect(document.querySelector('input[aria-label^="Compare "]')).toBeNull();
  });
});

describe("re-filter", () => {
  it("a higher budget lets a product back in, last, unscored and with no rank number", async () => {
    const result = buildMockResult(input, "refilter");
    const d = result.rerank!.others[0]!.name;
    render(result);
    expect(document.body.textContent).toContain("Left out (2)");
    await openPanel();
    type("Budget up to (₹)", "90000");
    const last = cards().at(-1)!;
    expect(nameOf(last)).toContain(d);
    expect(last.querySelector("[data-unscored]")?.textContent).toBe(NOT_SCORED);
    expect(last.textContent).not.toMatch(/#\d/);
    expect(last.textContent).not.toMatch(/Score \d/);
    expect(document.body.textContent).toContain("Left out (1)");
  });

  it("a budget below every price shows what one change would let through", async () => {
    render(buildMockResult(input, "refilter"));
    await openPanel();
    type("Budget up to (₹)", "50000");
    expect(cards()).toHaveLength(0);
    expect(document.body.textContent).toContain("Removing your ₹50,000 budget would let");
    expect(document.body.textContent).toContain("No option matches these must-haves.");
  });

  it("a must-have switched on filters out what fails it, with decide's kind of reason", async () => {
    const result = buildMockResult(input, "refilter");
    const b = result.products[1]!.name;
    render(result);
    await openPanel();
    const touch = field("Must have: with a touchscreen") ?? checkboxLabelled("with a touchscreen");
    act(() => touch.click());
    expect(order().some((n) => n.includes(b))).toBe(false);
    expect(document.body.textContent).toContain("Left out (3)");
  });

  it("a result from before checked values were kept re-ranks but offers no must-haves", async () => {
    const result = buildMockResult(input, "refilter");
    result.rerank = { ...result.rerank!, canRefilter: false };
    render(result);
    await openPanel();
    expect([...document.querySelectorAll("legend")].map((l) => l.textContent)).not.toContain(
      "Must-haves",
    );
  });
});

function checkboxLabelled(words: string): HTMLInputElement {
  const label = [...document.querySelectorAll("label")].find((l) => l.textContent?.includes(words));
  const box = label?.querySelector("input");
  if (!box) throw new Error(`no checkbox for ${words}`);
  return box;
}

describe("compare", () => {
  it("refuses a fifth pick and says the limit", () => {
    // Five of the fifty: enough for a fifth pick, and quick to render here.
    const result = buildMockResult(input, "many");
    result.products = result.products.slice(0, 5);
    result.rerank = { ...result.rerank!, scored: result.rerank!.scored.slice(0, 5) };
    render(result);
    const names = result.products.map((p) => p.name);
    for (const n of names.slice(0, 4)) act(() => checkbox(n).click());
    act(() => checkbox(names[4]!).click());
    expect(checkbox(names[4]!).checked).toBe(false);
    expect(document.body.textContent).toContain(COMPARE_LIMIT);
    expect(button("Compare (4)")?.disabled).toBe(false);
    act(() => checkbox(names[0]!).click());
    act(() => checkbox(names[4]!).click());
    expect(checkbox(names[4]!).checked).toBe(true);
    expect(document.body.textContent).not.toContain(COMPARE_LIMIT);
  });

  it("needs two picks before it opens", () => {
    const result = buildMockResult(input, "success");
    render(result);
    expect(button("Compare")?.disabled).toBe(true);
    act(() => checkbox(result.products[0]!.name).click());
    expect(button("Compare (1)")?.disabled).toBe(true);
  });

  it("shows each value with its state and sources, in a labelled scrolling region, and closes back to its button", async () => {
    const result = buildMockResult(input, "success");
    render(result);
    const names = result.products.map((p) => p.name);
    for (const n of names) act(() => checkbox(n).click());
    click("Compare (3)");
    const heading = await until(() =>
      [...document.querySelectorAll("h2")].find((h) => h.textContent === "Compare 3 products"),
    );
    expect(document.activeElement).toBe(heading);
    const region = document.querySelector<HTMLElement>('[role="region"]')!;
    expect(region.getAttribute("aria-label")).toBe("Comparison table");
    expect(region.tabIndex).toBe(0);
    const table = region.querySelector("table")!;
    expect([...table.querySelectorAll('th[scope="col"]')].map((th) => th.textContent)).toEqual([
      "Criterion",
      ...names,
    ]);
    const rows = [...table.querySelectorAll('th[scope="row"]')].map((th) => th.textContent);
    expect(rows).toEqual(result.products[0]!.criteria.map((c) => c.label));
    expect(table.textContent).toMatch(/Verified|Estimated|Sources disagree|Not verified/);
    expect(table.textContent).not.toMatch(/\bundefined\b|\bnull\b/);
    expect(table.querySelectorAll('button[aria-label^="Sources for "]').length).toBeGreaterThan(0);

    click("Close comparison");
    expect(document.querySelector('[role="region"]')).toBeNull();
    expect(document.activeElement).toBe(button("Compare (3)"));
  });

  it("can compare a product a change let back in", async () => {
    const result = buildMockResult(input, "refilter");
    const d = result.rerank!.others[0]!.name;
    render(result);
    await openPanel();
    type("Budget up to (₹)", "90000");
    act(() => checkbox(result.products[0]!.name).click());
    act(() => checkbox(d).click());
    click("Compare (2)");
    const region = await until(() => document.querySelector('[role="region"]'));
    expect(region.textContent).toContain(d);
  });
});

describe("after the review", () => {
  const legends = () => [...document.querySelectorAll("legend")].map((l) => l.textContent);

  it("hiding the panel keeps the changes, and opening it again shows them", async () => {
    render(buildMockResult(input, "success"));
    await openPanel();
    type("CPU speed, from 0 to 10", "0");
    type("Key comfort, from 0 to 10", "10");
    const changed = order();
    click("Hide what matters");
    expect(document.querySelector("[data-original-confidence]")).not.toBeNull();
    click("Change what matters");
    expect(field("Key comfort, from 0 to 10")?.value).toBe("10");
    expect(field("CPU speed, from 0 to 10")?.value).toBe("0");
    expect(order()).toEqual(changed);
  });

  it("says the reasons, points and data coverage are for the original priorities too", async () => {
    render(buildMockResult(input, "success"));
    await openPanel();
    type("CPU speed, from 0 to 10", "0");
    expect(
      document.querySelector("[data-original-confidence]")?.parentElement?.textContent,
    ).toContain("So were each product's reasons, points and data coverage.");
  });

  it("drops a compare pick that a change holds back", async () => {
    const result = buildMockResult(input, "refilter");
    const [a, b] = result.products.map((p) => p.name);
    render(result);
    act(() => checkbox(a!).click());
    act(() => checkbox(b!).click());
    expect(button("Compare (2)")?.disabled).toBe(false);
    await openPanel();
    act(() => checkboxLabelled("with a touchscreen").click()); // B has none
    expect(document.querySelector(`input[aria-label="Compare ${b}"]`)).toBeNull();
    expect(button("Compare (1)")?.disabled).toBe(true);
  });

  it("refuses a budget whose lowest amount is above its highest", async () => {
    const result = buildMockResult(input, "refilter");
    result.rerank = { ...result.rerank!, budget: { min: 40000, max: 80000, currency: "INR" } };
    render(result);
    await openPanel();
    type("Budget from (₹)", "90000");
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(
      "The lowest amount must be below the highest.",
    );
    expect(document.querySelector("[data-original-confidence]")).toBeNull();
  });

  it("offers the must-haves when decide showed nothing, and lets a product back in unscored", async () => {
    const none = structuredClone(searchExample as unknown as SearchResult);
    none.decision!.ranked = [];
    const result = resultFromSearch(none, []);
    expect(result.products).toEqual([]);
    render(result);
    click("Change what matters");
    await until(() => legends().includes("Must-haves") || null);
    expect(legends()).not.toContain("How much each one matters");
    act(() => checkboxLabelled("at least 16 GB RAM").click());
    const heading = [...document.querySelectorAll("h2")].find(
      (h) => h.textContent === "Found, not scored",
    );
    expect(heading).toBeDefined();
    const vela = cards().find((c) => nameOf(c).includes("Vela"));
    expect(vela?.querySelector("[data-unscored]")?.textContent).toBe(NOT_SCORED);
  });

  it("shows both sides of a disputed value in the comparison, each with its source, and an estimate's coverage", async () => {
    const result = buildMockResult(input, "success");
    const [a, b] = result.products;
    const row = a!.criteria[0]!;
    a!.criteria[0] = {
      ...row,
      state: "disputed",
      value: null,
      claims: [
        { ...row.claims![0]!, claimId: "c1", side: "supporting", valueText: "16 GB" },
        { ...row.claims![0]!, claimId: "c2", side: "conflicting", valueText: "8 GB" },
      ],
    };
    delete a!.criteria[0]!.sources;
    b!.criteria[0] = { ...b!.criteria[0]!, state: "estimated" };
    render(result);
    act(() => checkbox(a!.name).click());
    act(() => checkbox(b!.name).click());
    click("Compare (2)");
    const region = await until(() => document.querySelector('[role="region"]'));
    const cells = [...region.querySelectorAll("tbody tr:first-child td")];
    expect(cells[0]!.textContent).toContain("Sources disagree");
    expect(cells[0]!.textContent).toContain("16 GB");
    expect(cells[0]!.textContent).toContain("8 GB");
    expect(cells[1]!.textContent).toMatch(/Estimated\. Data coverage \d+%/);
  });
});
