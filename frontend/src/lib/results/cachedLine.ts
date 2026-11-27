/**
 * The results header's line for a repeated search answered from the cache (US-34): "From a search made at 2:15 pm"
 * today, "From a search made at 3 Oct, 2:15 pm" another day. Null for a fresh search, or with no usable time, so the
 * header never claims a time it doesn't know. Its own module, so the brief page's bundle never carries it.
 */
export function cachedLine(cachedAt: string | null | undefined, now: number): string | null {
  const made = cachedAt ? new Date(cachedAt) : null;
  if (!made || Number.isNaN(made.getTime())) return null;
  const time = made.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  if (new Date(now).toDateString() === made.toDateString()) return `From a search made at ${time}`;
  const day = made.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  return `From a search made at ${day}, ${time}`;
}
