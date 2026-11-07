/**
 * Frontend view of a research run. No backend contract for research exists in
 * this project yet, so these types describe what the UI can render; every
 * optional field is genuinely optional and the UI omits it when missing.
 */

export type WeightOrigin = "answer" | "standard" | "remembered";

export interface RubricItem {
  criterionId: string;
  title: string;
  /** Raw (unnormalised) weight from the rubric. */
  weight: number;
  origin?: WeightOrigin | undefined;
}

/** The four research stages, in order. */
export const RESEARCH_STAGES = [
  "Finding products that fit your must-haves",
  "Checking prices across stores",
  "Reading specs and reviews",
  "Scoring each option against your brief",
] as const;

export interface ProgressEvent {
  type: "stage_done";
  /** Index into RESEARCH_STAGES. */
  stage: number;
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

export type ResearchErrorKind = "network" | "server" | "unknown";

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

export interface ResearchService {
  /** True when the service emits ProgressEvents; otherwise the UI shows no ticks. */
  streamsProgress: boolean;
  isMock: boolean;
  run(
    input: ResearchInput,
    opts: { signal: AbortSignal; onEvent: (e: ProgressEvent) => void },
  ): Promise<ResearchResult>;
}

export const RESEARCH_TIMEOUT_MS = 170_000;
