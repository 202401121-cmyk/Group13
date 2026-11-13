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

/** Where a claim came from, ready to show. */
export interface SourceRef {
  id: string;
  /** The site, the community (such as "r/..."), or the page title. */
  name: string;
  /** Plain words for the kind of source, such as "Review site" or "Reddit". */
  typeLabel: string;
  url: string;
  /** "Published 3 Oct 2026", or "Checked 3 Oct 2026" when the publish date isn't known. */
  dateLabel: string | null;
}

/** One claim behind a value: the source's own words, or why they aren't shown. */
export interface ClaimView {
  claimId: string;
  /** Supporting claims back the value shown; conflicting ones are the other side of a disputed value. */
  side: "supporting" | "conflicting";
  /** The exact words from the source. Null when they weren't kept. */
  quote: string | null;
  /** Set exactly when the quote is null: why it isn't shown. */
  quoteWithheld: string | null;
  source: SourceRef;
  /** What this source said the value is, when it is known. */
  valueText?: string | null;
}

export interface CriterionValue {
  /** The criterion's key, which the evidence route takes as criterion_key. */
  criterionId: string;
  label: string;
  state: ValueState;
  value: string | null;
  /** For disputed values: what each source said. */
  sources?: { source: string; value: string }[];
  /** The claims this value rests on, supporting first. Missing or empty when no source gave it. */
  claims?: ClaimView[];
}

/** One sentence of a product's explanation, with the claims it rests on (none for the trade-off). */
export interface ExplanationPart {
  text: string;
  claims: ClaimView[];
}

/** One of a product's main reasons: a criterion that added the most points to its score. */
export interface ReasonView {
  criterionId: string;
  label: string;
  state: ValueState;
  /** Points added to the score. Null for an unknown value, which is never shown as 0. */
  points: number | null;
  claims: ClaimView[];
}

/** What "Where this comes from" shows for one product and criterion. */
export interface EvidenceView {
  /** The value's state as the evidence route gives it. Null when the route wasn't used: the card's state stands. */
  state: ValueState | null;
  claims: ClaimView[];
  /** Why some quotes are missing, when the route says. */
  withheldReason: string | null;
  /** "route": the evidence route answered. "citations": built from the result's own citations instead. */
  from: "route" | "citations";
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
  /** The product's ID, which the evidence route takes as product_id. */
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
  /** The explanation sentence by sentence, each with its sources. Its texts joined are `explanation`. */
  explanationParts?: ExplanationPart[];
  contributions?: Contribution[];
  /** The three criteria that added the most points, largest first. */
  reasons?: ReasonView[];
  /** "best_match" or "runner_up" when the result names one. */
  label?: "best_match" | "runner_up" | null;
  /** Score points behind the top product; 0 for the top product. */
  gapToLeader?: number | null;
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
  /** The run this result came from, which the evidence route needs. */
  searchId?: string | null;
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
  /**
   * Where one product's value for one criterion comes from. Never fails: when the answer can't be had, it gives
   * back `fallback` (the claims the result already carries) with `from: "citations"`.
   */
  evidence(
    searchId: string | null,
    productId: string,
    criterionId: string,
    fallback: ClaimView[],
  ): Promise<EvidenceView>;
}

export const RESEARCH_TIMEOUT_MS = 170_000;
/** With no event for this long, the screen says so and offers to read the result by search ID. */
export const QUIET_AFTER_MS = 60_000;
