import { describe, expect, it } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import {
  FINISH_FIRST,
  createLiveResearchService,
  resultFromSearch,
} from "@/lib/results/liveAdapter";
import { ResearchError, type RubricItem } from "@/lib/results/types";
import type { SearchResult } from "@/types/contracts/SearchResult";
import { fakeBackend, json, scripted } from "./helpers/fakeBackend";
import { SID, asked, rubric, stepOf } from "./helpers/interviewSteps";

// JSON imports type arrays loosely, so the shared example is typed as the contract it was exported from.
const example = searchExample as unknown as SearchResult;
const T0 = Date.parse(example.started_at);
const viewRubric: RubricItem[] = rubric.weights.map((w) => ({
  criterionId: w.key,
  title: w.key === "cpu_performance" ? "CPU performance" : w.key,
  weight: w.normalized,
}));
const finished = stepOf("finished", [asked("cpu_performance", "answered", "High")]);
const session = (step: unknown, request = "best laptop for coding under 70k") =>
  json(200, { session_id: SID, request, interview_step: step });
const input = {
  sessionId: SID,
  category: "laptop",
  rubric: viewRubric,
  mustHaves: [],
  exclusions: [],
};
const run = (svc: ReturnType<typeof createLiveResearchService>) =>
  svc.run(input, { signal: new AbortController().signal, onEvent: () => {} });

describe("SearchResult -> results page", () => {
  const r = resultFromSearch(example, viewRubric, T0);

  it("is labelled live, with the backend's notes", () => {
    expect(r.source).toBe("live");
    expect(r.saved).toBe(example.saved);
    expect(r.partial).toBe(false);
    expect(r.coverageNote).toBe(example.coverage_note);
    expect(r.pricesCheckedAt).toBe("2026-10-03T08:00:00Z");
  });

  it("orders products by rank, and gives tied products the same rank", () => {
    expect(r.products.map((p) => [p.name, p.rank])).toEqual([
      ["Nova Book 14", 1],
      ["Kestrel Slim 15", 1], // tied with Nova Book 14: shown as "Tied for 1st", never a made-up order
      ["Orion Play 15", 3],
    ]);
  });

  it("keeps score, confidence and chance of being first", () => {
    const best = r.products[0]!;
    expect(best).toMatchObject({
      id: "prd_nova_book_14",
      score: 78.3,
      confidence: "medium",
      chanceFirst: 46,
    });
  });

  it("points add up to the score", () => {
    for (const p of r.products) {
      const sum = (p.contributions ?? []).reduce((a, c) => a + c.points, 0);
      expect(sum).toBeCloseTo(p.score!, 0);
    }
    expect(r.products[0]!.contributions![0]).toMatchObject({
      criterionId: "cpu_performance",
      label: "CPU performance",
    });
  });

  it("keeps every value state and never invents a missing one", () => {
    const kestrel = r.products[1]!;
    const orion = r.products[2]!;
    expect(kestrel.criteria.find((c) => c.criterionId === "battery_life")?.state).toBe("disputed");
    expect(orion.criteria.find((c) => c.criterionId === "build_durability")).toMatchObject({
      state: "estimated",
      value: "7.25 / 10",
    });
    expect(orion.coverage).toEqual({ checked: 6, total: 7 });
    // The contract carries no claims, so a disputed value can't list what each source said.
    expect(kestrel.criteria.every((c) => c.sources === undefined)).toBe(true);
    expect(r.products.every((p) => p.imageUrl === null && p.value === null)).toBe(true);
  });

  it("a criterion with no value at all is unknown, not zero", () => {
    const thin = {
      ...example,
      products: example.products.map((p) => ({ ...p, values: [] })),
    };
    const rows = resultFromSearch(thin, viewRubric, T0).products[0]!.criteria;
    expect(rows.every((c) => c.state === "unknown" && c.value === null)).toBe(true);
  });

  it("offers older than 24 hours are marked stale", () => {
    const [nova, kestrel, orion] = r.products;
    expect(nova!.offers).toEqual([
      {
        store: "Example Store",
        price: 64990,
        checkedAt: "2026-10-03T08:00:00Z",
        stale: false,
        url: "https://store.example.com/nova-book-14-16gb-512gb",
      },
    ]);
    expect(kestrel!.offers[0]?.stale).toBe(true);
    expect(orion!.offers).toEqual([]);
  });

  it("only rupee prices are shown", () => {
    const usd = {
      ...example,
      commerce: {
        ...example.commerce!,
        offers: example.commerce!.offers.map((o) => ({
          ...o,
          price: { amount: 999, currency: "USD" },
        })),
      },
    };
    expect(resultFromSearch(usd, viewRubric, T0).products[0]!.offers[0]?.price).toBeNull();
  });

  it("joins the explanation and the trade-off", () => {
    expect(r.products[0]!.explanation).toContain("most comfortable of the three");
    expect(r.products[0]!.explanation).toContain("compiles faster, but it is much heavier");
    expect(r.products[2]!.explanation).toBeNull();
  });

  it("lists left-out and can't-tell products with their reasons", () => {
    expect(r.leftOut).toEqual([
      { name: "Aurora Air 13", reason: "₹84,990 is above your ₹70,000 budget" },
    ]);
    expect(r.couldntCheck).toEqual([
      { name: "Vela Pro 14", reason: "We couldn't confirm it has at least 16 GB RAM" },
    ]);
  });

  it("a relaxation hint names the shopper's own words for the rule, or nothing", () => {
    const nothing: SearchResult = {
      ...example,
      decision: {
        ...example.decision!,
        ranked: [],
        relaxation_hint: "No laptop has 16 GB RAM under your budget.",
        filtered: [
          {
            product_id: "prd_vela_pro_14",
            group: "filtered_out",
            reasons: ["Only 8 GB RAM"],
            rule_attribute: "ram_gb",
          },
        ],
      },
    };
    expect(resultFromSearch(nothing, viewRubric, T0).relaxation).toEqual({
      hint: "No laptop has 16 GB RAM under your budget.",
      mustHave: "at least 16 GB RAM",
    });
    const unnamed: SearchResult = {
      ...nothing,
      decision: {
        ...nothing.decision!,
        filtered: [{ ...nothing.decision!.filtered[0]!, rule_attribute: null }],
      },
    };
    expect(resultFromSearch(unnamed, viewRubric, T0).relaxation?.mustHave).toBe("");
  });
});

describe("running a search", () => {
  it("sends the finished rubric and the request as typed", async () => {
    const { client, calls } = fakeBackend(scripted(session(finished), json(200, example)));
    const svc = createLiveResearchService(client);
    expect(svc).toMatchObject({ streamsProgress: false, isMock: false });
    const result = await run(svc);
    expect(calls.map((c) => [c.method, c.path])).toEqual([
      ["GET", `/api/presearch/${SID}`],
      ["POST", "/api/search"],
    ]);
    expect(calls[1]?.body).toEqual({
      session_id: SID,
      user_id: null,
      raw_query: "best laptop for coding under 70k",
      rubric,
    });
    expect(result.source).toBe("live");
  });

  it("won't search before the interview is finished", async () => {
    const asking = stepOf("question", [asked("cpu_performance", "pending")]);
    for (const step of [null, asking]) {
      const { client, calls } = fakeBackend(() => session(step));
      const err = await run(createLiveResearchService(client)).catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ResearchError);
      expect(err).toMatchObject({ kind: "unknown", message: FINISH_FIRST });
      expect(calls).toHaveLength(1);
    }
  });

  it("a lost connection is a network error, a 5xx a server error", async () => {
    const down = fakeBackend(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(run(createLiveResearchService(down.client))).rejects.toMatchObject({
      kind: "network",
    });
    const broken = fakeBackend(scripted(session(finished), json(500, {})));
    await expect(run(createLiveResearchService(broken.client))).rejects.toMatchObject({
      kind: "server",
    });
  });

  it("rejects a result that breaks the SearchResult contract", async () => {
    const bad = { ...example, search_id: "not-a-search-id" };
    const { client } = fakeBackend(scripted(session(finished), json(200, bad)));
    await expect(run(createLiveResearchService(client))).rejects.toMatchObject({
      kind: "unknown",
      message: expect.stringMatching(/couldn't read/),
    });
  });

  it("a session ID in the wrong shape is never sent", async () => {
    const { client, calls } = fakeBackend(() => session(finished));
    const svc = createLiveResearchService(client);
    await expect(
      svc.run(
        { ...input, sessionId: "abc" },
        { signal: new AbortController().signal, onEvent: () => {} },
      ),
    ).rejects.toBeInstanceOf(ResearchError);
    expect(calls).toHaveLength(0);
  });
});
