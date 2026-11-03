import { statusOf, type InterviewState } from "./reducer";

/** Criteria rows for "Your brief": only real answers are shown, never invented ones. */
export function briefCriteria(s: InterviewState) {
  return s.criteria.map((c) => {
    const status = statusOf(s, c.id);
    const a = s.answers[c.id];
    const value =
      status === "pending"
        ? null
        : a?.status === "answered" || a?.status === "remembered"
          ? a.value
          : null;
    return { id: c.id, title: c.title, status, value };
  });
}

export function progressOf(s: InterviewState) {
  return { settled: s.criteria.filter((c) => s.answers[c.id]).length, total: s.criteria.length };
}

/** True when the backend note already contains the question, so it isn't shown twice. */
export function noteContainsQuestion(note: string | null, question: string | null) {
  if (!note || !question) return false;
  return note.toLowerCase().includes(question.toLowerCase().replace(/[?.]$/, ""));
}
