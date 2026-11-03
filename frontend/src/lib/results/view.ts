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
