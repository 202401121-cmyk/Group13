/**
 * D-80 (20 Nov 2026): a sign-in or a sign-out starts a new session ID, so a guest's rows and an account's rows never
 * share one. The next request goes through intake again.
 *
 * This tab remembers who it last searched as (WHO_KEY). When that changes, guest to signed in or back, every search
 * kept in this tab goes: the session ID, the brief and the answers, the request being written, a mock run. The next
 * request then starts with a new session ID from intake. A token refresh, or the same user signing in again, changes
 * nothing.
 */

/** Who this tab last searched as: a user id, or GUEST. Kept in this tab only. */
export const WHO_KEY = "shopsense.auth.who";
export const GUEST = "guest";

/** Every key of ours in this tab that belongs to a search. The auth keys stay. */
function isSearchKey(key: string): boolean {
  return key.startsWith("shopsense.") && !key.startsWith("shopsense.auth.");
}

/** Forgets every search kept in this tab, so the next request gets a new session ID. */
export function endSearches(storage: Storage = sessionStorage): void {
  const keys: string[] = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (key !== null && isSearchKey(key)) keys.push(key);
  }
  keys.forEach((key) => storage.removeItem(key));
}

/**
 * Notes who is searching now. Returns true when that changed from who this tab last searched as, and then every
 * search kept in the tab is gone. The first look in a fresh tab only notes it: there is nothing to end yet.
 */
export function noteWho(who: string, storage: Storage = sessionStorage): boolean {
  try {
    const before = storage.getItem(WHO_KEY);
    storage.setItem(WHO_KEY, who);
    if (before === null || before === who) return false;
    endSearches(storage);
    return true;
  } catch {
    return false; // storage unavailable: nothing is kept in it either
  }
}
