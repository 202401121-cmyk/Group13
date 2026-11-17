/**
 * S4-30: the results page says only what the result supports. With no product scored there is no best match, no
 * runner-up and no "#1"; "Prices checked" comes only from shown offers that have a price; a price a source gave for
 * a product with no offer is shown apart from offers, never as a buy link. And, from S4-5, the mock interview's reason
 * for a question reads as the backend writes it, it can stop early, and progress then reaches 100%.
 */
import { act, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import { ResultCard } from "@/components/results/ResultCard";
import { checkContract } from "@/lib/api/schemas";
import {
  SETTLED_AFTER,
  criteriaFor,
  createMockInterviewService,
  whyNow,
} from "@/lib/interview/mockService";
import { initialInterview, interviewReducer } from "@/lib/interview/reducer";
import { progressOf } from "@/lib/interview/view";
import type { Brief } from "@/lib/request/types";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import { buildMockResult } from "@/lib/results/mockResults";
import type { ProductResult } from "@/lib/results/types";
import { newestPriceTime, shortDay, sourceDateLabel } from "@/lib/results/view";
import type { SearchResult } from "@/types/contracts/SearchResult";

const example = searchExample as unknown as SearchResult;
const T0 = Date.parse(example.started_at);
const ORION = "prd_orion_play_15"; // the example's product with no offer
const CLAIM = "clm_053f4bf83174cf53"; // from example-reviews.in, read 3 Oct

function show(ui: ReactElement): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(ui);
  return host;
}

/** The example with a price_inr value for Orion, as extract and verify can give one. */
function withPrice(value: number | string, state: "known" | "unknown" = "known", claims = [CLAIM]) {
  const copy = structuredClone(example);
  const orion = copy.products.find((p) => p.product.id === ORION)!;
  orion.values.push({
    product_id: ORION,
    criterion_key: "price_inr",
    state,
    score: null,
    value,
    confidence: 0.8,
    supporting_claim_ids: claims,
    conflicting_claim_ids: [],
    alternatives: [],
  });
  return copy;
}

const orionOf = (copy: SearchResult) =>
  resultFromSearch(copy, [], T0).products.find((p) => p.id === ORION)!;

describe("a price a source gave, for a product with no offer (DECIDED 3)", () => {
  it("is still a valid SearchResult", async () => {
    await expect(checkContract("SearchResult", withPrice(28980))).resolves.toBeDefined();
  });

  it("shows a number in rupees, with the site and the day it was read", () => {
    expect(orionOf(withPrice(28980)).sourcedPrice).toMatchObject({
      text: "₹28,980",
      site: "example-reviews.in",
      seen: "3 Oct",
      state: "verified",
    });
  });

  it("names the same day the drawer does, whatever the time zone", () => {
    const late = "2026-11-14T20:00:00Z"; // 15 Nov in India
    expect(shortDay(late)).toBe("14 Nov");
    expect(sourceDateLabel(null, late)).toBe("Checked 14 Nov 2026");
  });

  it("shows text as the source wrote it, never read as a price here", () => {
    expect(orionOf(withPrice("Rs 28,980 at launch")).sourcedPrice?.text).toBe(
      "Rs 28,980 at launch",
    );
  });

  it("is none without a claim, for an unknown value, or when a store offers the product", () => {
    expect(orionOf(withPrice(28980, "known", [])).sourcedPrice).toBeNull();
    expect(orionOf(withPrice(28980, "unknown")).sourcedPrice).toBeNull();
    const offered = withPrice(28980);
    offered.commerce!.offers[0]!.product_id = ORION;
    expect(orionOf(offered).sourcedPrice).toBeNull();
  });

  it("the card shows it apart from offers, with its sources, and never as a buy link", async () => {
    const p = orionOf(withPrice(28980));
    const onShowSources = vi.fn();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () =>
      root.render(<ResultCard product={p} tiedWith={[]} index={0} onShowSources={onShowSources} />),
    );
    const line = host.querySelector("[data-sourced-price]")!;
    expect(line).toHaveTextContent("Price seen in a source: ₹28,980 on example-reviews.in, 3 Oct");
    expect(line.querySelector("a")).toBeNull();
    expect(host).toHaveTextContent("No store offer");
    expect(host).not.toHaveTextContent("No price found");
    await act(async () => line.querySelector("button")!.click());
    expect(onShowSources.mock.calls[0]![1]).toMatchObject({
      criterionId: "price_inr",
      label: "Price",
    });
    await act(async () => root.unmount());
    host.remove();
  });
});

describe("prices checked (DECIDED 2)", () => {
  const offer = (checkedAt: string, price: number | null) => ({
    store: "S",
    price,
    checkedAt,
    stale: false,
    url: null,
  });
  const product = (offers: ReturnType<typeof offer>[]) => ({ offers }) as unknown as ProductResult;

  it("is the newest shown offer that has a price, by time, not by text", () => {
    expect(
      newestPriceTime([
        product([offer("2026-11-15T10:00:00+05:30", 100)]), // 04:30 UTC
        product([offer("2026-11-15T05:00:00Z", 100), offer("2026-11-15T09:00:00Z", null)]),
      ]),
    ).toBe("2026-11-15T05:00:00Z");
  });

  it("is null when no shown offer has a price", () => {
    expect(
      newestPriceTime([product([offer("2026-11-15T05:00:00Z", null)]), product([])]),
    ).toBeNull();
  });

  it("live: offers without a price or for products not shown say nothing", () => {
    const copy = structuredClone(example);
    const offers = copy.commerce!.offers;
    Object.assign(offers[1]!, { price: null, as_of: "2026-10-04T08:00:00Z" });
    offers.push({ ...offers[0]!, product_id: "prd_aurora_air_13", as_of: "2026-10-05T08:00:00Z" });
    expect(resultFromSearch(copy, [], T0).pricesCheckedAt).toBe("2026-10-03T08:00:00Z");
  });
});

describe("a page where nothing is scored (DECIDED 1)", () => {
  const input = {
    sessionId: "sess_resultshonest000001",
    category: "laptop",
    rubric: [{ criterionId: "cpu", title: "CPU performance", weight: 3 }],
    mustHaves: [],
    exclusions: [],
  };
  const now = Date.parse("2026-11-17T10:00:00Z");

  it("the mock's not-ranked result has no score, no label and no price time", () => {
    const r = buildMockResult(input, "not-ranked", now);
    expect(r.products.map((p) => [p.score, p.label])).toEqual(r.products.map(() => [null, null]));
    expect(r.pricesCheckedAt).toBeNull();
    expect(r.coverageNote).toMatch(/^Not ranked: /);
  });

  it("an unranked card has no Best match, Runner-up or #N; a ranked one with no score keeps #N", () => {
    const [a, b] = buildMockResult(input, "not-ranked", now).products;
    const plain = show(<ResultCard product={a!} tiedWith={[]} featured index={0} ranked={false} />);
    expect(plain).not.toHaveTextContent("Best match");
    expect(plain).not.toHaveTextContent("#1");
    expect(plain).not.toHaveTextContent("Rank 1");
    expect(plain).not.toHaveTextContent("ranks here");
    expect(plain).toHaveTextContent("What we found");
    expect(plain).not.toHaveTextContent("Main reasons");
    expect(plain).toHaveTextContent("No score");
    expect(
      show(
        <ResultCard
          product={{ ...b!, label: "runner_up" }}
          tiedWith={[]}
          index={1}
          ranked={false}
        />,
      ),
    ).not.toHaveTextContent("Runner-up");
    // On a page where some products are scored, an unscored one is ranked as decide ranked it.
    const mixed = show(<ResultCard product={{ ...b!, rank: 3 }} tiedWith={[]} index={2} />);
    expect(mixed).toHaveTextContent("#3");
    expect(mixed).toHaveTextContent("No score");
  });
});

it("live: an unscored product has no main reasons, only what was found", () => {
  const copy = structuredClone(example);
  for (const r of copy.decision!.ranked) {
    Object.assign(r, { score: null, label: null, gap_to_leader: null, chance_first: null });
    for (const c of r.contributions) Object.assign(c, { state: "unknown", contribution: 0 });
  }
  const products = resultFromSearch(copy, [], T0).products;
  expect(products.map((p) => p.reasons)).toEqual(products.map(() => []));
});

describe("the mock interview, as the smart one (S4-5)", () => {
  const brief = (product: string): Brief => ({
    product,
    usedFor: null,
    budget: null,
    region: null,
    mustHaves: [],
    exclusions: [],
    source: "model",
  });
  let service = createMockInterviewService(0);
  afterEach(() => {
    service = createMockInterviewService(0);
  });

  it("says why a question comes now, worded as backend/app/interview/why.py writes it", () => {
    const [cpu] = criteriaFor(brief("Laptop")).criteria;
    expect(whyNow(cpu!)).toBe(
      `CPU performance: it usually matters a lot and we don't know your view yet, so we ask now. ${cpu!.definition}`,
    );
    expect(whyNow({ id: "x", title: "Port choice", definition: "d".repeat(150) })).toBe(
      "Port choice: it usually matters and we don't know your view yet, so we ask now.",
    );
  });

  it("stops once settled, before asking every criterion, and progress then reads 100%", async () => {
    const { criteria } = criteriaFor(brief("Camera"));
    expect(criteria.length).toBeGreaterThan(SETTLED_AFTER);
    let s = interviewReducer(initialInterview, {
      type: "CRITERIA_LOADED",
      criteria,
      fallback: false,
    });
    s = interviewReducer(s, { type: "START" });
    s = interviewReducer(s, {
      type: "TURN_OK",
      result: await service.turn({ brief: brief("Camera"), criteria, answers: {} }),
    });
    let asked = 0;
    while (s.phase === "asking") {
      expect(s.step?.why).toMatch(
        /: it (usually matters a lot|usually matters|can matter) and we don't know/,
      );
      asked += 1;
      s = interviewReducer(s, { type: "SEND", text: "High" });
      const result = await service.turn({
        brief: brief("Camera"),
        criteria,
        answers: s.answers,
        turn: s.pending!,
      });
      s = interviewReducer(s, { type: "TURN_OK", result });
    }
    expect(s.phase).toBe("finished");
    expect(asked).toBe(SETTLED_AFTER);
    expect(progressOf(s)).toEqual({ settled: criteria.length, total: criteria.length });
  });
});
