import { endSearches, GUEST, noteWho } from "./sessionReset";
import { SIGN_IN_FAILED, returnPath, type AuthState } from "./types";

/**
 * Set just before a page is loaded afresh because who is searching changed: a sign-in (before going to Google), a
 * sign-out, or a change seen in a page that was already open. The fresh page then ends the searches kept in this tab
 * once more, in case a reply that was still on its way wrote one back meanwhile, and knows it needs no reload of its
 * own. Kept with the auth keys, so ending the searches never removes it.
 */
export const RESET_KEY = "shopsense.auth.reset";

function markReset() {
  try {
    sessionStorage.setItem(RESET_KEY, "1");
  } catch {
    /* storage unavailable: nothing is kept in it either */
  }
}

/** True once, on the page a reset loaded: and then the tab's searches are ended again. */
function takeReset(): boolean {
  try {
    if (sessionStorage.getItem(RESET_KEY) === null) return false;
    sessionStorage.removeItem(RESET_KEY);
    endSearches();
    return true;
  } catch {
    return false;
  }
}

/**
 * A state holder both services share: the current state, and who is told when it changes.
 *
 * D-80: a change of who is searching ends the searches kept in this tab, and the page is loaded afresh (`whoChanged`)
 * so no search held in memory carries on under its old session ID either. That holds for a change seen once the page
 * was showing (a sign-in or sign-out in another tab, a session that ended) and for one found when the page first reads
 * the session (a tab restored after its shopper signed in elsewhere). The page a sign-in or sign-out started here
 * loads (`leave`, then the reset flag) needs no second load.
 */
export function stateHolder(initial: AuthState, whoChanged: () => void = () => undefined) {
  let state = initial;
  let settled = false;
  let leaving = false;
  const listeners = new Set<(s: AuthState) => void>();
  return {
    get: () => state,
    /** This page is about to be loaded afresh by a sign-in or sign-out started here. */
    leave() {
      leaving = true;
      markReset();
    },
    /** The sign-in didn't start after all: this page stays. */
    stay() {
      leaving = false;
      try {
        sessionStorage.removeItem(RESET_KEY);
      } catch {
        /* storage unavailable */
      }
    },
    set(next: AuthState) {
      state = next;
      const who =
        next.status === "guest" ? GUEST : next.status === "signedIn" ? next.user.id : null;
      const first = who !== null && !settled;
      const fresh = first && takeReset(); // the page a reset loaded
      const changed = who !== null && noteWho(who);
      if (who !== null) settled = true;
      listeners.forEach((listener) => listener(next));
      if (changed && !fresh && !leaving) {
        leaving = true;
        markReset();
        whoChanged();
      }
    },
    subscribe(listener: (s: AuthState) => void) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
  };
}

/** Loads the page the shopper is on afresh, or home if it was a search's own page (D-80). */
export function reloadHere(load: (path: string) => void, win?: Window): () => void {
  return () => {
    const here = (win ?? window).location;
    load(returnPath(here.pathname + here.search));
  };
}

const ERROR_PARAMS = ["error", "error_code", "error_description"];

/**
 * A sign-in that failed comes back with `error` in the address (in the query, or the fragment). Returns our own
 * sentence for it, never the text in the address, and takes those parameters out of the address so a reload
 * doesn't say it again.
 */
export function signInErrorFromAddress(win: Window = window): string | undefined {
  const url = new URL(win.location.href);
  const hash = new URLSearchParams(url.hash.replace(/^#/, ""));
  if (!url.searchParams.has("error") && !hash.has("error")) return undefined;
  for (const name of ERROR_PARAMS) {
    url.searchParams.delete(name);
    hash.delete(name);
  }
  const fragment = hash.toString();
  url.hash = fragment ? `#${fragment}` : "";
  win.history.replaceState(win.history.state, "", url.pathname + url.search + url.hash);
  return SIGN_IN_FAILED;
}

/** The name to show for a signed-in shopper: the one Google gave, if it is a usable string. */
export function nameFrom(metadata: unknown): string | null {
  if (typeof metadata !== "object" || metadata === null) return null;
  for (const field of ["full_name", "name"]) {
    const value = (metadata as Record<string, unknown>)[field];
    if (typeof value === "string" && value.trim()) return value.trim().slice(0, 80);
  }
  return null;
}
