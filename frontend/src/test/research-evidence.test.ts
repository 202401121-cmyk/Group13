/**
 * US-21: "Where this comes from" asks the evidence route, checked against its contract. Whatever goes wrong, the
 * screen still gets the claims the result already carries, and nothing is ever thrown at it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import evidenceDisputed from "@contracts/examples/evidence_disputed.json";
import evidenceUnknown from "@contracts/examples/evidence_unknown.json";
import { WITHHELD, createLiveResearchService } from "@/lib/results/liveAdapter";
import { createMockResearchService } from "@/lib/results/mockService";
import type { ClaimView, ResearchInput } from "@/lib/results/types";
import { BASE, fakeBackend, json } from "./helpers/fakeBackend";

type Handler = Parameters<typeof fakeBackend>[0];

const SEARCH = evidenceDisputed.search_id;
const PRODUCT = evidenceDisputed.product_id;
const CRITERION = evidenceDisputed.criterion_key;

/** The claims the result itself carries: what the screen shows when the route can't answer. */
const FALLBACK: ClaimView[] = [
  {
    claimId: "clm_2c31271c5c981d18",
    side: "supporting",
    quote: "battery rated up to 10 hours",
    quoteWithheld: null,
    source: {
      id: "src_2958fd9d1b3643cf",
      name: "kestrel.example.com",
      typeLabel: "Maker's site",
      url: "https://kestrel.example.com/slim-15/specs",
      dateLabel: "Checked 3 Oct 2026",
    },
  },
];
const FELL_BACK = { state: null, claims: FALLBACK, withheldReason: null, from: "citations" };

function live(handler: Handler) {
  const { client, calls } = fakeBackend(handler);
  const svc = createLiveResearchService(client, {
    baseUrl: BASE,
    openStream: () => {
      throw new Error("no stream is needed here");
    },
  });
  return { svc, calls };
}

describe("evidence route (live)", () => {
  it("maps a disputed answer: the winning claim supports, the other disagrees", async () => {
    const { svc, calls } = live(() => json(200, evidenceDisputed));
    const ev = await svc.evidence(SEARCH, PRODUCT, CRITERION, FALLBACK);
    expect(calls.map((c) => [c.method, c.path])).toEqual([
      [
        "GET",
        `/api/search/runs/${SEARCH}/evidence?product_id=${PRODUCT}&criterion_key=${CRITERION}`,
      ],
    ]);
    expect(ev).toEqual({
      state: "disputed",
      from: "route",
      withheldReason: null,
      claims: [
        {
          claimId: "clm_2c31271c5c981d18",
          side: "supporting",
          quote: "battery rated up to 10 hours",
          quoteWithheld: null,
          valueText: "10",
          source: {
            id: "src_2958fd9d1b3643cf",
            name: "kestrel.example.com",
            typeLabel: "Maker's site",
            url: "https://kestrel.example.com/slim-15/specs",
            dateLabel: "Checked 3 Oct 2026",
          },
        },
        {
          claimId: "clm_46feb890769b8225",
          side: "conflicting",
          quote: "I get about 6 hours, not 10",
          quoteWithheld: null,
          valueText: "6",
          source: {
            id: "src_edb2d0c42af72797",
            name: "r/developersIndia",
            typeLabel: "Reddit",
            url: "https://www.reddit.com/r/developersIndia/comments/abc123/",
            dateLabel: "Checked 3 Oct 2026",
          },
        },
      ],
    });
  });

  it("maps an unknown answer with no claims", async () => {
    const { svc } = live(() => json(200, evidenceUnknown));
    const ev = await svc.evidence(
      evidenceUnknown.search_id,
      evidenceUnknown.product_id,
      evidenceUnknown.criterion_key,
      [],
    );
    expect(ev).toEqual({ state: "unknown", claims: [], withheldReason: null, from: "route" });
  });

  it("a withheld quote carries the route's reason, and keeps its link", async () => {
    const reason = "Reddit's words aren't kept in saved results.";
    const withheld = structuredClone(evidenceDisputed);
    withheld.claims[1]!.quote = null as unknown as string;
    withheld.quote_withheld_reason = reason as unknown as null;
    const { svc } = live(() => json(200, withheld));
    const ev = await svc.evidence(SEARCH, PRODUCT, CRITERION, FALLBACK);
    expect(ev.withheldReason).toBe(reason);
    expect(ev.claims[1]).toMatchObject({ quote: null, quoteWithheld: reason });
    expect(ev.claims[1]!.source.url).toMatch(/^https:\/\/www\.reddit\.com\//);
  });

  it("a withheld quote with no reason given still says why, by the kind of source", async () => {
    const withheld = structuredClone(evidenceDisputed);
    withheld.claims[0]!.quote = null as unknown as string;
    withheld.claims[1]!.quote = null as unknown as string;
    const { svc } = live(() => json(200, withheld));
    const ev = await svc.evidence(SEARCH, PRODUCT, CRITERION, FALLBACK);
    expect(ev.from).toBe("route");
    expect(ev.claims.map((c) => c.quoteWithheld)).toEqual([WITHHELD.other, WITHHELD.reddit]);
  });

  it("puts the IDs in the address safely", async () => {
    const { svc, calls } = live(() => json(404, { detail: "Not Found" }));
    await svc.evidence(SEARCH, PRODUCT, "battery life&x=1", FALLBACK);
    expect(calls[0]!.path).toBe(
      `/api/search/runs/${SEARCH}/evidence?product_id=${PRODUCT}&criterion_key=battery%20life%26x%3D1`,
    );
  });

  it("falls back to the result's own citations on a 404", async () => {
    const { svc } = live(() => json(404, { detail: "Not Found" }));
    expect(await svc.evidence(SEARCH, PRODUCT, CRITERION, FALLBACK)).toEqual(FELL_BACK);
  });

  it("falls back when the answer doesn't match the contract", async () => {
    const { svc } = live(() => json(200, { ...evidenceDisputed, state: "maybe" }));
    expect(await svc.evidence(SEARCH, PRODUCT, CRITERION, FALLBACK)).toEqual(FELL_BACK);
  });

  it("falls back when the answer is for another product or criterion", async () => {
    const { svc } = live(() => json(200, evidenceDisputed));
    expect(await svc.evidence(SEARCH, PRODUCT, "weight", FALLBACK)).toEqual(FELL_BACK);
    expect(await svc.evidence(SEARCH, "prd_nova_book_14", CRITERION, FALLBACK)).toEqual(FELL_BACK);
  });

  it("falls back on a network error or a server error", async () => {
    const down = live(() => {
      throw new TypeError("Failed to fetch");
    });
    expect(await down.svc.evidence(SEARCH, PRODUCT, CRITERION, FALLBACK)).toEqual(FELL_BACK);
    const broken = live(() => json(500, {}));
    expect(await broken.svc.evidence(SEARCH, PRODUCT, CRITERION, FALLBACK)).toEqual(FELL_BACK);
  });

  it("falls back without a request when there is no search ID", async () => {
    const { svc, calls } = live(() => json(200, evidenceDisputed));
    expect(await svc.evidence(null, PRODUCT, CRITERION, FALLBACK)).toEqual(FELL_BACK);
    expect(calls).toEqual([]);
  });
});

describe("evidence (mock)", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const input = (scenario: string): ResearchInput => ({
    sessionId: "sess_abcdef123456",
    category: "laptop",
    rubric: [
      { criterionId: "cpu", title: "CPU performance", weight: 3 },
      { criterionId: "ram", title: "Memory", weight: 2 },
      { criterionId: "battery", title: "Battery life", weight: 1 },
    ],
    mustHaves: [],
    exclusions: [],
    scenario,
  });

  /** Runs a mock search to its end and gives its result. */
  async function finished(scenario: string) {
    vi.useFakeTimers();
    const svc = createMockResearchService();
    const started = svc.start(input(scenario));
    await vi.advanceTimersByTimeAsync(400);
    const searchId = await started;
    await vi.advanceTimersByTimeAsync(10_000);
    const reading = svc.read(searchId, input(scenario));
    await vi.advanceTimersByTimeAsync(400);
    const run = await reading;
    return { svc, result: run.result! };
  }

  async function ask(
    svc: ReturnType<typeof createMockResearchService>,
    ...args: Parameters<ReturnType<typeof createMockResearchService>["evidence"]>
  ) {
    const answer = svc.evidence(...args);
    await vi.advanceTimersByTimeAsync(1_000);
    return answer;
  }

  it("answers a disputed value from the same data, with both sides", async () => {
    const { svc, result } = await finished("disputed");
    const a = result.products[0]!;
    const memory = a.criteria[1]!;
    expect(result.searchId).toMatch(/^srch_[0-9a-f]{24}$/);
    const ev = await ask(svc, result.searchId!, a.id, memory.criterionId, []);
    expect(ev.from).toBe("route");
    expect(ev.state).toBe("disputed");
    expect(ev.claims.map((c) => [c.side, c.valueText])).toEqual([
      ["supporting", "16 GB"],
      ["conflicting", "8 GB"],
    ]);
    expect(ev.claims).toEqual(memory.claims);
  });

  it("gives a withheld Reddit quote its reason", async () => {
    const { svc, result } = await finished("withheld-quote");
    const a = result.products[0]!;
    const ev = await ask(svc, result.searchId!, a.id, a.criteria[0]!.criterionId, []);
    const reddit = ev.claims.find((c) => c.source.typeLabel === "Reddit")!;
    expect(reddit.quote).toBeNull();
    expect(reddit.quoteWithheld).toBe(WITHHELD.reddit);
    expect(ev.withheldReason).toBe(WITHHELD.reddit);
  });

  it("falls back to what it was given for a search it doesn't know", async () => {
    vi.useFakeTimers();
    const svc = createMockResearchService();
    const ev = await ask(svc, "srch_000000000000000000000000", "mock-A", "cpu", FALLBACK);
    expect(ev).toEqual(FELL_BACK);
  });
});
