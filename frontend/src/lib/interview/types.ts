import type { Brief } from "@/lib/request/types";
import type { RubricItem } from "@/lib/results/types";

export interface Criterion {
  id: string;
  title: string;
  definition: string;
  lowerIsBetter?: boolean;
  valueForMoney?: boolean;
  /** Extra context used when the shopper asks about this criterion. */
  tip?: string;
  /** If this criterion's answer depends on another, it is re-asked when that one changes. */
  dependsOn?: string;
}

export interface CriteriaResult {
  criteria: Criterion[];
  fallback: boolean;
}

export type AnswerStatus = "answered" | "skipped" | "remembered";
export interface AnswerRecord {
  status: AnswerStatus;
  value: string | null;
}
export type Answers = Record<string, AnswerRecord>;

export type StepKind = "question" | "answer_then_repeat" | "rephrase" | "redirect" | "finished";

export interface Step {
  kind: StepKind;
  criterionId: string | null;
  question: string | null;
  note: string | null;
  why: string | null;
  replies: string[];
}

export interface Turn {
  criterionId: string;
  /** null = skipped */
  text: string | null;
  revise: boolean;
}

export interface TurnInput {
  brief: Brief;
  criteria: Criterion[];
  answers: Answers;
  turn?: Turn | undefined;
  /** The session the backend keeps this interview in (live mode). */
  sessionId?: string;
}

export interface TurnResult {
  step: Step;
  answers: Answers;
  laterChanged: boolean;
  /** The weights the backend worked out, sent with the finished step (live mode only). */
  rubric?: RubricItem[];
}

/** Swap the mock for a real backend by implementing this interface. */
export interface InterviewService {
  getCriteria(brief: Brief, sessionId?: string): Promise<CriteriaResult>;
  turn(input: TurnInput): Promise<TurnResult>;
}

export const MAX_ANSWER_LENGTH = 500;
export const ANSWER_COUNTER_FROM = 450;
export const REQUEST_TIMEOUT_MS = 12_000;
