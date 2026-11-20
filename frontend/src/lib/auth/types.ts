/**
 * Signing in (US-30): Google through Supabase Auth, in the browser, with the project's publishable key (D-32).
 *
 * The screens see only an AuthState and the AuthService below. The access token never reaches a screen: the API
 * client asks for it on each call (bearerSource), and nothing writes it to a URL, a log or storage of ours. supabase-js
 * keeps the session itself.
 */

/** Who is signed in: the Supabase user's id, and a name to show (none from us is ever stored, D-36). */
export interface AuthUser {
  id: string;
  /** The name Google gave, if any. The screen falls back to "Your account". */
  name: string | null;
}

export type AuthState =
  | { status: "loading" }
  | { status: "unavailable" } // live mode with no Supabase settings: no "Sign in", everyone is a guest
  | { status: "guest"; error?: string | undefined }
  | { status: "signedIn"; user: AuthUser };

export interface AuthService {
  readonly isMock: boolean;
  current(): AuthState;
  subscribe(listener: (state: AuthState) => void): () => void;
  /** Starts reading the session, in the browser only. Safe to call more than once. */
  start(): void;
  /** Sends the shopper to Google; they come back to `returnTo`, a path on this site. */
  signIn(returnTo: string): Promise<void>;
  /** Signs out, then loads `then` afresh, so nothing of the signed-in session stays in memory (D-80). */
  signOut(then: string): Promise<void>;
  /** The current access token, refreshed by supabase-js when it has expired. Null for a guest. */
  accessToken(): Promise<string | null>;
  /** A token refreshed now, for one retry after a 404 on a signed-in shopper's run (S4-10). Null if none. */
  refreshedToken(): Promise<string | null>;
  /** Drops a session the backend no longer accepts (401), so the shopper goes on as a guest. */
  dropSession(): Promise<void>;
}

export const SIGN_IN_FAILED = "We couldn't sign you in with Google. Please try again.";
export const SIGN_OUT_FAILED = "We couldn't sign you out. Please try again.";
export const SESSION_ENDED =
  "Your sign-in has ended. Please sign in again to see your saved searches.";

/** A call made while the session couldn't be read: it fails rather than go as a guest's (D-80). */
export class SignInNotReady extends Error {
  constructor() {
    super("We couldn't check your sign-in. Please try again.");
    this.name = "SignInNotReady";
  }
}

/** A path on this site to come back to, or "/": never another site, and never a search's own pages (D-80). */
export function returnPath(full: string): string {
  // Never a fragment: going to "/#how" from "/" would only scroll, and the page must load afresh (D-80).
  const path = full.split("#")[0] ?? "";
  if (!path.startsWith("/") || path.startsWith("//") || path.startsWith("/\\")) return "/";
  // A sign-in or a sign-out ends the search in progress (D-80), so its own pages have nothing to show afterwards.
  if (/^\/(s\/|research|criteria)/.test(path)) return "/";
  return path;
}
