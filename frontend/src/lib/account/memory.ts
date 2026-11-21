/**
 * What ShopSense remembers for a signed-in shopper (US-06), and the controls over it (US-11): see every item, change a
 * weight's words or weight, switch a must-have off or on, download everything, forget one item or everything, and
 * delete the account. The backend's routes: docs/frontend-api.md, "What the app remembers, and deleting the account".
 *
 * Every route answers only for the shopper's own token: another shopper's preference and one already gone both answer
 * 404, so the screen refreshes its list rather than guessing which.
 */

export type RememberedKind = "weight" | "hard_rule" | "exclusion";

/** One remembered item, as the screen shows it. Never an embedding, a user id or an email: the backend sends none. */
export interface RememberedItem {
  id: string;
  kind: RememberedKind;
  /** The kind of product it holds for, as a key ("air_fryer"); null for a brand left out in every search. */
  category: string | null;
  /** What the shopper told us, in plain words (at most 200 characters). */
  text: string;
  /** A weight's 0 to 10; null for the other kinds. */
  weight: number | null;
  /** A must-have: true while it filters results, false once switched off; null for the other kinds. */
  active: boolean | null;
  /** When it was first saved and last changed, ms since the epoch. */
  createdAt: number;
  updatedAt: number;
}

/** A change to one item. Only a weight's words and weight can change, and only a must-have can be switched. */
export interface MemoryEdit {
  text?: string;
  weight?: number;
  active?: boolean;
}

export interface Edited {
  item: RememberedItem;
  /** The backend's note when new words were saved but couldn't be matched yet (X-Memory-Note); else null. */
  note: string | null;
}

/** The export, ready to save: its file name and its JSON text. */
export interface MemoryFile {
  name: string;
  text: string;
}

/**
 * A forget waiting for its Undo. Its sign-in is read when Forget is pressed, so the delete can still go as the page
 * goes: a sign-out or a closed tab within the 5 seconds, when the sign-in itself may already be gone.
 */
export interface HeldForget {
  /** Sends the delete now, as forget() does. */
  send(): Promise<void>;
  /** Sends the delete as the page goes: at once, with the sign-in read earlier, and kept going past the page. */
  sendAsPageGoes(): void;
}

export interface MemoryService {
  readonly isMock: boolean;
  /** Everything remembered, newest first. */
  list(): Promise<RememberedItem[]>;
  edit(id: string, edit: MemoryEdit): Promise<Edited>;
  /** Deletes one item, and its embedding with it. */
  forget(id: string): Promise<void>;
  /** A forget to be sent after the Undo, or as the page goes. Nothing is sent yet. */
  holdForget(id: string): HeldForget;
  /** Deletes everything remembered. The account and its searches stay. */
  forgetAll(): Promise<void>;
  download(): Promise<MemoryFile>;
  /** Deletes the account and everything saved with it. `typed` must be the word "delete", or nothing is sent. */
  deleteAccount(typed: string): Promise<void>;
}

export type MemoryErrorKind =
  "signedOut" | "notFound" | "changed" | "invalid" | "unavailable" | "network" | "unknown";

export const MEMORY_MESSAGES = {
  signedOut: "Please sign in to see what ShopSense remembers about you.",
  notFound: "That preference was already gone, so we refreshed the list.",
  changed: "This preference changed while you were editing it. Please look at it again and retry.",
  invalid: "That change can't be made. Please check it and try again.",
  unavailable:
    "What ShopSense remembers can't be reached right now. Searching still works. Please try again in a minute.",
  network: "We couldn't reach ShopSense. Check your connection and try again.",
  unknown: "Something went wrong. Please try again.",
} as const satisfies Record<MemoryErrorKind, string>;

export class MemoryError extends Error {
  constructor(
    public readonly kind: MemoryErrorKind,
    message: string = MEMORY_MESSAGES[kind],
  ) {
    super(message);
    this.name = "MemoryError";
  }
}

/** The word that confirms deleting the account, as the backend reads it: spaces around it and capitals don't count. */
export const CONFIRM_WORD = "delete";
export const isConfirmWord = (typed: string): boolean =>
  typed.trim().toLowerCase() === CONFIRM_WORD;
/** The backend's own sentence for a missing or wrong word (backend/app/api/account.py CONFIRM_FIRST). */
export const CONFIRM_FIRST =
  'To delete your account, type "delete" to confirm. This can\'t be undone.';

/**
 * Set in this tab once an account is deleted, just before the sign-out: the confirmation page says "deleted" only
 * then. An auth key, so the sign-out's reset of this tab's searches (D-80) keeps it.
 */
export const DELETED_KEY = "shopsense.auth.deleted";

/** The export's file name, as the backend's Content-Disposition gives it. */
export const EXPORT_FILE = "shopsense-memory.json";

/** How long "Undo" stays after Forget, before the delete is sent. */
export const UNDO_MS = 5_000;

/** A weight's words: 1 to 200 characters, as PreferenceEdit allows. */
export const TEXT_MAX = 200;
export const WEIGHT_MIN = 0;
export const WEIGHT_MAX = 10;

/** A preference's id as the backend makes them: a UUID. Anything else can't be anyone's, so it is never sent. */
export const PREFERENCE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
