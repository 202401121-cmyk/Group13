import { keyInWords } from "@/lib/text";
import type { SavedSearchItem } from "./types";

/** When a search was saved, in words: "20 Nov 2026, 1:30 pm". */
export function savedAt(ms: number): string {
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(ms);
}

/** Does a saved search match the filter? Its request, its kind as shown ("Air fryer") and its best match count. */
export function matchesFilter(item: SavedSearchItem, filter: string): boolean {
  const words = filter.trim().toLowerCase();
  if (!words) return true;
  const kind = item.category ? `${item.category} ${keyInWords(item.category)}` : "";
  return [item.request, kind, item.topProduct ?? ""].join(" ").toLowerCase().includes(words);
}

/** The calendar day a time falls on, where the shopper is: a key for grouping searches by day. */
export function dayKey(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

/** "Today", "Yesterday", or the day, with the year when it isn't this year: the heading over a day's searches. */
export function dayHeading(ms: number, now = Date.now()): string {
  const today = new Date(now);
  // The day before by the calendar, not 24 hours back, so a clock change never moves it.
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1).getTime();
  if (dayKey(ms) === dayKey(now)) return "Today";
  if (dayKey(ms) === dayKey(yesterday)) return "Yesterday";
  const sameYear = new Date(ms).getFullYear() === today.getFullYear();
  return new Intl.DateTimeFormat("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
    ...(sameYear ? {} : { year: "numeric" }),
  }).format(ms);
}
