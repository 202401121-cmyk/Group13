/** Plain helpers for the brief and results screens (no components, so fast refresh keeps working). */
import { describeBudget } from "@/lib/request/format";
import type { Brief } from "@/lib/request/types";
import type { ProductResult } from "./types";

export function mustHavesOf(brief: Brief): string[] {
  const budget = describeBudget(brief.budget);
  return [...(budget ? [`Budget: ${budget}`] : []), ...brief.mustHaves];
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
