export interface Budget {
  min: number | null;
  max: number | null;
}

export interface Brief {
  product: string | null;
  usedFor: string | null;
  budget: Budget | null;
  region: string | null;
  mustHaves: string[];
  exclusions: string[];
  /** "fallback" when the rules-based backup reader produced the brief. */
  source: "model" | "fallback";
}

export type FieldKey = "product" | "usedFor" | "budget" | "region" | "mustHaves" | "exclusions";

export interface FollowUp {
  question: string;
  options: string[];
}

export type UnderstandResponse = (
  | { kind: "understood"; brief: Brief }
  | { kind: "follow-up"; followUp: FollowUp }
  | { kind: "examples" }
) & {
  /** The session this request belongs to: the backend's in live mode; the mock makes one in the same format. */
  sessionId?: string;
};

export interface UnderstandInput {
  text: string;
  clarification?: string;
  /** The session of the request being clarified. Left out for a new request, which starts a new session. */
  sessionId?: string;
}

/** What saveBrief needs to send only the fields that changed, to the right session. */
export interface SaveContext {
  sessionId: string | null;
  previous: Brief | null;
}

/** Swap the mock adapter for a real backend by implementing this interface. */
export interface RequestService {
  understand(input: UnderstandInput): Promise<UnderstandResponse>;
  saveBrief(brief: Brief, context?: SaveContext): Promise<Brief>;
}

export const MAX_REQUEST_LENGTH = 1000;

export const EXAMPLE_REQUESTS = [
  "Laptop for coding under ₹70,000",
  "Earbuds for the gym under ₹3,000",
  "Pram for a newborn",
  "Phone with a good camera under ₹30,000",
] as const;
