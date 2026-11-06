/**
 * The awkward result states, from the data to the card: ties, old prices, no offer and disputed values. The
 * shared example goes through the real adapter, so what is shown is what the contract carries.
 */
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import { ResultCard, TooCloseToCall } from "@/components/results/ResultCard";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import { buildMockResult } from "@/lib/results/mockService";
import type { Offer, ProductResult, RubricItem } from "@/lib/results/types";
import { formatCheckTime, isPriceStale, ordinal, tieGroups, tiesFor } from "@/lib/results/view";
import { listInWords } from "@/lib/text";
import type { SearchResult } from "@/types/contracts/SearchResult";

const example = searchExample as unknown as SearchResult;
const T0 = Date.parse(example.started_at);
const rubric: RubricItem[] = [{ criterionId: "battery_life", title: "Battery Life!", weight: 1 }];
const mockInput = {
  sessionId: "s",
  category: "laptop",
  rubric: [
    { criterionId: "cpu", title: "CPU performance", weight: 3 },
    { criterionId: "ram", title: "Memory", weight: 2 },
    { criterionId: "battery", title: "Battery life", weight: 1 },
  ],
  mustHaves: [],
  exclusions: [],
};

/** The component's HTML in a detached element: these tests read what it says, not how it behaves. */
function show(ui: ReactElement): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(ui);
  return host;
}

function card(p: ProductResult, products: ProductResult[] = [p], featured = false) {
  return show(
    <ResultCard product={p} tiedWith={tiesFor(products, p)} featured={featured} index={0} />,
  );
}

describe("tied products", () => {
  it("keeps the separating criterion, under its title exactly as given", () => {
    const r = resultFromSearch(example, rubric, T0);
    const [nova, kestrel, orion] = r.products;
    expect(nova!.separatingCriterion).toBe("Battery Life!");
    expect(kestrel!.separatingCriterion).toBe("Battery Life!");
    expect(orion!.separatingCriterion).toBeNull();
  });

  it("falls back to the key in words when the rubric has no title for it", () => {
    const r = resultFromSearch(example, [], T0);
    expect(r.products[0]!.separatingCriterion).toBe("Battery life");
  });

  it("groups the example's tied products once, by the rank they share", () => {
    const { products } = resultFromSearch(example, rubric, T0);
    const groups = tieGroups(products);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ rank: 1, criteria: ["Battery Life!"] });
    expect(groups[0]!.products.map((p) => p.name)).toEqual(["Nova Book 14", "Kestrel Slim 15"]);
  });

  it("finds no tie when every rank is different", () => {
    expect(tieGroups(buildMockResult(mockInput, "success").products)).toEqual([]);
  });

  it("lists every criterion the tied products name, and none twice", () => {
    const [a, b, c] = buildMockResult(mockInput, "success").products;
    const group = [
      { ...a!, rank: 2, separatingCriterion: "Weight" },
      { ...b!, rank: 2, separatingCriterion: "Battery life" },
      { ...c!, rank: 2, separatingCriterion: "Weight" },
    ];
    expect(tieGroups(group)[0]!.criteria).toEqual(["Weight", "Battery life"]);
  });

  it("the one card names the products and the criterion", () => {
    const { products } = resultFromSearch(example, rubric, T0);
    const tie = show(<TooCloseToCall tie={tieGroups(products)[0]!} />);
    expect(tie.querySelector("section")).toHaveAttribute("aria-label", "Too close to call");
    expect(tie.querySelector("h3")).toHaveTextContent("Too close to call");
    expect(tie).toHaveTextContent("Nova Book 14 and Kestrel Slim 15 are tied for 1st.");
    expect(tie).toHaveTextContent("What separates them: Battery Life!");
  });

  it("the card says so when the backend names no criterion, and invents none", () => {
    const { products } = resultFromSearch(example, rubric, T0);
    const unnamed = products.map((p) => ({ ...p, separatingCriterion: null }));
    const tie = show(<TooCloseToCall tie={tieGroups(unnamed)[0]!} />);
    expect(tie).toHaveTextContent("We can't say yet what separates them.");
    expect(tie).not.toHaveTextContent("What separates them:");
  });

  it('a tied product reads "Tied for 1st", never "#=1" or "Best match"', () => {
    const { products } = resultFromSearch(example, rubric, T0);
    const shown = card(products[0]!, products, true);
    expect(shown).toHaveTextContent("Tied for 1st");
    expect(shown).toHaveTextContent("Tied with Kestrel Slim 15");
    expect(shown).not.toHaveTextContent("#");
    expect(shown).not.toHaveTextContent("Best match");
  });

  it("a product that is not tied keeps its plain rank and the best match label", () => {
    const { products } = buildMockResult(mockInput, "success");
    const shown = card(products[0]!, products, true);
    expect(shown).toHaveTextContent("#1");
    expect(shown).toHaveTextContent("Best match");
    expect(shown).not.toHaveTextContent("Tied");
  });

  it("the mock's ties work the same way, at second place and at first", () => {
    const second = tieGroups(buildMockResult(mockInput, "tie").products);
    expect(second).toHaveLength(1);
    expect(second[0]).toMatchObject({ rank: 2, criteria: ["CPU performance"] });
    const first = tieGroups(buildMockResult(mockInput, "tie-first").products);
    expect(first).toHaveLength(1);
    expect(first[0]).toMatchObject({ rank: 1, criteria: ["CPU performance"] });
  });

  it("writes ranks and lists the way people say them", () => {
    const ranks = [1, 2, 3, 4, 10, 11, 12, 13, 21, 22, 23, 101, 111].map(ordinal);
    expect(ranks).toEqual([
      "1st",
      "2nd",
      "3rd",
      "4th",
      "10th",
      "11th",
      "12th",
      "13th",
      "21st",
      "22nd",
      "23rd",
      "101st",
      "111th",
    ]);
    expect(listInWords(["A"])).toBe("A");
    expect(listInWords(["A", "B"])).toBe("A and B");
    expect(listInWords(["A", "B", "C"])).toBe("A, B and C");
  });
});

describe("old prices", () => {
  const NOW = Date.parse("2026-11-06T12:00:00Z");
  const MINUTE = 60_000;
  const ago = (minutes: number) => new Date(NOW - minutes * MINUTE).toISOString();
  const offer = (o: Partial<Offer>): Offer => ({
    store: "Example Store",
    price: 64990,
    checkedAt: ago(60),
    stale: false,
    url: null,
    ...o,
  });
  const withOffer = (o: Offer): ProductResult => ({
    ...buildMockResult(mockInput, "success").products[0]!,
    offers: [o],
  });

  it("23 hours 59 minutes is not stale", () => {
    expect(isPriceStale(ago(23 * 60 + 59), NOW)).toBe(false);
  });

  it("exactly 24 hours is not stale, the same as the backend", () => {
    expect(isPriceStale(ago(24 * 60), NOW)).toBe(false);
  });

  it("24 hours 1 minute is stale", () => {
    expect(isPriceStale(ago(24 * 60 + 1), NOW)).toBe(true);
  });

  it("a missing or broken as-of time is neither stale nor fresh", () => {
    expect(isPriceStale(null, NOW)).toBeNull();
    expect(isPriceStale(undefined, NOW)).toBeNull();
    expect(isPriceStale("", NOW)).toBeNull();
    expect(isPriceStale("not a time", NOW)).toBeNull();
  });

  it("a time zone in the as-of time is respected", () => {
    expect(isPriceStale("2026-11-05T17:30:00+05:30", NOW)).toBe(false);
    expect(isPriceStale("2026-11-05T17:29:00+05:30", NOW)).toBe(true);
  });

  it("the adapter uses the same rule on the contract's as_of", () => {
    const fresh = resultFromSearch(example, rubric, T0).products;
    expect(fresh[0]!.offers[0]!.stale).toBe(false);
    expect(fresh[1]!.offers[0]!.stale).toBe(true);
    const later = resultFromSearch(example, rubric, T0 + 48 * 60 * MINUTE).products;
    expect(later[0]!.offers[0]!.stale).toBe(true);
  });

  it("a mock price is old because of its time, not a flag", () => {
    const { products } = buildMockResult(mockInput, "stale", NOW);
    const [a, b, c] = products;
    expect(a!.offers.map((o) => o.stale)).toEqual([false, true]);
    expect(b!.offers.map((o) => o.stale)).toEqual([true, true]);
    expect(c!.offers.map((o) => o.stale)).toEqual([false, null]);
    expect(isPriceStale(a!.offers[1]!.checkedAt, NOW)).toBe(true);
  });

  it("the mock's fresh prices stay fresh on any day it runs", () => {
    for (const now of [NOW, NOW + 400 * 24 * 60 * MINUTE]) {
      const { products } = buildMockResult(mockInput, "success", now);
      expect(products.flatMap((p) => p.offers).every((o) => o.stale === false)).toBe(true);
    }
  });

  it("a stale offer shows its as-of time and says the price may have changed", () => {
    const checkedAt = ago(3 * 24 * 60);
    const shown = card(withOffer(offer({ checkedAt, stale: isPriceStale(checkedAt, NOW) })));
    expect(shown).toHaveTextContent(`Price checked ${formatCheckTime(checkedAt)}`);
    expect(shown).toHaveTextContent("Price may have changed");
    expect(shown).not.toHaveTextContent("Old price");
  });

  it("a fresh offer shows its as-of time and no warning", () => {
    const shown = card(withOffer(offer({})));
    expect(shown).toHaveTextContent(`Price checked ${formatCheckTime(ago(60))}`);
    expect(shown).not.toHaveTextContent("Price may have changed");
  });

  it("an offer with no as-of time says so, with no warning and no time", () => {
    const shown = card(withOffer(offer({ checkedAt: null, stale: isPriceStale(null, NOW) })));
    expect(shown).toHaveTextContent("We don't know when this price was checked");
    expect(shown).not.toHaveTextContent("Price may have changed");
    expect(shown).not.toHaveTextContent("Price checked");
  });
});

describe("no offer", () => {
  const link = "https://www.google.com/search?tbm=shop&q=Orion+Play+15";
  const orion = () => resultFromSearch(example, rubric, T0).products[2]!;

  it("keeps the store search the contract carries for a product with no offer", () => {
    const [nova, kestrel, third] = resultFromSearch(example, rubric, T0).products;
    expect(third!.offers).toEqual([]);
    expect(third!.storeSearchUrl).toBe(example.commerce!.no_offer["prd_orion_play_15"]);
    expect(third!.storeSearchUrl).toBe(link);
    expect(nova!.storeSearchUrl).toBeNull();
    expect(kestrel!.storeSearchUrl).toBeNull();
  });

  it("has no link when the result has no commerce stage at all", () => {
    const none = resultFromSearch({ ...example, commerce: null }, rubric, T0);
    expect(none.products.every((p) => p.storeSearchUrl === null)).toBe(true);
  });

  it("the card says so and links to the store search in a new tab", () => {
    const shown = card(orion());
    expect(shown).toHaveTextContent("No price found yet");
    const search = shown.querySelector<HTMLAnchorElement>(`a[href="${link}"]`);
    expect(search).toHaveTextContent("Search stores");
    expect(search).toHaveTextContent("for Orion Play 15 (opens in a new tab)");
    expect(search).toHaveAttribute("target", "_blank");
    expect(search).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("with no link for the product, the card shows none and invents none", () => {
    const shown = card({ ...orion(), storeSearchUrl: null });
    expect(shown).toHaveTextContent("No price found yet");
    expect(shown.querySelector("a")).toBeNull();
  });

  it("never links to anything but a web address", () => {
    const shown = card({ ...orion(), storeSearchUrl: "javascript:alert(1)" });
    expect(shown.querySelector("a")).toBeNull();
  });

  it("a product with offers shows them and no store search", () => {
    const shown = card({
      ...resultFromSearch(example, rubric, T0).products[0]!,
      storeSearchUrl: link,
    });
    expect(shown).not.toHaveTextContent("No price found yet");
    expect(shown.querySelector(`a[href="${link}"]`)).toBeNull();
  });

  it("an offer with no price says the same thing", () => {
    const { products } = buildMockResult(mockInput, "success");
    const noPrice = { ...products[0]!.offers[0]!, price: null };
    expect(card({ ...products[0]!, offers: [noPrice] })).toHaveTextContent("No price found yet");
  });

  it("the mock's no-offer scenario carries a store search too", () => {
    const b = buildMockResult(mockInput, "no-offer").products[1]!;
    expect(b.offers).toEqual([]);
    expect(b.storeSearchUrl).toBe("https://example.com/store-search?q=Sample%20laptop%20B");
  });
});
