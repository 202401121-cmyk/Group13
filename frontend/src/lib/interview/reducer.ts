import { questionFor } from "./questions";
import {
  MAX_ANSWER_LENGTH,
  type Answers,
  type Criterion,
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
  /** The backend's weights once the interview is finished (live mode); null otherwise. */
  rubric: RubricItem[] | null;
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
  rubric: null,
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
      };
    case "CRITERIA_FAILED":
      return { ...s, phase: "criteria-error", error: a.message };
    case "START":
      if (!s.criteria.length || s.phase === "starting") return s;
      if (s.step && s.phase !== "criteria-ready") return s;
      if (s.step) return { ...s, phase: s.step.kind === "finished" ? "finished" : "asking" };
      return { ...s, phase: "starting", pending: null, error: null, requestId: s.requestId + 1 };
    case "SEND": {
      if (s.phase !== "asking" || !s.step?.criterionId) return s;
      const text = a.text === null ? null : a.text.trim().slice(0, MAX_ANSWER_LENGTH);
      if (text === "") return s;
      const id = s.step.criterionId;
      return {
        ...s,
        phase: "sending",
        pending: { criterionId: id, text, revise: s.revising === id },
        error: null,
        laterChanged: false,
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
        revising: stillRevising,
        laterChanged,
        rubric: a.result.rubric ?? null,
      };
    }
    case "TURN_FAILED":
      if (s.phase !== "sending" && s.phase !== "starting") return s;
      return { ...s, phase: "error", error: a.message };
    case "RETRY":
      if (s.phase !== "error") return s;
      return {
        ...s,
        phase: s.pending ? "sending" : "starting",
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
        step: questionFor(c),
        revising: c.id,
        pending: null,
        error: null,
        laterChanged: false,
        rubric: null, // the answer is changing, so the finished weights no longer hold
      };
    }
    case "RESTORE":
      return { ...s, ...a.state };
  }
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
  if (phase === "sending" || phase === "error") phase = "asking";
  return {
    phase,
    criteria: s.criteria,
    fallback: s.fallback,
    answers: s.answers,
    step: s.step,
    revising: s.revising,
    rubric: s.rubric,
  };
}
