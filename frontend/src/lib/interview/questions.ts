import type { Criterion, Step } from "./types";

export const IMPORTANCE_REPLIES = ["Very important", "Somewhat important", "Not important"];

export function questionFor(c: Criterion): Step {
  return {
    kind: "question",
    criterionId: c.id,
    question: `How important is ${c.title.toLowerCase()} to you?`,
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
