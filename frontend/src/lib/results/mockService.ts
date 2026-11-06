/**
 * Deterministic mock research adapter for the frontend preview. Nothing is
 * searched: product names, prices and values are fixtures, and every result is
 * marked source "mock" so the UI labels it as a preview.
 */
import type {
  CriterionValue,
  Offer,
  ProductResult,
  ResearchInput,
  ResearchResult,
  ResearchService,
  RubricItem,
} from "./types";
import { ResearchError } from "./types";
import { isPriceStale } from "./view";

export const MOCK_SCENARIOS = [
  { id: "success", label: "Ranked products" },
  { id: "tie", label: "Tie between products" },
  { id: "tie-first", label: "Tie for best match" },
  { id: "disputed", label: "Sources disagree" },
  { id: "unknown", label: "Unverified specs" },
  { id: "estimated", label: "Estimated values" },
  { id: "stale", label: "Old prices" },
  { id: "no-offer", label: "No store offers" },
  { id: "left-out", label: "Products left out" },
  { id: "couldnt-check", label: "Couldn't check some" },
  { id: "partial", label: "Partial results" },
  { id: "relaxation", label: "Relaxation hint" },
  { id: "empty", label: "No results" },
  { id: "timeout", label: "Research timeout" },
  { id: "network-retry", label: "Network error, then retry" },
] as const;
export type MockScenario = (typeof MOCK_SCENARIOS)[number]["id"];

/** The clock for a fixture built with no time given, so the same input always gives the same result. */
const FIXTURE_NOW = Date.parse("2026-10-27T11:40:00Z");
const HOUR_MS = 60 * 60 * 1000;
const STAGE_MS = 1400;

/** A mock price checked this many hours before `now`; the 24-hour rule then decides if it is old. */
const hoursAgo = (hours: number, now: number) => new Date(now - hours * HOUR_MS).toISOString();

function checkedAt(offer: Offer, time: string | null, now: number): Offer {
  return { ...offer, checkedAt: time, stale: isPriceStale(time, now) };
}

function values(rubric: RubricItem[], seed: number): CriterionValue[] {
  return rubric.map((r, i) => ({
    criterionId: r.criterionId,
    label: r.title,
    state: "verified" as const,
    value: `Sample rating ${((seed + i * 3) % 4) + 6} / 10`,
  }));
}

function product(
  letter: string,
  category: string,
  rank: number,
  score: number,
  rubric: RubricItem[],
  now: number,
): ProductResult {
  const criteria = values(rubric, rank);
  const contributions = criteria.map((c, i) => ({
    criterionId: c.criterionId,
    label: c.label,
    points: Math.max(2, 24 - i * 4 - rank),
  }));
  return {
    id: `mock-${letter}`,
    name: `Sample ${category} ${letter}`,
    rank,
    score,
    confidence: rank === 1 ? "high" : rank === 2 ? "medium" : "low",
    chanceFirst: rank === 1 ? 62 : rank === 2 ? 27 : 11,
    coverage: { checked: criteria.length, total: criteria.length },
    explanation: `Sample explanation: ${letter} scores well on the criteria you weighted most, within your must-haves.`,
    contributions,
    criteria,
    offers: [
      {
        store: "Sample Store One",
        price: 64990 + rank * 3500,
        url: rank === 1 ? "https://example.com/sample-product" : null,
      },
      { store: "Sample Store Two", price: 66490 + rank * 3500, url: null },
    ].map((o) => checkedAt({ ...o, checkedAt: null, stale: null }, hoursAgo(2, now), now)),
    value: { calculated: true, text: rank === 1 ? "Good value" : "Fair value" },
  };
}

export function buildMockResult(
  input: ResearchInput,
  scenario: string,
  now: number = FIXTURE_NOW,
): ResearchResult {
  const cat = input.category;
  const r = input.rubric;
  const products = [
    product("A", cat, 1, 86, r, now),
    product("B", cat, 2, 79, r, now),
    product("C", cat, 3, 71, r, now),
  ];
  const res: ResearchResult = {
    products,
    leftOut: [],
    couldntCheck: [],
    pricesCheckedAt: hoursAgo(2, now),
    saved: false,
    source: "mock",
  };
  const a = products[0]!;
  const b = products[1]!;
  const c = products[2]!;
  const tie = (x: ProductResult, y: ProductResult, rank: number, score: number) => {
    x.rank = y.rank = rank;
    x.score = y.score = score;
    x.separatingCriterion = y.separatingCriterion = r[0]?.title ?? null;
  };
  switch (scenario) {
    case "tie":
      tie(b, c, 2, 79);
      break;
    case "tie-first":
      tie(a, b, 1, 84);
      break;
    case "disputed":
      if (a.criteria[1]) {
        a.criteria[1] = {
          ...a.criteria[1],
          state: "disputed",
          value: null,
          sources: [
            { source: "Sample Store One listing", value: "16 GB" },
            { source: "Sample maker spec sheet", value: "8 GB" },
          ],
        };
      }
      a.confidence = "medium";
      break;
    case "unknown":
      for (const p of products) {
        const last = p.criteria.at(-1);
        if (last) Object.assign(last, { state: "unknown", value: null });
        if (p.coverage) p.coverage = { ...p.coverage, checked: p.coverage.total - 1 };
      }
      break;
    case "estimated":
      if (a.criteria[2])
        Object.assign(a.criteria[2], {
          state: "estimated",
          value: "About 9 hours (sample estimate)",
        });
      break;
    case "stale":
      a.offers[1] = checkedAt(a.offers[1]!, hoursAgo(72, now), now);
      b.offers = b.offers.map((o) => checkedAt(o, hoursAgo(72, now), now));
      c.offers[1] = checkedAt(c.offers[1]!, null, now);
      break;
    case "no-offer":
      b.offers = [];
      b.value = { calculated: false, reason: "No price was found, so value can't be worked out." };
      break;
    case "left-out":
      res.leftOut = [
        { name: `Sample ${cat} D`, reason: "Over your budget at every store checked." },
        { name: `Sample ${cat} E`, reason: "Matches something you asked to leave out." },
      ];
      break;
    case "couldnt-check":
      res.couldntCheck = [
        { name: `Sample ${cat} F`, reason: "Store pages didn't list its specs." },
      ];
      break;
    case "partial":
      res.partial = true;
      res.coverageNote =
        "Sample note: only two of the stores could be checked, so some options may be missing.";
      res.products = [a, b];
      break;
    case "relaxation":
    case "empty":
      res.products = [];
      res.leftOut = [{ name: `Sample ${cat} D`, reason: "Doesn't meet a must-have." }];
      if (scenario === "relaxation") {
        res.relaxation = {
          hint: "Sample hint: no option met every must-have. Loosening one may help.",
          mustHave: input.mustHaves[0] ?? "Your budget",
        };
      }
      break;
  }
  return res;
}

const wait = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });

export function createMockResearchService(): ResearchService {
  const attempts = new Map<string, number>();
  return {
    streamsProgress: true,
    isMock: true,
    async run(input, { signal, onEvent }) {
      const scenario = input.scenario ?? "success";
      const n = (attempts.get(input.sessionId) ?? 0) + 1;
      attempts.set(input.sessionId, n);
      for (let stage = 0; stage < 4; stage++) {
        await wait(STAGE_MS, signal);
        if (scenario === "network-retry" && n === 1 && stage === 1)
          throw new ResearchError("network", "Network error");
        if (scenario === "timeout" && stage === 2) {
          await wait(10 * 60_000, signal); // never finishes; the 170 s timeout fires
        }
        onEvent({ type: "stage_done", stage });
      }
      await wait(500, signal);
      return buildMockResult(input, scenario, Date.now());
    },
  };
}
