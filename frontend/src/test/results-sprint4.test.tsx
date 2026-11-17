/**
 * The Sprint 4 result states (task #59): a product's chance of ranking first with its confidence in words (US-19),
 * a tie of three, the explanation's trade-off and what it couldn't verify (US-20), a score compared with the other
 * products, offers over budget, out of stock or exactly 24 hours old (US-24), and an LLM outage with "Try again"
 * (US-31). The shared example goes through the real adapter after a contract check, so what is shown is what the
 * contract carries.
 */
import { act, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import { LlmUnavailableNotice } from "@/components/results/primitives";
import { ResultCard, TooCloseToCall } from "@/components/results/ResultCard";
import { checkContract } from "@/lib/api/schemas";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import { buildMockResult } from "@/lib/results/mockResults";
import type { ProductResult } from "@/lib/results/types";
import { asOfText, tieGroups, tiesFor } from "@/lib/results/view";
import type { SearchResult } from "@/types/contracts/SearchResult";

const example = searchExample as unknown as SearchResult;
const T0 = Date.parse(example.started_at);
const NOW = Date.parse("2026-11-17T10:00:00Z");
const input = {
  sessionId: "sess_sprint4screens0001",
  category: "laptop",
  rubric: [
    { criterionId: "cpu", title: "CPU performance", weight: 3 },
    { criterionId: "ram", title: "Memory", weight: 2 },
    { criterionId: "battery", title: "Battery life", weight: 1 },
  ],
  mustHaves: [],
  exclusions: [],
};

function show(ui: ReactElement): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(ui);
  return host;
}

const card = (p: ProductResult, products: ProductResult[] = [p]) =>
  show(<ResultCard product={p} tiedWith={tiesFor(products, p)} index={0} />);

/** The shared example with every Sprint 4 field set the other way, as a live result can carry them. */
function edited(): SearchResult {
  const copy = structuredClone(example);
  copy.llm_unavailable = true;
  Object.assign(copy.commerce!.offers[0]!, { over_budget: true, availability: "out_of_stock" });
  for (const c of copy.decision!.ranked[0]!.contributions) c.basis = "compared";
  const explanation = copy.explanations[0]!;
  explanation.method = "llm_unchecked";
  explanation.unverified = ["battery_life"];
  explanation.sentences.forEach((s, i) => {
    s.supported = i === 0 ? false : null;
  });
  return copy;
}

describe("the contract's Sprint 4 fields reach the screen", () => {
  it("the edited example is still a valid SearchResult", async () => {
    await expect(checkContract("SearchResult", edited())).resolves.toBeDefined();
  });

  it("maps the outage, the basis, the offer's stock and budget, and what the explanation couldn't verify", () => {
    const source = edited();
    const r = resultFromSearch(source, [], T0);
    expect(r.llmUnavailable).toBe(true);
    const first = r.products.find((p) => p.id === source.decision!.ranked[0]!.product_id)!;
    expect(first.reasons!.map((x) => x.basis)).toEqual(first.reasons!.map(() => "compared"));
    const offered = r.products.find((p) => p.id === source.commerce!.offers[0]!.product_id)!;
    expect(offered.offers[0]).toMatchObject({ overBudget: true, availability: "out_of_stock" });
    const explained = r.products.find((p) => p.id === source.explanations[0]!.product_id)!;
    expect(explained.unverified).toEqual(["Battery life"]);
    expect(explained.explanationMethod).toBe("llm_unchecked");
    expect(explained.explanationParts!.map((p) => p.supported)).toEqual(
      source.explanations[0]!.sentences.map((_, i) => (i === 0 ? false : null)),
    );
  });

  it("an ordinary result has no outage", () => {
    expect(resultFromSearch(example, [], T0).llmUnavailable).toBe(false);
  });
});

describe("chance of ranking first and confidence (US-19)", () => {
  it("each card says its chance as a whole percentage and its confidence in words", () => {
    const [a, b, c] = buildMockResult(input, "success", NOW).products;
    expect(card(a!)).toHaveTextContent("62% chance of ranking first");
    expect(card(a!)).toHaveTextContent("High confidence");
    expect(card(b!)).toHaveTextContent("27% chance of ranking first");
    expect(card(b!)).toHaveTextContent("Medium confidence");
    expect(card(c!)).toHaveTextContent("11% chance of ranking first");
    expect(card(c!)).toHaveTextContent("Low confidence");
  });

  it("a product with no chance says nothing about one, never 0%", () => {
    const [a] = buildMockResult(input, "success", NOW).products;
    expect(card({ ...a!, chanceFirst: null })).not.toHaveTextContent("chance of ranking first");
  });
});

describe("a tie of three", () => {
  const { products } = buildMockResult(input, "tie-three", NOW);

  it("is one group, named in full, with what separates them", () => {
    const groups = tieGroups(products);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.products).toHaveLength(3);
    const shown = show(<TooCloseToCall tie={groups[0]!} />);
    expect(shown).toHaveTextContent(
      "Sample laptop A, Sample laptop B and Sample laptop C are tied for 1st.",
    );
    expect(shown).toHaveTextContent("What separates them: CPU performance");
  });

  it("each card names the other two it is tied with", () => {
    expect(card(products[0]!, products)).toHaveTextContent(
      "Tied with Sample laptop B and Sample laptop C",
    );
    expect(card(products[2]!, products)).toHaveTextContent("Tied for 1st");
  });
});

describe("the explanation (US-20)", () => {
  const explained = () => {
    const source = edited();
    const r = resultFromSearch(source, [], T0);
    return r.products.find((p) => p.id === source.explanations[0]!.product_id)!;
  };

  it("shows the trade-off on its own line and names what it couldn't verify", () => {
    const shown = card(explained());
    expect(shown).toHaveTextContent(`One trade-off: ${example.explanations[0]!.trade_off}`);
    // D-44: a criterion title is never lower-cased.
    expect(shown).toHaveTextContent("Not verified: Battery life.");
  });

  it("says when a sentence wasn't found in its sources", () => {
    expect(card(explained())).toHaveTextContent("(not confirmed by its sources)");
  });

  it("shows a template explanation just as written, with nothing claimed about it", () => {
    const [a] = buildMockResult(input, "success", NOW).products;
    const template = "Sample laptop A fits your top criteria within your must-haves.";
    const shown = card({
      ...a!,
      explanationMethod: "template",
      explanation: template,
      explanationParts: [{ text: template, claims: [], supported: null }],
      tradeOff: null,
      unverified: [],
    });
    expect(shown.querySelector("[data-explanation]")).toHaveTextContent(template);
    expect(shown).not.toHaveTextContent("Not verified:");
    expect(shown).not.toHaveTextContent("not confirmed");
  });

  it("a reason whose score was compared with the other products says so", () => {
    const [a] = buildMockResult(input, "success", NOW).products;
    const shown = card(a!);
    expect(shown.textContent?.match(/Compared with the other products found\./g)).toHaveLength(1);
  });
});

describe("offers (US-24)", () => {
  it("an offer above the budget is flagged, and stock is said in words", () => {
    const [a, b] = buildMockResult(input, "over-budget", NOW).products;
    expect(card(a!)).toHaveTextContent("Above your budget");
    expect(card(b!)).toHaveTextContent("Out of stock");
    expect(card(b!)).not.toHaveTextContent("Above your budget");
  });

  it("in stock is said; unknown stock says nothing", () => {
    const [a] = buildMockResult(input, "success", NOW).products;
    const shown = card(a!);
    expect(shown.textContent?.match(/In stock/g)).toHaveLength(1); // the first offer only: the second's is unknown
    expect(shown).not.toHaveTextContent("Out of stock");
    expect(shown).not.toHaveTextContent("unknown");
  });

  it("a price exactly 24 hours old has not changed yet; a minute more may have", () => {
    const [a] = buildMockResult(input, "offer-24h", NOW).products;
    expect(a!.offers.map((o) => o.stale)).toEqual([false, true]);
    expect(card(a!).textContent?.match(/May have changed/g)).toHaveLength(1);
  });

  it("every store link opens the store's own page in a new tab, with no referrer", () => {
    const [a] = buildMockResult(input, "success", NOW).products;
    const links = card(a!).querySelectorAll("[aria-label^='Store offers'] a");
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  });
});

describe("an LLM outage (US-31)", () => {
  let host: HTMLDivElement | null = null;
  afterEach(() => host?.remove());

  it("the mock scenario carries the outage, with nothing ranked", () => {
    const r = buildMockResult(input, "llm-unavailable", NOW);
    expect(r.llmUnavailable).toBe(true);
    expect(r.products).toEqual([]);
  });

  it("says so plainly, as an alert, and Try again starts the search again", async () => {
    const onRetry = vi.fn();
    host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<LlmUnavailableNotice onRetry={onRetry} />));
    const alert = host.querySelector("[role=alert]")!;
    expect(alert).toHaveTextContent(
      "We couldn't reach the AI services that read the sources, so this search found nothing. Please try again.",
    );
    const button = [...host.querySelectorAll("button")].find((b) => b.textContent === "Try again")!;
    await act(async () => button.click());
    expect(onRetry).toHaveBeenCalledOnce();
    await act(async () => root.unmount());
  });
});

describe("an explanation's sentences open their sources (US-20)", () => {
  let host: HTMLDivElement | null = null;
  afterEach(() => host?.remove());

  it("each sentence with sources gets a full-size button that opens the drawer for it, with no badge", async () => {
    const source = edited();
    const r = resultFromSearch(source, [], T0);
    const p = r.products.find((x) => x.id === source.explanations[0]!.product_id)!;
    const onShowSources = vi.fn();
    host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () =>
      root.render(<ResultCard product={p} tiedWith={[]} index={0} onShowSources={onShowSources} />),
    );
    const buttons = [...host.querySelectorAll("button")].filter((b) =>
      /^Sentence \d+ sources: /.test(b.getAttribute("aria-label") ?? ""),
    );
    const cited = p.explanationParts!.filter((x) => x.claims.length > 0);
    expect(buttons).toHaveLength(cited.length);
    expect(buttons[0]).toHaveTextContent(/^Sentence \d+$/);
    expect(buttons[0]!.className).toContain("min-h-11");
    await act(async () => buttons[0]!.click());
    const [product, criterion, opener, sentence] = onShowSources.mock.calls[0]!;
    expect(product).toBe(p);
    expect(criterion.claims).toEqual(cited[0]!.claims);
    expect(opener).toBe(buttons[0]);
    expect(sentence).toEqual({ supported: cited[0]!.supported ?? null });
    await act(async () => root.unmount());
  });
});

describe("the as-of time (US-24)", () => {
  // Local times, so the test reads the same in any time zone.
  const at = (day: number, hour: number, minute: number) =>
    new Date(2026, 10, day, hour, minute).toISOString();
  const now = Date.parse(at(17, 18, 0));

  it("today says the time only", () => {
    expect(asOfText(at(17, 14, 15), now)).toBe("as of 2:15 pm");
  });

  it("another day says the date too", () => {
    expect(asOfText(at(3, 9, 5), now)).toBe("as of 3 Nov, 9:05 am");
  });

  it("no usable time says nothing, rather than guess", () => {
    expect(asOfText(null, now)).toBeNull();
    expect(asOfText("not a time", now)).toBeNull();
  });
});
