/** The words the memory screen shows (US-11). */
import { keyInWords } from "@/lib/text";
import type { RememberedKind } from "./memory";

/** The kind of product a remembered item holds for: "Air fryer", or "Every search" for a brand left out everywhere. */
export function categoryLabel(category: string | null): string {
  return category ? keyInWords(category) : "Every search";
}

/** What kind of thing was remembered, in the words the interview uses. */
export const KIND_LABELS = {
  weight: "Priority",
  hard_rule: "Must-have",
  exclusion: "Leave out",
} as const satisfies Record<RememberedKind, string>;

/** A weight as the shopper set it: "8 of 10", "6.5 of 10". */
export function weightLabel(weight: number): string {
  return `${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(weight)} of 10`;
}

/** The day something was last changed: "4 Oct 2026". */
export function changedOn(ms: number): string {
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(ms);
}
