import type { InterviewState } from "@/lib/interview/reducer";
import { exactFromAnswer, levelOf, weightOf, type RoughLevel } from "@/lib/interview/roughLevels";
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
 * Builds the rubric from the F2 interview in mock mode, with the same table the backend uses. A rough answer weighs
 * its level, and an exact one weighs itself. Anything else (typed words, skipped, not asked) uses the standard weight,
 * so no user choice is invented. In live mode the backend works out the weights itself (see rubricForBrief).
 */
const PHRASE_LEVEL: Record<string, RoughLevel> = {
  "very important": "high",
  "yes, a lot": "high",
  "somewhat important": "medium",
  "a little": "medium",
  "not important": "low",
  "not really": "low",
};
const STANDARD_LEVEL: RoughLevel = "medium";

function answerWeight(value: string): number | null {
  const phrase = value.trim().toLowerCase();
  const level =
    levelOf(value) ?? (Object.hasOwn(PHRASE_LEVEL, phrase) ? PHRASE_LEVEL[phrase] : null);
  return level ? weightOf(level) : exactFromAnswer(value);
}

export function rubricFromInterview(s: Pick<InterviewState, "criteria" | "answers">): RubricItem[] {
  return s.criteria.map((c) => {
    const a = s.answers[c.id];
    const mapped = a?.value ? answerWeight(a.value) : null;
    let origin: WeightOrigin = "standard";
    if (mapped !== null && a?.status === "answered") origin = "answer";
    else if (mapped !== null && a?.status === "remembered") origin = "remembered";
    return {
      criterionId: c.id,
      title: c.title,
      weight: origin === "standard" || mapped === null ? weightOf(STANDARD_LEVEL) : mapped,
      origin,
    };
  });
}

/**
 * The weights the brief and results pages show. Live mode shows only the backend's own rubric (null until
 * the interview is finished), never weights guessed here; mock mode works them out from the answers.
 */
export function rubricForBrief(
  s: Pick<InterviewState, "criteria" | "answers" | "rubric">,
  live: boolean,
): RubricItem[] | null {
  if (live) return s.rubric;
  return s.rubric ?? rubricFromInterview(s);
}

export const ORIGIN_LABEL: Record<WeightOrigin, string> = {
  answer: "From your answer",
  standard: "Standard",
  remembered: "Remembered",
};
