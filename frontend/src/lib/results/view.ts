/** Plain helpers for the brief and results screens (no components, so fast refresh keeps working). */
import { describeBudget } from "@/lib/request/format";
import type { Brief } from "@/lib/request/types";
import { inSentence } from "@/lib/text";
import type { ProductResult, ResearchInput, RubricItem } from "./types";

/** A price older than this may have changed (the contract's PRICE_STALE_AFTER, backend/app/contracts/commerce.py). */
export const PRICE_STALE_AFTER_MS = 24 * 60 * 60 * 1000;

/**
 * The one staleness rule, the same as the backend's Offer.is_stale(): older than 24 hours is stale, and exactly
 * 24 hours is not. With no usable as-of time the answer is null, because a guess either way would be a claim.
 */
export function isPriceStale(asOf: string | null | undefined, now: number): boolean | null {
  const checked = asOf ? Date.parse(asOf) : Number.NaN;
  if (Number.isNaN(checked)) return null;
  return now - checked > PRICE_STALE_AFTER_MS;
}

/** A product's data coverage in words. A missing figure is never shown as 0%. */
export function coverageText(percent: number | null | undefined): string {
  return percent == null || !Number.isFinite(percent)
    ? "Coverage not known"
    : `Data coverage ${Math.round(percent)}%`;
}

/**
 * Where focus goes when "Stop research?" closes: back to the control that opened it, or, if that is gone or can't
 * take focus (hidden at this screen size, say), to the page's main heading. Never the page body.
 */
export function giveFocusBack(opener: HTMLElement | null) {
  if (opener && opener !== document.body) {
    opener.focus();
    if (document.activeElement === opener) return;
  }
  document.querySelector<HTMLElement>("main h1")?.focus();
}

export function mustHavesOf(brief: Brief): string[] {
  const budget = describeBudget(brief.budget);
  return [...(budget ? [`Budget: ${budget}`] : []), ...brief.mustHaves];
}

/** What a search needs from the brief: the same when it starts and when a reload picks it up. */
export function researchInput(
  sessionId: string,
  brief: Brief,
  rubric: RubricItem[],
  scenario?: string | undefined,
): ResearchInput {
  return {
    sessionId,
    category: inSentence(brief.product ?? "product"),
    rubric,
    mustHaves: mustHavesOf(brief),
    exclusions: brief.exclusions,
    scenario,
  };
}

/**
 * Keeps only the latest of several answers on their way. Each request takes a token with next(); its answer is
 * used only while isCurrent(token). cancel() drops every answer still on its way (the drawer was closed).
 */
export function latestOnly() {
  let current = 0;
  return {
    next: () => ++current,
    isCurrent: (token: number) => token === current,
    cancel: () => {
      current += 1;
    },
  };
}

/** Why a quote isn't shown, when nothing more is said. Reddit's words are not kept in saved results (D-53). */
export const WITHHELD = {
  reddit: "Reddit's words aren't kept in saved results. Open the link to read them on Reddit.",
  other: "The quote wasn't kept.",
} as const;

function dayOf(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** "Published 3 Oct 2026" when the source says when, else "Checked 3 Oct 2026" from when it was read. */
export function sourceDateLabel(
  published: string | null | undefined,
  fetched: string | null | undefined,
): string | null {
  const day = dayOf(published);
  if (day) return `Published ${day}`;
  const checked = dayOf(fetched);
  return checked ? `Checked ${checked}` : null;
}

export function formatCheckTime(iso: string | null | undefined) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Names of products sharing this product's rank. Never invents a tie-break. */
export function tiesFor(products: ProductResult[], p: ProductResult) {
  if (p.rank == null) return [];
  return products.filter((o) => o !== p && o.rank === p.rank).map((o) => o.name);
}

export interface TieGroup {
  rank: number;
  products: ProductResult[];
  /** Every criterion the tied products name, so none is picked over another. Empty when none is named. */
  criteria: string[];
}

/** The products that share a rank, one group per rank, best rank first. */
export function tieGroups(products: ProductResult[]): TieGroup[] {
  const byRank = new Map<number, ProductResult[]>();
  for (const p of products) {
    if (p.rank != null) byRank.set(p.rank, [...(byRank.get(p.rank) ?? []), p]);
  }
  return [...byRank]
    .filter(([, group]) => group.length > 1)
    .sort(([a], [b]) => a - b)
    .map(([rank, group]) => ({
      rank,
      products: group,
      criteria: [...new Set(group.flatMap((p) => p.separatingCriterion ?? []))],
    }));
}

/** 1 -> "1st", 2 -> "2nd", 11 -> "11th", 23 -> "23rd". */
export function ordinal(n: number): string {
  const lastTwo = n % 100;
  if (lastTwo >= 11 && lastTwo <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}
