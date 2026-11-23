import { questionFor } from "./questions";
import {
  MAX_ANSWER_LENGTH,
  type Answers,
  type Criterion,
  type QuestionTerm,
  type RememberedView,
  type Step,
  type Turn,
  type TurnResult,
} from "./types";
import type { RubricItem } from "@/lib/results/types";

export type Phase =
  | "loading-criteria"
  | "criteria-ready"
  | "criteria-error"
  | "starting"
  | "asking"
  | "sending"
  | "finished"
  | "error";

export interface InterviewState {
  phase: Phase;
  criteria: Criterion[];
  fallback: boolean;
  answers: Answers;
  step: Step | null;
  pending: Turn | null;
  revising: string | null;
  error: string | null;
  laterChanged: boolean;
  /** A message about the last turn, such as a reply that came too late. Gone after the next reply. */
  notice: string | null;
  /** The backend's weights once the interview is finished (live mode); null otherwise. */
  rubric: RubricItem[] | null;
  /** The words each criterion's question explained when it was asked, so a question reopened by "Go back" keeps them. */
  terms: Record<string, QuestionTerm[]>;
  /** What this interview remembers from earlier searches (US-06). */
  remembered: RememberedView[];
  /** The remembered preference being forgotten now; null otherwise. */
  forgetting: string | null;
  requestId: number;
}

export type InterviewAction =
  | { type: "CRITERIA_LOADING" }
  | { type: "CRITERIA_LOADED"; criteria: Criterion[]; fallback: boolean }
  | { type: "CRITERIA_FAILED"; message: string }
  | { type: "START" }
  | { type: "SEND"; text: string | null }
  | { type: "TURN_OK"; result: TurnResult }
  | { type: "TURN_FAILED"; message: string }
  | { type: "RETRY" }
  | { type: "GO_BACK"; criterionId: string }
  | { type: "FORGET"; preferenceId: string }
  | { type: "RESTORE"; state: Partial<InterviewState> };

export const initialInterview: InterviewState = {
  phase: "loading-criteria",
  criteria: [],
  fallback: false,
  answers: {},
  step: null,
  pending: null,
  revising: null,
  error: null,
  laterChanged: false,
  notice: null,
  rubric: null,
  terms: {},
  remembered: [],
  forgetting: null,
  requestId: 0,
};

export function interviewReducer(s: InterviewState, a: InterviewAction): InterviewState {
  switch (a.type) {
    case "CRITERIA_LOADING":
      return { ...s, phase: "loading-criteria", error: null };
    case "CRITERIA_LOADED":
      return {
        ...s,
        phase: "criteria-ready",
        criteria: a.criteria,
        fallback: a.fallback,
        rubric: null,
        error: null,
        terms: {}, // a new set of criteria: no explanation from the last one may be shown again
      };
    case "CRITERIA_FAILED":
      return { ...s, phase: "criteria-error", error: a.message };
    case "START":
      if (!s.criteria.length || s.phase === "starting") return s;
      if (s.step && s.phase !== "criteria-ready") return s;
      if (s.step) return { ...s, phase: s.step.kind === "finished" ? "finished" : "asking" };
      return { ...s, phase: "starting", pending: null, error: null, requestId: s.requestId + 1 };
    case "SEND": {
      // After a failed send the shopper may send something else: it replaces what "Try again" would send.
      if ((s.phase !== "asking" && s.phase !== "error") || !s.step?.criterionId) return s;
      const text = a.text === null ? null : a.text.trim().slice(0, MAX_ANSWER_LENGTH);
      if (text === "") return s;
      const id = s.step.criterionId;
      const { questionId } = s.step;
      return {
        ...s,
        phase: "sending",
        forgetting: null, // a reply sent after a failed Forget is the reply, never that forget again
        pending: {
          criterionId: id,
          text,
          revise: s.revising === id,
          ...(questionId ? { questionId } : {}),
        },
        error: null,
        laterChanged: false,
        notice: null,
        requestId: s.requestId + 1,
      };
    }
    case "TURN_OK": {
      if (s.phase !== "sending" && s.phase !== "starting") return s;
      const { step, answers, laterChanged } = a.result;
      const stillRevising =
        s.revising && step.criterionId === s.revising && step.kind !== "question"
          ? s.revising
          : null;
      return {
        ...s,
        phase: step.kind === "finished" ? "finished" : "asking",
        step,
        answers,
        pending: null,
        forgetting: null,
        revising: stillRevising,
        laterChanged,
        notice: a.result.notice ?? null,
        rubric: a.result.rubric ?? null,
        remembered: a.result.remembered
          ? keepDates(s.remembered, a.result.remembered)
          : s.remembered,
        terms:
          step.criterionId && step.terms.length
            ? { ...s.terms, [step.criterionId]: step.terms }
            : s.terms,
      };
    }
    case "TURN_FAILED":
      if (s.phase !== "sending" && s.phase !== "starting") return s;
      return { ...s, phase: "error", error: a.message };
    case "RETRY":
      if (s.phase !== "error") return s;
      return {
        ...s,
        phase: s.pending || s.forgetting ? "sending" : "starting",
        error: null,
        requestId: s.requestId + 1,
      };
    case "GO_BACK": {
      if (s.phase === "sending" || s.phase === "starting") return s;
      const c = s.criteria.find((x) => x.id === a.criterionId);
      if (!c) return s;
      return {
        ...s,
        phase: "asking",
        step: questionFor(c, s.terms[c.id] ?? []),
        revising: c.id,
        pending: null,
        forgetting: null,
        error: null,
        laterChanged: false,
        notice: null,
        rubric: null, // the answer is changing, so the finished weights no longer hold
      };
    }
    case "FORGET":
      // One click, whatever the screen shows: a question, the finished brief, or a failed reply.
      if (s.phase !== "asking" && s.phase !== "finished" && s.phase !== "error") return s;
      if (!s.remembered.some((r) => r.preferenceId === a.preferenceId)) return s;
      // A reply waiting to be tried again goes first: a forget now would drop it.
      if (s.pending) return s;
      return {
        ...s,
        phase: "sending",
        forgetting: a.preferenceId,
        pending: null,
        revising: null,
        error: null,
        laterChanged: false,
        notice: null,
        requestId: s.requestId + 1,
      };
    case "RESTORE":
      return { ...s, ...a.state };
  }
}

/**
 * The backend counts a remembered preference's age in whole days, so its day can come out one later or earlier as the
 * clock turns. The day first shown stays for the session, so the label never changes mid-interview.
 */
export function keepDates(before: RememberedView[], after: RememberedView[]): RememberedView[] {
  return after.map((r) => {
    const shown = before.find((b) => b.preferenceId === r.preferenceId);
    return shown ? { ...r, since: shown.since } : r;
  });
}

export type CriterionStatus = "answered" | "skipped" | "remembered" | "pending" | "not-asked";

export function statusOf(s: InterviewState, id: string): CriterionStatus {
  if (s.pending?.criterionId === id && (s.phase === "sending" || s.phase === "error"))
    return "pending";
  return s.answers[id]?.status ?? "not-asked";
}

export function counts(answers: Answers) {
  const v = Object.values(answers);
  return {
    answered: v.filter((x) => x.status === "answered").length,
    skipped: v.filter((x) => x.status === "skipped").length,
    remembered: v.filter((x) => x.status === "remembered").length,
  };
}

/** Stable snapshot for session storage: in-flight phases restore as their stable parent. */
export function toInterviewSnapshot(s: InterviewState): Partial<InterviewState> {
  let phase: Phase = s.phase;
  if (phase === "loading-criteria" || phase === "criteria-error")
    phase = s.criteria.length ? "criteria-ready" : "loading-criteria";
  if (phase === "starting") phase = "criteria-ready";
  // A forget sent from the finished brief restores as finished: there is no question to show.
  if (phase === "sending" || phase === "error")
    phase = s.step?.kind === "finished" ? "finished" : "asking";
  return {
    phase,
    criteria: s.criteria,
    fallback: s.fallback,
    answers: s.answers,
    step: s.step,
    revising: s.revising,
    rubric: s.rubric,
    terms: s.terms,
    remembered: s.remembered,
  };
}
