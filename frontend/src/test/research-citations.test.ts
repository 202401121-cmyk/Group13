/**
 * US-21: every value on the results page keeps where it came from. The shared example goes through the real
 * adapter: a value's claims are its citations joined to its sources, and nothing is added that the result
 * doesn't carry.
 */
import { describe, expect, it } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import { WITHHELD, resultFromSearch } from "@/lib/results/liveAdapter";
import type { ProductResult, RubricItem } from "@/lib/results/types";
import type { SearchResult } from "@/types/contracts/SearchResult";

const example = searchExample as unknown as SearchResult;
const T0 = Date.parse(example.started_at);
const rubric: RubricItem[] = [
  { criterionId: "cpu_performance", title: "CPU performance", weight: 9 },
];

/** A copy of the example with one change, so each test says exactly what it alters. */
function changed(edit: (r: SearchResult) => void): SearchResult {
  const copy = structuredClone(example);
  edit(copy);
  return copy;
}

const productOf = (r: { products: ProductResult[] }, name: string) =>
  r.products.find((p) => p.name === name)!;
const rowOf = (r: { products: ProductResult[] }, name: string, key: string) =>
  productOf(r, name).criteria.find((c) => c.criterionId === key)!;

const REVIEW = {
  id: "src_37c43827cfe41e89",
  name: "example-reviews.in",
  typeLabel: "Review site",
  url: "https://example-reviews.in/kestrel-slim-15-review",
  dateLabel: "Checked 3 Oct 2026",
};
const MAKER = {
  id: "src_2958fd9d1b3643cf",
  name: "kestrel.example.com",
  typeLabel: "Maker's site",
  url: "https://kestrel.example.com/slim-15/specs",
  dateLabel: "Checked 3 Oct 2026",
};
const REDDIT = {
  id: "src_edb2d0c42af72797",
  name: "r/developersIndia",
  typeLabel: "Reddit",
  url: "https://www.reddit.com/r/developersIndia/comments/abc123/",
  dateLabel: "Checked 3 Oct 2026",
};

describe("claims behind each value", () => {
  const r = resultFromSearch(example, rubric, T0);

  it("a known value carries its supporting claim, with the exact quote and its source", () => {
    expect(rowOf(r, "Nova Book 14", "cpu_performance").claims).toEqual([
      {
        claimId: "clm_053f4bf83174cf53",
        side: "supporting",
        quote: "Nova Book 14: cpu performance rated 7.5/10.",
        quoteWithheld: null,
        source: REVIEW,
      },
    ]);
  });

  it("a disputed value carries both sides: supporting first, then what disagrees", () => {
    const row = rowOf(r, "Kestrel Slim 15", "battery_life");
    expect(row.state).toBe("disputed");
    expect(row.claims).toEqual([
      {
        claimId: "clm_2c31271c5c981d18",
        side: "supporting",
        quote: "battery rated up to 10 hours",
        quoteWithheld: null,
        source: MAKER,
      },
      {
        claimId: "clm_46feb890769b8225",
        side: "conflicting",
        quote: "I get about 6 hours, not 10",
        quoteWithheld: null,
        source: REDDIT,
      },
    ]);
  });

  it("an estimated value has no claims: no source gave it", () => {
    const row = rowOf(r, "Orion Play 15", "build_durability");
    expect(row.state).toBe("estimated");
    expect(row.claims).toEqual([]);
  });

  it("an unknown value has no claims, even if the result lists claim IDs for it", () => {
    const odd = changed((x) => {
      const v = x.products[0]!.values.find((v) => v.criterion_key === "display_quality")!;
      Object.assign(v, { state: "unknown", score: null, value: null });
    });
    const row = rowOf(resultFromSearch(odd, rubric, T0), "Nova Book 14", "display_quality");
    expect(row.state).toBe("unknown");
    expect(row.claims).toEqual([]);
  });

  it("a criterion with no value at all is unknown with no claims", () => {
    const thin = changed((x) => {
      x.products[0]!.values = [];
    });
    const rows = productOf(resultFromSearch(thin, rubric, T0), "Nova Book 14").criteria;
    expect(rows.every((c) => c.state === "unknown" && c.claims?.length === 0)).toBe(true);
  });

  it("a Reddit quote that wasn't kept says why, and keeps the link", () => {
    const saved = changed((x) => {
      x.citations.find((c) => c.claim_id === "clm_46feb890769b8225")!.quote = null;
    });
    const claim = rowOf(resultFromSearch(saved, rubric, T0), "Kestrel Slim 15", "battery_life")
      .claims![1]!;
    expect(claim.quote).toBeNull();
    expect(claim.quoteWithheld).toBe(WITHHELD.reddit);
    expect(claim.quoteWithheld).toMatch(/Reddit/);
    expect(claim.source.url).toBe(REDDIT.url);
  });

  it("any other quote that wasn't kept says so plainly", () => {
    const saved = changed((x) => {
      x.citations.find((c) => c.claim_id === "clm_053f4bf83174cf53")!.quote = null;
    });
    const claim = rowOf(resultFromSearch(saved, rubric, T0), "Nova Book 14", "cpu_performance")
      .claims![0]!;
    expect(claim.quote).toBeNull();
    expect(claim.quoteWithheld).toBe(WITHHELD.other);
  });

  it("leaves out a claim with no citation, or whose source is missing, and never invents one", () => {
    const gaps = changed((x) => {
      x.citations = x.citations.filter((c) => c.claim_id !== "clm_053f4bf83174cf53");
      x.citations.find((c) => c.claim_id === "clm_46feb890769b8225")!.source_id = "src_missing";
    });
    const out = resultFromSearch(gaps, rubric, T0);
    expect(rowOf(out, "Nova Book 14", "cpu_performance").claims).toEqual([]);
    expect(rowOf(out, "Kestrel Slim 15", "battery_life").claims!.map((c) => c.claimId)).toEqual([
      "clm_2c31271c5c981d18",
    ]);
  });

  it("names a source by its site, then its community, then its title", () => {
    const titled = changed((x) => {
      const review = x.sources.find((s) => s.id === REVIEW.id)!;
      review.site = null;
      review.published_at = "2026-10-01T06:00:00Z";
    });
    const source = rowOf(resultFromSearch(titled, rubric, T0), "Nova Book 14", "cpu_performance")
      .claims![0]!.source;
    expect(source.name).toBe("Kestrel Slim 15 review");
    expect(source.dateLabel).toBe("Published 1 Oct 2026");
  });
});

describe("the explanation's sentences", () => {
  const r = resultFromSearch(example, rubric, T0);

  it("each sentence keeps the claims it rests on, with their sources", () => {
    const nova = productOf(r, "Nova Book 14");
    expect(nova.explanationParts).toEqual([
      {
        text: "Its keyboard is the most comfortable of the three, which matters most for long coding.",
        claims: [
          {
            claimId: "clm_5807c6297c20c05e",
            side: "supporting",
            quote: "Nova Book 14: keyboard comfort rated 9.0/10.",
            quoteWithheld: null,
            source: REDDIT,
          },
        ],
      },
      {
        text: "Battery life is strong, so you can work a full day away from a plug.",
        claims: [
          {
            claimId: "clm_bf7c2488fe2eda5f",
            side: "supporting",
            quote: "Nova Book 14: battery life rated 8.0/10.",
            quoteWithheld: null,
            source: REVIEW,
          },
        ],
      },
      // The contract gives the trade-off no claim IDs, so it has no sources to show.
      { text: "The Orion Play 15 compiles faster, but it is much heavier.", claims: [] },
    ]);
    // The joined text stays for anything that still reads it.
    expect(nova.explanation).toBe(nova.explanationParts!.map((p) => p.text).join(" "));
  });

  it("a sentence with two claims keeps both, in order", () => {
    const kestrel = productOf(r, "Kestrel Slim 15");
    expect(kestrel.explanationParts![0]!.claims.map((c) => c.claimId)).toEqual([
      "clm_8fe73a640ab049ef",
      "clm_dc147cd38e1e6839",
    ]);
  });

  it("leaves out a claim ID with no citation or source", () => {
    const odd = changed((x) => {
      x.explanations[0]!.sentences[0]!.claim_ids = ["clm_unknown", "clm_5807c6297c20c05e"];
      x.explanations[0]!.sentences[1]!.claim_ids = ["clm_unknown"];
    });
    const nova = productOf(resultFromSearch(odd, rubric, T0), "Nova Book 14");
    expect(nova.explanationParts![0]!.claims.map((c) => c.claimId)).toEqual([
      "clm_5807c6297c20c05e",
    ]);
    expect(nova.explanationParts![1]!.claims).toEqual([]);
  });

  it("a product with no explanation has no parts", () => {
    expect(productOf(r, "Orion Play 15").explanationParts).toEqual([]);
  });
});

describe("main reasons", () => {
  const r = resultFromSearch(example, rubric, T0);

  it("are the three criteria that added the most points, largest first", () => {
    const nova = productOf(r, "Nova Book 14");
    expect(nova.reasons!.map((x) => [x.criterionId, x.points])).toEqual([
      ["keyboard_comfort", 16.4],
      ["cpu_performance", 15.3],
      ["battery_life", 12.7],
    ]);
    expect(nova.reasons![1]).toMatchObject({ label: "CPU performance", state: "verified" });
    expect(nova.reasons![1]!.claims.map((c) => c.claimId)).toEqual(["clm_053f4bf83174cf53"]);
  });

  it("carry a disputed value's state and both sides", () => {
    const kestrel = productOf(r, "Kestrel Slim 15");
    const battery = kestrel.reasons!.find((x) => x.criterionId === "battery_life")!;
    expect(battery.state).toBe("disputed");
    expect(battery.claims.map((c) => c.side)).toEqual(["supporting", "conflicting"]);
  });

  it("never show an unknown value as 0 points", () => {
    const unknown = changed((x) => {
      x.decision!.ranked[0]!.contributions = [
        {
          criterion_key: "keyboard_comfort",
          score: null,
          normalized_weight: 0.18,
          contribution: 0,
          state: "unknown",
        },
        {
          criterion_key: "cpu_performance",
          score: 7.5,
          normalized_weight: 0.2,
          contribution: 1.5341,
          state: "known",
        },
      ];
    });
    const nova = productOf(resultFromSearch(unknown, rubric, T0), "Nova Book 14");
    expect(nova.reasons!.map((x) => [x.criterionId, x.state, x.points])).toEqual([
      ["cpu_performance", "verified", 15.3],
      ["keyboard_comfort", "unknown", null],
    ]);
  });

  it("the result keeps its search ID, so the evidence route can be asked", () => {
    expect(r.searchId).toBe(example.search_id);
  });
});
