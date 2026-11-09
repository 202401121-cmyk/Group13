/**
 * Frontend view of a research run: what the screens can render. The contract's types stay in
 * liveAdapter.ts, the one place that maps between the two. Every optional field is genuinely
 * optional, and the UI omits it when missing.
 */

export type WeightOrigin = "answer" | "standard" | "remembered";

export interface RubricItem {
  criterionId: string;
  title: string;
  /** Raw (unnormalised) weight from the rubric. */
  weight: number;
  origin?: WeightOrigin | undefined;
}

export type ProgressStatus = "started" | "done" | "fallback" | "failed";

/** One thing that happened in a run. */
export interface ProgressEvent {
  /** Counts up from 1 within a run. Order and repeats are decided by this, never by arrival. */
  id: number;
  stage: string;
  status: ProgressStatus;
  message: string;
  /** Sources found so far, per source type. */
  sources: Record<string, number>;
}

/** A run as the backend has it now. Queued counts as running, and partial results as done. */
export interface RunSnapshot {
  searchId: string;
  status: "running" | "done" | "failed" | "cancelled";
  /** When the run began, in ms since 1970. */
  startedAt: number;
  events: ProgressEvent[];
  /** Set exactly when the status is "done". */
  result: ResearchResult | null;
}

export type ValueState = "verified" | "estimated" | "unknown" | "disputed";

export interface CriterionValue {
  criterionId: string;
  label: string;
  state: ValueState;
  value: string | null;
  /** For disputed values: what each source said. */
  sources?: { source: string; value: string }[];
}

export interface Contribution {
  criterionId: string;
  label: string;
  /** Points this criterion added to the score. */
  points: number;
}

export interface Offer {
  store: string;
  price: number | null;
  /** ISO timestamp of the price check. */
  checkedAt: string | null;
  /** Always from isPriceStale(), never set by hand. Null when the as-of time is missing. */
  stale: boolean | null;
  url: string | null;
}

export type Confidence = "high" | "medium" | "low";

export interface ProductResult {
  id: string;
  name: string;
  imageUrl?: string | null;
  rank: number | null;
  /** Title of the criterion that separates this product from those it is tied with. */
  separatingCriterion?: string | null;
  score?: number | null;
  confidence?: Confidence | null;
  chanceFirst?: number | null;
  coverage?: { checked: number; total: number } | null;
  /** Whole percent of the score backed by real evidence (the contract's RankedProduct.coverage). */
  dataCoverage?: number | null;
  explanation?: string | null;
  contributions?: Contribution[];
  criteria: CriterionValue[];
  offers: Offer[];
  /** With no offer: a store search for this product (the contract's commerce.no_offer). */
  storeSearchUrl?: string | null;
  value?: { calculated: true; text: string } | { calculated: false; reason?: string | null } | null;
}

export interface ExcludedProduct {
  name: string;
  reason: string;
  /** In "Couldn't check": the criterion that couldn't be confirmed, when the result names one. */
  criterion?: string | null;
}

export interface ResearchResult {
  /** Ranked, eligible products (best first). */
  products: ProductResult[];
  leftOut: ExcludedProduct[];
  couldntCheck: ExcludedProduct[];
  coverageNote?: string | null;
  partial?: boolean;
  relaxation?: { hint: string; mustHave: string } | null;
  pricesCheckedAt?: string | null;
  saved: boolean;
  /** "mock" results must always be labelled as a preview in the UI. */
  source: "mock" | "live";
}

export type ResearchErrorKind = "network" | "server" | "unknown" | "busy" | "notFound";

export class ResearchError extends Error {
  constructor(
    public kind: ResearchErrorKind,
    message: string,
  ) {
    super(message);
  }
}

export interface ResearchInput {
  sessionId: string;
  category: string;
  rubric: RubricItem[];
  mustHaves: string[];
  exclusions: string[];
  scenario?: string | undefined;
}

export interface FollowHandlers {
  onEvent(event: ProgressEvent): void;
  /** The run has ended. Nothing more is called after this. */
  onEnd(run: RunSnapshot): void;
  /** Following it failed for good. Nothing more is called after this. */
  onError(error: unknown): void;
}

/** "stopping": it will end soon as cancelled. "tooLate": it will finish anyway. "stopped": it already had. */
export type CancelOutcome = "stopping" | "tooLate" | "stopped";

export interface ResearchService {
  isMock: boolean;
  /** Starts a run and gives its search ID. */
  start(input: ResearchInput): Promise<string>;
  /** The run's state now. */
  read(searchId: string, input: ResearchInput): Promise<RunSnapshot>;
  /** Follows the run until it ends. Gives back the function that stops following. */
  follow(searchId: string, input: ResearchInput, handlers: FollowHandlers): () => void;
  /** Asks the run to stop. */
  cancel(searchId: string): Promise<CancelOutcome>;
}

export const RESEARCH_TIMEOUT_MS = 170_000;
/** With no event for this long, the screen says so and offers to read the result by search ID. */
export const QUIET_AFTER_MS = 60_000;
