/**
 * The awkward result states, from the data to the card: ties, old prices, no offer and disputed values. The
 * shared example goes through the real adapter, so what is shown is what the contract carries.
 */
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import { NotSavedNotice } from "@/components/results/primitives";
import { CouldntCheckItem, ResultCard, TooCloseToCall } from "@/components/results/ResultCard";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import { buildMockResult } from "@/lib/results/mockService";
import type { Offer, ProductResult, RubricItem } from "@/lib/results/types";
import {
  coverageText,
  formatCheckTime,
  giveFocusBack,
  isPriceStale,
  ordinal,
  tieGroups,
  tiesFor,
} from "@/lib/results/view";
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

describe("disputed values", () => {
  const row = (shown: HTMLElement) =>
    [...shown.querySelectorAll("li")].find((li) => li.textContent?.includes("Sources disagree"));

  it("in live mode says the sources disagree and shows no value and no source", () => {
    const kestrel = resultFromSearch(example, rubric, T0).products[1]!;
    expect(kestrel.criteria.find((c) => c.state === "disputed")?.sources).toBeUndefined();
    // The row's whole text: the criterion, the badge and the note, with nothing made up beside them.
    expect(row(card(kestrel))?.textContent).toBe(
      "Battery Life!Sources disagreeSource details aren't available yet.",
    );
  });

  it("in mock mode keeps both values, each with its source", () => {
    const a = buildMockResult(mockInput, "disputed").products[0]!;
    const shown = row(card(a));
    expect(shown).toHaveTextContent("Sample Store One listing: 16 GB");
    expect(shown).toHaveTextContent("Sample maker spec sheet: 8 GB");
    expect(shown).not.toHaveTextContent("Source details aren't available yet.");
  });

  it("an empty sources list counts as no details", () => {
    const a = buildMockResult(mockInput, "disputed").products[0]!;
    const criteria = a.criteria.map((c) => (c.state === "disputed" ? { ...c, sources: [] } : c));
    expect(row(card({ ...a, criteria }))).toHaveTextContent("Source details aren't available yet.");
  });

  it("the mock can show the state a live result is in", () => {
    const a = buildMockResult(mockInput, "disputed-no-sources").products[0]!;
    expect(a.criteria[1]).toMatchObject({ state: "disputed", value: null });
    expect(a.criteria[1]?.sources).toBeUndefined();
  });
});

describe("data coverage", () => {
  const scenario = buildMockResult(mockInput, "estimated");
  const estimatedBadge = (shown: HTMLElement) =>
    [...shown.querySelectorAll("li span")].find((e) => /^Estimated/.test(e.textContent ?? ""));

  it("is a whole percentage, from the contract's coverage", () => {
    const { products } = resultFromSearch(example, rubric, T0);
    expect(products.map((p) => p.dataCoverage)).toEqual([100, 100, 90]);
  });

  it("rounds to a whole number and never writes a fraction", () => {
    const ranked = example.decision!.ranked.map((r) => ({ ...r, coverage: 0.6249 }));
    const edited = { ...example, decision: { ...example.decision!, ranked } };
    expect(resultFromSearch(edited, rubric, T0).products[0]!.dataCoverage).toBe(62);
  });

  it("sits next to the Estimated label", () => {
    const [a] = scenario.products;
    expect(a!.dataCoverage).toBe(62);
    expect(card(a!).textContent).toContain("Estimated. Data coverage 62%");
  });

  it("the example's estimated value, through the adapter, shows its product's coverage", () => {
    const orion = resultFromSearch(example, rubric, T0).products[2]!;
    expect(card(orion).textContent).toContain("Estimated. Data coverage 90%");
  });

  it("says it isn't known when the figure is missing, and never shows 0%", () => {
    const b = scenario.products[1]!;
    expect(b.dataCoverage).toBeNull();
    const shown = card(b);
    expect(shown.textContent).toContain("Estimated. Coverage not known");
    expect(shown.textContent).not.toContain("0%");
    const { dataCoverage: _gone, ...none } = b;
    expect(estimatedBadge(card(none))).toHaveTextContent("Estimated. Coverage not known");
  });

  it("a real 0% is a real figure, shown as it is", () => {
    expect(coverageText(0)).toBe("Data coverage 0%");
  });

  it("only a usable number counts as a figure", () => {
    expect(coverageText(61.6)).toBe("Data coverage 62%");
    for (const missing of [null, undefined, Number.NaN, Infinity]) {
      expect(coverageText(missing)).toBe("Coverage not known");
    }
  });

  it("every product shows its coverage on the card, estimated or not", () => {
    const [a] = buildMockResult(mockInput, "success").products;
    expect(card(a!)).toHaveTextContent("Data coverage 92%");
    expect(card(a!)).not.toHaveTextContent("Estimated");
  });
});

describe("couldn't check", () => {
  const item = (p: Parameters<typeof CouldntCheckItem>[0]["product"]) =>
    show(
      <ul>
        <CouldntCheckItem product={p} />
      </ul>,
    );

  it("gives each item a web search for the product and the criterion", () => {
    const [p] = buildMockResult(mockInput, "couldnt-check").couldntCheck;
    const link = item(p!).querySelector("a")!;
    expect(new URL(link.href).origin).toBe("https://www.google.com");
    expect(new URL(link.href).searchParams.get("q")).toBe("Sample laptop F CPU performance");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("Search the web");
    expect(link).toHaveTextContent("for Sample laptop F (opens in a new tab)");
  });

  it("keeps the name and the reason", () => {
    const [p] = resultFromSearch(example, rubric, T0).couldntCheck;
    const shown = item(p!);
    expect(shown).toHaveTextContent("Vela Pro 14");
    expect(shown).toHaveTextContent("We couldn't confirm it has at least 16 GB RAM");
  });

  it("the link uses the criterion's name, never the shopper's own words for the rule", () => {
    const [p] = resultFromSearch(example, rubric, T0).couldntCheck;
    const search = new URL(item(p!).querySelector("a")!.href).searchParams.get("q");
    expect(search).toBe("Vela Pro 14 Ram gb");
    expect(search).not.toContain("at least 16 GB RAM");
  });

  it("uses the criterion's title when the rubric has one", () => {
    const titled = [{ criterionId: "ram_gb", title: "RAM (GB)", weight: 1 }];
    const [p] = resultFromSearch(example, titled, T0).couldntCheck;
    expect(p!.criterion).toBe("RAM (GB)");
  });

  it("with no criterion named, the link searches for the product alone", () => {
    const search = (url: string) => new URL(url).searchParams.get("q");
    const link = item({ name: "Vela Pro 14", reason: "No specs." }).querySelector("a")!;
    expect(search(link.href)).toBe("Vela Pro 14");
  });

  it("products left out get no search link", () => {
    const { leftOut } = resultFromSearch(example, rubric, T0);
    expect(leftOut[0]).not.toHaveProperty("criterion");
  });
});

describe("focus after the stop-research dialog", () => {
  function page() {
    document.body.innerHTML = `
      <header><button id="opener">Open menu</button></header>
      <main><h1 tabindex="-1">Researching laptops for you</h1></main>`;
    return {
      opener: document.getElementById("opener")!,
      heading: document.querySelector("h1")!,
    };
  }

  it("goes back to the control that opened it", () => {
    const { opener } = page();
    giveFocusBack(opener);
    expect(opener).toHaveFocus();
  });

  it("goes to the main heading when that control has left the page", () => {
    const { opener, heading } = page();
    opener.remove();
    giveFocusBack(opener);
    expect(heading).toHaveFocus();
  });

  it("goes to the main heading when that control can't take focus", () => {
    const { opener, heading } = page();
    opener.setAttribute("disabled", "");
    giveFocusBack(opener);
    expect(heading).toHaveFocus();
  });

  it("goes to the main heading when nothing was focused", () => {
    const { heading } = page();
    giveFocusBack(document.body);
    expect(heading).toHaveFocus();
    page();
    giveFocusBack(null);
    expect(document.querySelector("h1")).toHaveFocus();
  });
});

describe("the saved banner", () => {
  const BANNER =
    "This result isn't saved. It stays at this address for up to 30 minutes, then it's gone.";
  const banner = (saved: boolean) => {
    const r = resultFromSearch({ ...example, saved }, rubric, T0);
    return show(<NotSavedNotice saved={r.saved} />);
  };

  it("shows when the backend says the result was not saved", () => {
    expect(banner(false)).toHaveTextContent(BANNER);
  });

  it("does not show when the row was committed", () => {
    expect(banner(true)).toHaveTextContent("");
    expect(banner(true).textContent).toBe("");
  });

  it("the mock never claims a save", () => {
    expect(buildMockResult(mockInput, "success").saved).toBe(false);
  });
});
