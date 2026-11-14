import type { Brief } from "@/lib/request/types";
import type { RubricItem, WeightOrigin } from "@/lib/results/types";

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

/** What can be left out (US-10). */
export type ExclusionKind = "brand" | "model" | "feature" | "seller";

/** A must-have the shopper said, in their own words (US-09). Switched off, it is a preference and filters nothing. */
export interface RuleView {
  id: string;
  words: string;
  active: boolean;
}

/** One preference: its weight from 0 to 10, and its share of all the weights (0 to 1). */
export interface WeightView {
  key: string;
  title: string;
  weight: number;
  share: number;
  origin?: WeightOrigin | undefined;
}

export interface ExclusionView {
  kind: ExclusionKind;
  value: string;
  /** Kept for later searches once sign-in exists; a guest's is never used again (D-36, US-30). */
  always: boolean;
}

/** The rubric as the editor shows it: must-haves, preferences and leave-outs, which never mix (DR-08). */
export interface EditableRubric {
  rules: RuleView[];
  weights: WeightView[];
  exclusions: ExclusionView[];
}

/** One change the shopper makes; saved through the rubric route, in order (docs/frontend-api.md). */
export type RubricEditView =
  | { kind: "switch_rule"; ruleId: string; active: boolean }
  | { kind: "set_weight"; key: string; weight: number }
  | { kind: "add_exclusion"; exclusion: ExclusionView }
  | { kind: "remove_exclusion"; exclusion: ExclusionView };

/** The rubric rebuilt with the edits, and a sentence to show beside the weights, if the backend sent one. */
export interface EditAnswer {
  rubric: EditableRubric;
  note: string | null;
}

export interface ParsedExclusions {
  exclusions: ExclusionView[];
  /** What couldn't be read as a leave-out, as typed, to show back so the shopper can rephrase it. */
  notUnderstood: string[];
}

/** What the brief page already knows: the mock builds its rubric from it, and live mode takes the titles. */
export interface RubricSeed {
  brief: Brief;
  items: RubricItem[];
}

/** Swap the mock for a real backend by implementing this interface. */
export interface InterviewService {
  getCriteria(brief: Brief, sessionId?: string): Promise<CriteriaResult>;
  turn(input: TurnInput): Promise<TurnResult>;
  /** The rubric to edit, or null when there is none yet (live mode: until the interview is finished). */
  loadRubric(sessionId: string, seed: RubricSeed): Promise<EditableRubric | null>;
  editRubric(sessionId: string, edits: RubricEditView[]): Promise<EditAnswer>;
  /** Reads leave-outs from the shopper's words, such as "no Xiaomi" (US-10). Changes nothing. */
  parseExclusions(text: string): Promise<ParsedExclusions>;
}

export const MAX_ANSWER_LENGTH = 500;
export const ANSWER_COUNTER_FROM = 450;
export const REQUEST_TIMEOUT_MS = 12_000;
