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
import type { ProductResult, RubricItem } from "@/lib/results/types";
import { ordinal, tieGroups, tiesFor } from "@/lib/results/view";
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
