/**
 * US-21 on screen: what "Where this comes from" says for each state, and the main reasons on a card. Static
 * renders: these tests read what is said; the e2e spec checks how the drawer behaves.
 */
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import { ResultCard } from "@/components/results/ResultCard";
import { SourceDrawer, type SourceTarget } from "@/components/results/SourceDrawer";
import { WITHHELD, resultFromSearch } from "@/lib/results/liveAdapter";
import type { ClaimView, EvidenceView, ValueState } from "@/lib/results/types";
import type { SearchResult } from "@/types/contracts/SearchResult";

const example = searchExample as unknown as SearchResult;
const T0 = Date.parse(example.started_at);

function show(ui: ReactElement): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(ui);
  return host;
}

const maker: ClaimView = {
  claimId: "clm_1",
  side: "supporting",
  quote: "battery rated up to 10 hours",
  quoteWithheld: null,
  valueText: "10 hours",
  source: {
    id: "src_1",
    name: "kestrel.example.com",
    typeLabel: "Maker's site",
    url: "https://kestrel.example.com/slim-15/specs",
    dateLabel: "Checked 3 Oct 2026",
  },
};
const reddit: ClaimView = {
  claimId: "clm_2",
  side: "conflicting",
  quote: "I get about 6 hours, not 10",
  quoteWithheld: null,
  valueText: "6 hours",
  source: {
    id: "src_2",
    name: "r/developersIndia",
    typeLabel: "Reddit",
    url: "https://www.reddit.com/r/developersIndia/comments/abc123/",
    dateLabel: "Checked 3 Oct 2026",
  },
};

const target = (state: ValueState, claims: ClaimView[] = []): SourceTarget => ({
  productId: "prd_kestrel_slim_15",
  productName: "Kestrel Slim 15",
  criterion: { criterionId: "battery_life", label: "Battery life", state, claims },
});
const answered = (state: ValueState | null, claims: ClaimView[]): EvidenceView => ({
  state,
  claims,
  withheldReason: null,
  from: "route",
});

function drawer(t: SourceTarget, evidence: EvidenceView | null) {
  return show(<SourceDrawer target={t} evidence={evidence} onClose={() => {}} />);
}

describe("where this comes from", () => {
  it("is a dialog named by its heading", () => {
    const d = drawer(target("verified", [maker]), answered("verified", [maker]));
    const dialog = d.querySelector("dialog")!;
    const heading = d.querySelector(`#${dialog.getAttribute("aria-labelledby")!}`)!;
    expect(heading.tagName).toBe("H2");
    expect(heading).toHaveTextContent("Battery life: where this comes from");
    expect(d.querySelector("button")).toHaveTextContent("Close");
  });

  it("a known value shows its source's name, type and date, the exact quote and a link", () => {
    const d = drawer(target("verified", [maker]), answered("verified", [maker]));
    expect(d).toHaveTextContent("Known");
    expect(d).toHaveTextContent("kestrel.example.com");
    expect(d).toHaveTextContent("Maker's site");
    expect(d).toHaveTextContent("Checked 3 Oct 2026");
    expect(d.querySelector("blockquote")).toHaveTextContent("battery rated up to 10 hours");
    const link = d.querySelector<HTMLAnchorElement>("a")!;
    expect(link.href).toBe("https://kestrel.example.com/slim-15/specs");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("Open source");
    expect(link.querySelector(".sr-only")).toHaveTextContent("(opens in a new tab)");
  });

  it("a disputed value shows every source on both sides", () => {
    const d = drawer(target("disputed", [maker, reddit]), answered("disputed", [maker, reddit]));
    expect(d).toHaveTextContent("Disputed");
    const groups = [...d.querySelectorAll("h3")].map((h) => h.textContent);
    expect(groups).toEqual(["What supports it", "What disagrees"]);
    const [supports, disagrees] = [...d.querySelectorAll("section")];
    expect(supports).toHaveTextContent("kestrel.example.com");
    expect(supports).toHaveTextContent("10 hours");
    expect(supports).not.toHaveTextContent("r/developersIndia");
    expect(disagrees).toHaveTextContent("r/developersIndia");
    expect(disagrees).toHaveTextContent("I get about 6 hours, not 10");
    expect(d.querySelectorAll("blockquote")).toHaveLength(2);
  });

  it("an unknown value says no source gave it, and never shows 0", () => {
    const d = drawer(target("unknown"), answered("unknown", []));
    expect(d).toHaveTextContent("Unknown");
    expect(d).toHaveTextContent("No source gave this value. It isn't counted as zero.");
    expect(d.textContent).not.toMatch(/\b0\b/);
    expect(d.querySelector("blockquote")).toBeNull();
  });

  it("an estimated value says where the estimate came from", () => {
    const d = drawer(target("estimated"), answered("estimated", []));
    expect(d).toHaveTextContent("Estimated");
    expect(d).toHaveTextContent("Estimated from similar products; no source gave it directly.");
  });

  it("a withheld quote says why, and keeps the link", () => {
    const hidden = {
      ...reddit,
      side: "supporting" as const,
      quote: null,
      quoteWithheld: WITHHELD.reddit,
    };
    const d = drawer(target("verified", [hidden]), answered("verified", [hidden]));
    expect(d.querySelector("blockquote")).toBeNull();
    expect(d).toHaveTextContent(WITHHELD.reddit);
    expect(d.querySelector("a")).toHaveAttribute("href", reddit.source.url);
  });

  it("says it is loading while the evidence is on its way", () => {
    const d = drawer(target("verified", [maker]), null);
    expect(d.querySelector("[role=status]")).toHaveTextContent(/Loading/);
    expect(d.querySelector("blockquote")).toBeNull();
  });

  it("keeps the card's state when the route wasn't used", () => {
    const d = drawer(target("disputed", [maker, reddit]), {
      state: null,
      claims: [maker, reddit],
      withheldReason: null,
      from: "citations",
    });
    expect(d).toHaveTextContent("What disagrees");
  });

  it("a disputed value with no claims says the details aren't available yet", () => {
    const d = drawer(target("disputed"), answered("disputed", []));
    expect(d).toHaveTextContent("Source details aren't available yet.");
  });
});

describe("main reasons on a card", () => {
  const r = resultFromSearch(example, [], T0);
  const kestrel = r.products.find((p) => p.name === "Kestrel Slim 15")!;
  const card = (onShow?: () => void) =>
    show(
      <ResultCard
        product={kestrel}
        tiedWith={[]}
        index={1}
        {...(onShow ? { onShowSources: onShow } : {})}
      />,
    );

  it("lists the top three, each with its state and up to two source links", () => {
    const c = card(() => {});
    const list = c.querySelector("[aria-label^='Main reasons']")!;
    const items = [...list.querySelectorAll(":scope > li")];
    expect(items.map((li) => li.querySelector("[data-reason-label]")?.textContent)).toEqual([
      "Cpu performance",
      "Keyboard comfort",
      "Battery life",
    ]);
    const battery = items[2]!;
    expect(battery).toHaveTextContent("Disputed");
    const links = [...battery.querySelectorAll("a")];
    expect(links.map((a) => a.textContent)).toEqual([
      "kestrel.example.com (opens in a new tab)",
      "r/developersIndia (opens in a new tab)",
    ]);
    for (const a of links) {
      expect(a).toHaveAttribute("target", "_blank");
      expect(a).toHaveAttribute("rel", "noopener noreferrer");
    }
    expect(items[0]).toHaveTextContent("Known");
  });

  it("links each explanation sentence to its sources, and leaves the trade-off plain", () => {
    const nova = r.products.find((p) => p.name === "Nova Book 14")!;
    const explained = show(<ResultCard product={nova} tiedWith={[]} index={0} />).querySelector(
      "[data-explanation]",
    )!;
    expect(explained).toHaveTextContent("Its keyboard is the most comfortable of the three");
    expect(explained).toHaveTextContent(
      "The Orion Play 15 compiles faster, but it is much heavier.",
    );
    const links = [...explained.querySelectorAll("a")];
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      [
        "r/developersIndia, the source for this sentence (opens in a new tab)",
        "https://www.reddit.com/r/developersIndia/comments/abc123/",
      ],
      [
        "example-reviews.in, the source for this sentence (opens in a new tab)",
        "https://example-reviews.in/kestrel-slim-15-review",
      ],
    ]);
    for (const a of links) {
      expect(a).toHaveAttribute("target", "_blank");
      expect(a).toHaveAttribute("rel", "noopener noreferrer");
    }
  });

  it("a sentence whose two claims share a source links it once", () => {
    const explained = card().querySelector("[data-explanation]")!;
    expect([...explained.querySelectorAll("a")].map((a) => a.textContent)).toEqual([
      "example-reviews.in, the source for this sentence (opens in a new tab)",
    ]);
  });

  it("gives each reason a button named for its criterion", () => {
    const buttons = [...card(() => {}).querySelectorAll("button")];
    const battery = buttons.find(
      (b) => b.getAttribute("aria-label") === "Where this comes from: Battery life",
    );
    // The name starts with the words on the button, so speech input can find it.
    expect(battery).toHaveTextContent(/^Where this comes from$/);
  });

  it("has no source button when nothing can open the drawer", () => {
    const buttons = [...card().querySelectorAll("button")].map((b) => b.textContent);
    expect(buttons.filter((b) => b.startsWith("Where this comes from"))).toEqual([]);
  });

  it("shows the gap to the leader for a product that isn't tied", () => {
    const orion = r.products.find((p) => p.name === "Orion Play 15")!;
    const c = show(<ResultCard product={orion} tiedWith={[]} index={2} />);
    expect(c).toHaveTextContent("11.3 points behind the top score");
  });
});
