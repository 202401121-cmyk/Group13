/**
 * Frontend view of a research run: what the screens can render. The contract's types stay in
 * liveAdapter.ts, the one place that maps between the two. Every optional field is genuinely
 * optional, and the UI omits it when missing.
 */

/** Where a weight came from; "manual" is one the shopper set in the rubric editor (US-09). */
export type WeightOrigin = "answer" | "standard" | "remembered" | "manual";

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

/** One sentence of a product's explanation, with the claims it rests on. */
export interface ExplanationPart {
  text: string;
  claims: ClaimView[];
  /** Whether a check found the sentence in its sources: false is "not confirmed", null not checked. */
  supported?: boolean | null;
}

/** How the explanation was written (US-20): checked or unchecked by the LLM, or code's template when it failed. */
export type ExplanationMethod = "llm_checked" | "llm_unchecked" | "template";

/** How a criterion's score was made (US-18): a source's opinion, compared with the other products, or estimated. */
export type ScoreBasis = "opinion" | "compared" | "estimated";

/** One of a product's main reasons: a criterion that added the most points to its score. */
export interface ReasonView {
  criterionId: string;
  label: string;
  state: ValueState;
  /** Points added to the score. Null for an unknown value, which is never shown as 0. */
  points: number | null;
  claims: ClaimView[];
  /** How its score was made, when the result says. */
  basis?: ScoreBasis | null;
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
  /** "unknown" says nothing, so the card shows nothing for it. */
  availability?: "in_stock" | "out_of_stock" | "unknown";
  /** The price is above the shopper's budget. */
  overBudget?: boolean;
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
  /** The one trade-off, on its own line. */
  tradeOff?: string | null;
  /** Criteria the explanation could not verify, by title. */
  unverified?: string[];
  explanationMethod?: ExplanationMethod | null;
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
  /** With no offer: a price a source gave for it (its price_inr value with a claim), never a buy link. */
  sourcedPrice?: SourcedPrice | null;
  value?: { calculated: true; text: string } | { calculated: false; reason?: string | null } | null;
}

/** A price a source gave (US-24, S4-30): shown apart from store offers, with the claims it rests on. */
export interface SourcedPrice {
  /** "₹28,980", or the source's own words when the value isn't a plain number. */
  text: string;
  /** The site of the first source that gives it, such as "91mobiles.com". */
  site: string;
  /** The day that source was read, such as "15 Nov"; null when unknown. */
  seen: string | null;
  /** "verified", or "disputed" when other sources give another price. */
  state: ValueState;
  claims: ClaimView[];
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
  /** Every LLM provider failed (US-31): the page says so plainly and offers to try again. */
  llmUnavailable?: boolean;
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
