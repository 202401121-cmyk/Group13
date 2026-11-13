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

/** A word in a question that a shopper may not know, explained in plain words (US-07). */
export interface QuestionTerm {
  term: string;
  explanation: string;
}

export type StepKind = "question" | "answer_then_repeat" | "rephrase" | "redirect" | "finished";

export interface Step {
  kind: StepKind;
  criterionId: string | null;
  /** The backend's id for this question (InterviewQuestion.id). The mock and a local "Go back" have none. */
  questionId?: string | null | undefined;
  question: string | null;
  note: string | null;
  why: string | null;
  replies: string[];
  /** Words in the question explained, behind "What does this mean?". Empty means none. */
  terms: QuestionTerm[];
}

export interface Turn {
  criterionId: string;
  /** The question this reply answers. A retry sends the same id, so it can never answer the next question. */
  questionId?: string | null | undefined;
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
  /** Something the shopper should be told about this turn, shown above the question until the next reply. */
  notice?: string;
}

/** Swap the mock for a real backend by implementing this interface. */
export interface InterviewService {
  getCriteria(brief: Brief, sessionId?: string): Promise<CriteriaResult>;
  turn(input: TurnInput): Promise<TurnResult>;
}

export const MAX_ANSWER_LENGTH = 500;
export const ANSWER_COUNTER_FROM = 450;
export const REQUEST_TIMEOUT_MS = 12_000;
