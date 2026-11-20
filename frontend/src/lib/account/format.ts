/** When a search was saved, in words: "20 Nov 2026, 1:30 pm". */
export function savedAt(ms: number): string {
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(ms);
}
