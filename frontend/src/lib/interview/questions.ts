import type { Criterion, QuestionTerm, Step } from "./types";

/**
 * The buttons under every question. The backend understands exactly these (backend/app/interview/answers.py),
 * plus Skip, which the screen has its own button for.
 */
export const IMPORTANCE_REPLIES = ["Low", "Medium", "High", "Good enough"];

/** Shown when the backend refuses a reply as out of date and the screen has caught up with it. */
export const ALREADY_ANSWERED = "This question was already answered. Here is where you are.";

/**
 * A question worded the way the backend words it (backend/app/interview/questions.py), so a question asked
 * again after "Go back" looks the same in mock and live mode. The title opens the sentence, so it stands
 * exactly as given (D-44). `terms` are the words the question explains, when it has any (US-07).
 */
export function questionFor(c: Criterion, terms: QuestionTerm[] = []): Step {
  return {
    kind: "question",
    criterionId: c.id,
    question: `${c.title}: how much does it matter to you?`,
    note: null,
    why: `${c.definition}${c.lowerIsBetter ? " Lower is better here." : ""}`,
    replies: IMPORTANCE_REPLIES,
    terms,
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
