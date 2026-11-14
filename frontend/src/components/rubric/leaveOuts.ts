/**
 * What the rubric editor does with leave-outs read from words (US-10): which to add, in saves of at most MAX_EDITS,
 * keeping to MAX_EXCLUSIONS in all, and what to tell the shopper. Loaded the first time they press "Add", so the
 * brief page's first load stays within its budget (D-45).
 */
import { MAX_EDITS, MAX_EXCLUSIONS, sameExclusion } from "@/lib/interview/rubricEdits";
import type { ExclusionView, ParsedExclusions } from "@/lib/interview/types";

export const ASK_FOR_WORDS =
  "Type a brand, a model or a feature to leave out, such as “no Xiaomi”.";

function quoted(words: string[]): string {
  return words.map((w) => `“${w}”`).join(", ");
}

export interface LeaveOutPlan {
  /** The leave-outs to add, one save each, in order. */
  saves: ExclusionView[][];
  /** What to tell the shopper, or null. */
  said: string | null;
  /** True when every word was read, so the box can be emptied. */
  clear: boolean;
}

export function planLeaveOuts(read: ParsedExclusions, have: ExclusionView[]): LeaveOutPlan {
  const fresh = read.exclusions.filter((e) => !have.some((h) => sameExclusion(h, e)));
  const room = Math.max(0, MAX_EXCLUSIONS - have.length);
  const adding = fresh.slice(0, room);
  const said: string[] = [];
  if (read.exclusions.length === 0 && read.notUnderstood.length === 0) said.push(ASK_FOR_WORDS);
  if (read.notUnderstood.length > 0) {
    said.push(
      `We couldn't read ${quoted(read.notUnderstood)}. Try a brand, a model or a feature, such as “no Xiaomi” or “not touchscreen”.`,
    );
  }
  if (fresh.length > adding.length) {
    const left = quoted(fresh.slice(room).map((e) => e.value));
    said.push(`You can leave out up to ${MAX_EXCLUSIONS} things, so ${left} wasn't added.`);
  }
  if (read.exclusions.length > 0 && fresh.length === 0) said.push("That's already on your list.");
  const saves: ExclusionView[][] = [];
  for (let i = 0; i < adding.length; i += MAX_EDITS) saves.push(adding.slice(i, i + MAX_EDITS));
  return {
    saves,
    said: said.join(" ") || null,
    clear: read.exclusions.length > 0 && read.notUnderstood.length === 0,
  };
}
