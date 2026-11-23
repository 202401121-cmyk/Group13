/**
 * The mock research results: made-up products, values, claims and offers for each preview scenario. Nothing is
 * searched, and every result is marked source "mock". Only a finished mock search needs them, so the mock service
 * fetches this file when a search starts and no page loads it first (D-45 first-load budget).
 */
import { mockRefilter, mockRerank } from "./mockRerank";
import type {
  ClaimView,
  CriterionValue,
  Offer,
  ProductResult,
  ReasonView,
  ResearchInput,
  ResearchResult,
  RubricItem,
} from "./types";
import { formatINR } from "@/lib/request/format";
import { WITHHELD, isPriceStale, newestPriceTime, shortDay, sourceDateLabel } from "./view";

/** The clock for a fixture built with no time given, so the same input always gives the same result. */
const FIXTURE_NOW = Date.parse("2026-10-27T11:40:00Z");
const HOUR_MS = 60 * 60 * 1000;

/** A mock price checked this many hours before `now`; the 24-hour rule then decides if it is old. */
const hoursAgo = (hours: number, now: number) => new Date(now - hours * HOUR_MS).toISOString();

function checkedAt(offer: Offer, time: string | null, now: number): Offer {
  return { ...offer, checkedAt: time, stale: isPriceStale(time, now) };
}

const DAY_MS = 24 * HOUR_MS;

/** Made-up review sites for the preview. Every link goes to example.com, never a real review. */
const REVIEW_SITES = [
  { slug: "sample-tech-reviews", name: "Sample Tech Reviews" },
  { slug: "sample-review-lab", name: "Sample Review Lab" },
] as const;

const slug = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

interface MockSource {
  key: string;
  name: string;
  typeLabel: string;
  url: string;
  /** Days before `now` it was published, or null when only the day it was read is known. */
  publishedDaysAgo: number | null;
}

function mockClaim(
  id: string,
  side: ClaimView["side"],
  source: MockSource,
  quote: string | null,
  valueText: string | null,
  now: number,
): ClaimView {
  const day = (days: number) => new Date(now - days * DAY_MS).toISOString();
  return {
    claimId: `clm_mock_${id}`,
    side,
    quote,
    quoteWithheld:
      quote === null ? (source.typeLabel === "Reddit" ? WITHHELD.reddit : WITHHELD.other) : null,
    source: {
      id: `src_mock_${source.key}`,
      name: source.name,
      typeLabel: source.typeLabel,
      url: source.url,
      dateLabel: sourceDateLabel(
        source.publishedDaysAgo === null ? null : day(source.publishedDaysAgo),
        day(1),
      ),
    },
    valueText,
  };
}

/** A known value rests on one or two made-up review sites, each quoting the same rating. */
function reviewClaims(
  name: string,
  criterion: RubricItem,
  rating: number,
  count: number,
  now: number,
): ClaimView[] {
  return REVIEW_SITES.slice(0, count).map((site, k) =>
    mockClaim(
      `${slug(name)}_${slug(criterion.criterionId)}_${k}`,
      "supporting",
      {
        key: `${site.slug}_${slug(name)}`,
        name: site.name,
        typeLabel: "Review site",
        url: `https://example.com/reviews/${site.slug}/${slug(name)}`,
        publishedDaysAgo: 6 + k * 9,
      },
      `${name} scores ${rating} out of 10 for ${criterion.title} in our tests.`,
      `${rating} / 10`,
      now,
    ),
  );
}

function values(rubric: RubricItem[], seed: number, name: string, now: number): CriterionValue[] {
  return rubric.map((r, i) => {
    const rating = ((seed + i * 3) % 4) + 6;
    return {
      criterionId: r.criterionId,
      label: r.title,
      state: "verified" as const,
      value: `Sample rating ${rating} / 10`,
      claims: reviewClaims(name, r, rating, (seed + i) % 2 === 0 ? 2 : 1, now),
    };
  });
}

/** The three criteria that added the most points, with each one's state and claims, as live results give them. */
function reasonsOf(p: ProductResult): ReasonView[] {
  return [...(p.contributions ?? [])]
    .sort((x, y) => y.points - x.points)
    .slice(0, 3)
    .flatMap((c) => {
      const row = p.criteria.find((r) => r.criterionId === c.criterionId);
      if (!row) return [];
      return [
        {
          criterionId: c.criterionId,
          label: c.label,
          state: row.state,
          // An unknown value is never shown as 0 points.
          points: row.state === "unknown" ? null : c.points,
          claims: row.claims ?? [],
        },
      ];
    });
}

function product(
  letter: string,
  category: string,
  rank: number,
  score: number,
  rubric: RubricItem[],
  now: number,
): ProductResult {
  const criteria = values(rubric, rank, `Sample ${category} ${letter}`, now);
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
    dataCoverage: rank === 1 ? 92 : rank === 2 ? 84 : 76,
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
    ].map((o, i) =>
      checkedAt(
        {
          ...o,
          checkedAt: null,
          stale: null,
          availability: i === 0 ? "in_stock" : "unknown",
          overBudget: false,
        },
        hoursAgo(2, now),
        now,
      ),
    ),
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
    pricesCheckedAt: null, // from the priced offers, below
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
  /** A's second value is disputed: with both sides' sources, or (as a result with no citations) with none. */
  const dispute = (withSources: boolean) => {
    const row = a.criteria[1];
    if (row) {
      const id = `${slug(a.name)}_${slug(row.criterionId)}`;
      a.criteria[1] = {
        ...row,
        state: "disputed",
        value: null,
        ...(withSources
          ? {
              sources: [
                { source: "Sample Store One listing", value: "16 GB" },
                { source: "Sample maker spec sheet", value: "8 GB" },
              ],
              claims: [
                mockClaim(
                  `${id}_store`,
                  "supporting",
                  {
                    key: "sample-store-one",
                    name: "Sample Store One listing",
                    typeLabel: "Store",
                    url: "https://example.com/sample-store-one/listing",
                    publishedDaysAgo: null,
                  },
                  "Comes with 16 GB of memory.",
                  "16 GB",
                  now,
                ),
                mockClaim(
                  `${id}_maker`,
                  "conflicting",
                  {
                    key: "sample-maker",
                    name: "Sample maker spec sheet",
                    typeLabel: "Maker's site",
                    url: "https://example.com/sample-maker/spec-sheet",
                    publishedDaysAgo: 30,
                  },
                  "Memory: 8 GB, which can be upgraded later.",
                  "8 GB",
                  now,
                ),
              ],
            }
          : { claims: [] }),
      };
    }
    a.confidence = "medium";
  };
  switch (scenario) {
    case "tie":
      tie(b, c, 2, 79);
      break;
    case "tie-three":
      a.rank = b.rank = c.rank = 1;
      a.score = b.score = c.score = 80;
      a.separatingCriterion = b.separatingCriterion = c.separatingCriterion = r[0]?.title ?? null;
      break;
    case "over-budget":
      a.offers[0] = { ...a.offers[0]!, overBudget: true };
      b.offers[0] = { ...b.offers[0]!, availability: "out_of_stock" };
      break;
    case "offer-24h":
      // Exactly 24 hours is not old (the backend's rule); a minute more is.
      a.offers[0] = checkedAt(a.offers[0]!, hoursAgo(24, now), now);
      a.offers[1] = checkedAt(a.offers[1]!, new Date(now - DAY_MS - 60_000).toISOString(), now);
      break;
    case "llm-unavailable":
      // Every LLM provider failed (US-31): nothing could be read, so nothing is ranked.
      res.products = [];
      res.partial = true;
      res.llmUnavailable = true;
      res.coverageNote =
        "The AI services we use were all unavailable, so no source could be read. Please try again later.";
      break;
    case "tie-first":
      tie(a, b, 1, 84);
      break;
    case "disputed":
      dispute(true);
      break;
    case "disputed-no-sources":
      // A result that carries the state but no citations: there are no claims to show.
      dispute(false);
      break;
    case "unknown":
      for (const p of products) {
        const last = p.criteria.at(-1);
        if (last) Object.assign(last, { state: "unknown", value: null, claims: [] });
        if (p.coverage) p.coverage = { ...p.coverage, checked: p.coverage.total - 1 };
      }
      break;
    case "estimated":
      for (const p of [a, b]) {
        if (p.criteria[2])
          Object.assign(p.criteria[2], {
            state: "estimated",
            value: "About 9 hours (sample estimate)",
            claims: [],
          });
      }
      a.dataCoverage = 62;
      b.dataCoverage = null; // a result with no figure: the screen must say so, never show 0%
      break;
    case "stale":
      a.offers[1] = checkedAt(a.offers[1]!, hoursAgo(72, now), now);
      b.offers = b.offers.map((o) => checkedAt(o, hoursAgo(72, now), now));
      c.offers[1] = checkedAt(c.offers[1]!, null, now);
      break;
    case "not-ranked":
      // Nothing could be scored, as in S3-19's live walk: no order, no prices, and the note says why.
      for (const p of products) {
        Object.assign(p, { score: null, chanceFirst: null, contributions: [], confidence: "low" });
        Object.assign(p, { dataCoverage: 0, value: null });
        p.explanation = `We found ${p.name} in the sources, but couldn't check what matters to you.`;
        p.criteria = p.criteria.map((c) => ({ ...c, state: "unknown", value: null, claims: [] }));
        p.coverage = { checked: 0, total: p.criteria.length };
        p.offers = [];
        p.storeSearchUrl = `https://example.com/store-search?q=${encodeURIComponent(p.name)}`;
      }
      res.partial = true;
      res.coverageNote =
        "Not ranked: we found no scores for your criteria. 8 sources were not read, to keep the search from " +
        "running too long. 18 Reddit threads were not read, to leave room for web pages.";
      break;
    case "sourced-price": {
      // No store offered B, but a review gave its price: shown apart from offers, never as a buy link.
      b.offers = [];
      b.storeSearchUrl = `https://example.com/store-search?q=${encodeURIComponent(b.name)}`;
      const price = `₹${formatINR(71990)}`;
      b.sourcedPrice = {
        text: price,
        site: "Sample Review Lab",
        seen: shortDay(new Date(now - DAY_MS).toISOString()),
        state: "verified",
        claims: [
          mockClaim(
            `${slug(b.name)}_price`,
            "supporting",
            {
              key: `sample-review-lab_${slug(b.name)}`,
              name: "Sample Review Lab",
              typeLabel: "Review site",
              url: `https://example.com/reviews/sample-review-lab/${slug(b.name)}`,
              publishedDaysAgo: null,
            },
            `${b.name} sells for about ${price}.`,
            price,
            now,
          ),
        ],
      };
      break;
    }
    case "no-offer":
      b.offers = [];
      b.storeSearchUrl = `https://example.com/store-search?q=${encodeURIComponent(b.name)}`;
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
        {
          name: `Sample ${cat} F`,
          reason: "Store pages didn't list its specs.",
          criterion: r[0]?.title ?? null,
        },
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
    case "refilter": {
      // US-23: products held back, each for a reason a change can lift, or not (mockRerank.ts). Each card is as the
      // live adapter makes one for a held product: values and offers, at the price the filter read; no score.
      const heldCard = (letter: string, rank: number, price: number): ProductResult => {
        const p = product(letter, cat, rank, 0, r, now);
        return {
          id: p.id,
          name: p.name,
          imageUrl: null,
          rank: null,
          score: null,
          criteria: p.criteria,
          offers: p.offers.map((o, i) => ({ ...o, price: price + i * 1500 })),
          value: null,
        };
      };
      const d = heldCard("D", 4, 84990);
      const e = heldCard("E", 5, 69990);
      const f = heldCard("F", 6, 70990);
      const held = [
        {
          product: d,
          price: 84990,
          ram: 16,
          group: "filtered_out" as const,
          reason: "₹84,990 is above your ₹80,000 budget",
        },
        {
          product: e,
          price: 69990,
          ram: null,
          group: "cant_tell" as const,
          reason: `We couldn't find its RAM, so we can't check "at least 8 GB RAM"`,
        },
        {
          product: f,
          price: 70990,
          ram: 16,
          group: "filtered_out" as const,
          reason: "You ruled out Kestrel",
        },
      ];
      res.leftOut = [held[0], held[2]].map((h) => ({ name: h!.product.name, reason: h!.reason }));
      res.couldntCheck = [{ name: e.name, reason: held[1]!.reason, criterion: "RAM" }];
      res.rerank = mockRefilter(products, held, r);
      break;
    }
    case "many":
      // US-23: fifty products, to re-rank on a slow CPU.
      res.products = Array.from({ length: 50 }, (_, i) =>
        product(String(i + 1).padStart(2, "0"), cat, i + 1, 75 - i / 2, r, now),
      );
      break;
    case "withheld-quote": {
      // A Reddit thread backs A's top value too, but Reddit's words aren't kept in saved results (D-53).
      const row = a.criteria[0];
      if (row) {
        row.claims = [
          ...(row.claims ?? []),
          mockClaim(
            `${slug(a.name)}_${slug(row.criterionId)}_reddit`,
            "supporting",
            {
              key: "reddit-sample-shoppers",
              name: "r/SampleShoppers",
              typeLabel: "Reddit",
              url: "https://www.reddit.com/r/SampleShoppers/comments/sample1/",
              publishedDaysAgo: 12,
            },
            null,
            null,
            now,
          ),
        ];
      }
      break;
    }
  }
  // As a live result names them: the top product's lead, and each product's main reasons.
  const leader = res.products[0]?.score ?? null;
  for (const p of res.products) {
    // The second reason's score was compared with the other products found, as a live one can be (US-18).
    // An unscored product has no reasons it ranks (as live: decide's contributions are all unknown then).
    p.reasons = (p.score == null ? [] : reasonsOf(p)).map((reason, i) => ({
      ...reason,
      basis: i === 1 ? "compared" : "opinion",
    }));
    const weakest = [...(p.contributions ?? [])].sort((x, y) => x.points - y.points)[0];
    p.tradeOff = weakest ? `The weakest of what you weighted is ${weakest.label}.` : null;
    p.unverified = p.criteria.filter((c) => c.state === "unknown").map((c) => c.label);
    p.explanationMethod = "llm_checked";
    // The sample explanation rests on the same claims as the product's top reason.
    p.explanationParts = p.explanation
      ? [{ text: p.explanation, claims: p.reasons[0]?.claims ?? [] }]
      : [];
    // Unscored products have no label, as decide gives none.
    p.label =
      p.score == null ? null : p.rank === 1 ? "best_match" : p.rank === 2 ? "runner_up" : null;
    p.gapToLeader =
      leader === null || p.score == null ? null : Math.round((leader - p.score) * 10) / 10;
  }
  // As live: only the shown offers with a price say when prices were checked.
  res.pricesCheckedAt = newestPriceTime(res.products);
  // US-23: what the browser re-ranks and re-filters from, unless the scenario set its own.
  if (res.rerank === undefined) res.rerank = mockRerank(res.products, r);
  return res;
}
