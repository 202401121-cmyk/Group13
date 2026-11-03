import type { Criterion, Step } from "./types";

/**
 * The buttons under every question. The backend understands exactly these (backend/app/interview/answers.py),
 * plus Skip, which the screen has its own button for.
 */
export const IMPORTANCE_REPLIES = ["Low", "Medium", "High"];

/**
 * A question worded the way the backend words it (backend/app/interview/questions.py), so a question asked
 * again after "Go back" looks the same in mock and live mode.
 */
export function questionFor(c: Criterion): Step {
  return {
    kind: "question",
    criterionId: c.id,
    question: `How much do you care about ${c.title.toLowerCase()}?`,
    note: null,
    why: `${c.definition}${c.lowerIsBetter ? " Lower is better here." : ""}`,
    replies: IMPORTANCE_REPLIES,
  };
}

export function withTimeout<T>(
  p: Promise<T>,
  ms: number,
  message = "This is taking too long. Please try again.",
): Promise<T> {
  let t: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, rej) => {
    t = setTimeout(() => rej(new Error(message)), ms);
  });
  return Promise.race([p, timeout]).finally(() => clearTimeout(t));
}
