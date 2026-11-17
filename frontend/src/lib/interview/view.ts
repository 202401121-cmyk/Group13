import { statusOf, type InterviewState } from "./reducer";
import type { Step } from "./types";

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

/** How far the interview has come. Once it has finished, early or not, every criterion is settled (S4-5). */
export function progressOf(s: InterviewState) {
  const total = s.criteria.length;
  if (s.phase === "finished") return { settled: total, total };
  return { settled: s.criteria.filter((c) => s.answers[c.id]).length, total };
}

/** What the question card shows for a step: the backend's own words, where they belong. */
export interface StepDisplay {
  /** Words before the question, as written: an answer, a redirect, a skip or correction note. Null when none. */
  note: string | null;
  /** "Asked another way" on a rephrase, else null. */
  label: string | null;
  /** The card's main line: the question, or a rephrase's own words. */
  heading: string;
  /** The question itself for screen readers, when the heading is a rephrase's words. */
  question: string | null;
}

const core = (question: string) =>
  question
    .toLowerCase()
    .replace(/[?.]\s*$/, "")
    .trim();

/**
 * How a step reads on the card (US-39). Nothing is reworded: the backend's message is split around the question it
 * already holds, so the question stays the heading and what comes before it (the answer to the shopper's question,
 * the one-line redirect, a skip or correction note) sits above it. A rephrase's words are the heading, with the same
 * buttons; the question stays for screen readers. A message that doesn't hold the question is shown whole above it.
 */
export function stepDisplay(step: Step): StepDisplay {
  const question = step.question ?? "";
  const message = step.note?.trim() ?? "";
  if (step.kind === "rephrase") {
    const own = message && core(message) !== core(question);
    return {
      note: null,
      label: "Asked another way",
      heading: own ? message : question,
      question: own ? question : null,
    };
  }
  if (!message) return { note: null, label: null, heading: question, question: null };
  const at = question ? message.toLowerCase().lastIndexOf(core(question)) : -1;
  if (at < 0) return { note: message, label: null, heading: question, question: null };
  const before = message.slice(0, at).trim();
  const after = message
    .slice(at + core(question).length)
    .replace(/^[?.]/, "")
    .trim();
  const note = [before, after].filter(Boolean).join(" ");
  return { note: note || null, label: null, heading: question, question: null };
}

/** The questions the shopper skipped, in the criteria's order, so they can be answered now. */
export function skippedCriteria(s: InterviewState) {
  return s.criteria.filter((c) => s.answers[c.id]?.status === "skipped");
}
