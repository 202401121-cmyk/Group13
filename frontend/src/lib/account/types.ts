/**
 * A signed-in shopper's own data (US-30): who they are to the backend, and their saved searches.
 *
 * The backend answers these only with the shopper's token (GET /api/me, /api/me/searches, /api/me/searches/{id},
 * docs/frontend-api.md "Signing in"). Another shopper's saved search, and one that doesn't exist, both answer 404, so
 * the screen says only that it couldn't find it.
 */
import type { ResearchResult } from "@/lib/results/types";

/** One saved search in the list, newest first. */
export interface SavedSearchItem {
  id: string;
  /** When it was saved, ms since the epoch. */
  createdAt: number;
  /** What the shopper typed, at most 500 characters (a longer request ends with "…"). */
  request: string;
  category: string | null;
  /** The product that came first, when the ranking scored one. */
  topProduct: string | null;
}

export interface SavedPage {
  items: SavedSearchItem[];
  /** Ask with it for the next page; null on the last. */
  nextCursor: string | null;
}

/** One saved search, opened: the result it finished with. */
export interface SavedSearch {
  id: string;
  request: string;
  category: string;
  /** When the search finished, ms since the epoch. */
  finishedAt: number;
  result: ResearchResult;
}

export interface Me {
  signedIn: boolean;
  displayName: string | null;
}

export interface AccountService {
  readonly isMock: boolean;
  me(): Promise<Me>;
  searches(cursor?: string | null): Promise<SavedPage>;
  search(id: string): Promise<SavedSearch>;
}

export type AccountErrorKind = "signedOut" | "notFound" | "unavailable" | "network" | "unknown";

export const ACCOUNT_MESSAGES = {
  signedOut: "Please sign in to see your saved searches.",
  notFound: "We couldn't find that saved search.",
  unavailable:
    "Your saved searches can't be reached right now. Searching still works; please try again in a minute.",
  network: "We couldn't reach ShopSense. Check your connection and try again.",
  unknown: "Something went wrong. Please try again.",
} as const satisfies Record<AccountErrorKind, string>;

export class AccountError extends Error {
  constructor(
    public readonly kind: AccountErrorKind,
    message: string = ACCOUNT_MESSAGES[kind],
  ) {
    super(message);
    this.name = "AccountError";
  }
}

/** A saved search's id as the backend makes them: a UUID. Anything else can't be anyone's. */
export const SAVED_SEARCH_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Page size, as the backend sends them. */
export const PAGE_SIZE = 20;
