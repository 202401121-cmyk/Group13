import type { InterviewState } from "@/lib/interview/reducer";
import type { RubricItem, WeightOrigin } from "./types";

/**
 * Normalises weights to whole percentages that sum to exactly 100 using the
 * largest-remainder method. Ties in remainder go to the earlier item.
 */
export function normalizePercents(weights: number[]): number[] {
  const clean = weights.map((w) => (Number.isFinite(w) && w > 0 ? w : 0));
  const total = clean.reduce((a, b) => a + b, 0);
  if (total <= 0) return clean.map(() => 0);
  const exact = clean.map((w) => (w / total) * 100);
  const floors = exact.map(Math.floor);
  let left = 100 - floors.reduce((a, b) => a + b, 0);
  const order = exact
    .map((e, i) => ({ i, r: e - Math.floor(e) }))
    .sort((a, b) => b.r - a.r || a.i - b.i);
  for (const { i } of order) {
    if (left <= 0) break;
    floors[i]! += 1;
    left -= 1;
  }
  return floors;
}

export interface WeightRow extends RubricItem {
  percent: number;
}

/** Sorted by descending weight; equal weights keep their original order. */
export function weightRows(rubric: RubricItem[]): WeightRow[] {
  const pct = normalizePercents(rubric.map((r) => r.weight));
  return rubric
    .map((r, i) => ({ ...r, percent: pct[i]!, i }))
    .sort((a, b) => b.weight - a.weight || a.i - b.i)
    .map(({ i: _i, ...r }) => r);
}

/**
 * Builds the rubric from the F2 interview. Only the three quick-reply
 * importance answers map to a weight; anything else (typed answers, skipped,
 * not asked) uses the standard weight, so no user choice is invented.
 */
const ANSWER_WEIGHT: Record<string, number> = {
  "very important": 3,
  "yes, a lot": 3,
  "somewhat important": 2,
  "a little": 2,
  "not important": 1,
  "not really": 1,
};
const STANDARD_WEIGHT = 2;

export function rubricFromInterview(s: Pick<InterviewState, "criteria" | "answers">): RubricItem[] {
  return s.criteria.map((c) => {
    const a = s.answers[c.id];
    const mapped = a?.value ? ANSWER_WEIGHT[a.value.trim().toLowerCase()] : undefined;
    let origin: WeightOrigin = "standard";
    if (mapped && a?.status === "answered") origin = "answer";
    else if (mapped && a?.status === "remembered") origin = "remembered";
    return {
      criterionId: c.id,
      title: c.title,
      weight: origin === "standard" ? STANDARD_WEIGHT : mapped!,
      origin,
    };
  });
}

export const ORIGIN_LABEL: Record<WeightOrigin, string> = {
  answer: "From your answer",
  standard: "Standard",
  remembered: "Remembered",
};
