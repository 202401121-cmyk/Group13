/** Stand-ins for sign-in in tests: a token source, and a fake supabase-js auth client. Never a real account. */
import type { BearerSource } from "@/lib/api/client";
import type { SessionLike, SupabaseAuthLike } from "@/lib/auth/liveService";

/** A token source that gives `token`, then `fresh` once refreshed, and counts the refreshes. */
export function fakeBearer(token: string | null, fresh: string | null = "fresh-token") {
  const counts = { token: 0, refreshed: 0 };
  const bearer: BearerSource = {
    token: async () => {
      counts.token++;
      return token;
    },
    refreshed: async () => {
      counts.refreshed++;
      return fresh;
    },
  };
  return { bearer, counts };
}

export function session(id = "a0000000-0000-4000-8000-00000000000a", name?: string): SessionLike {
  return {
    access_token: `token-for-${id}`,
    user: { id, user_metadata: name ? { full_name: name } : {} },
  };
}

/** A supabase-js auth client with a session the test controls, recording what was asked of it. */
export function fakeSupabase(initial: SessionLike | null = null) {
  let current = initial;
  let listener: ((event: string, s: SessionLike | null) => void) | null = null;
  const calls = {
    signIn: [] as { redirectTo: string }[],
    signOut: [] as (string | undefined)[],
    refresh: 0,
  };
  const errors = { signIn: null as unknown, signOut: null as unknown, refresh: null as unknown };
  const auth: SupabaseAuthLike = {
    getSession: async () => ({ data: { session: current }, error: null }),
    refreshSession: async () => {
      calls.refresh++;
      if (errors.refresh) return { data: { session: null }, error: errors.refresh };
      current = current ? { ...current, access_token: `${current.access_token}-refreshed` } : null;
      return { data: { session: current }, error: null };
    },
    onAuthStateChange: (callback) => {
      listener = callback;
      return { data: { subscription: { unsubscribe: () => undefined } } };
    },
    signInWithOAuth: async ({ options }) => {
      calls.signIn.push({ redirectTo: options.redirectTo });
      return { error: errors.signIn };
    },
    signOut: async (args) => {
      calls.signOut.push(args?.scope);
      const error = args?.scope === "local" ? null : errors.signOut;
      if (!error) {
        current = null;
        listener?.("SIGNED_OUT", null); // as supabase-js does, during signOut()
      }
      return { error };
    },
  };
  return {
    auth,
    calls,
    errors,
    /** What supabase-js says when the session changes: signed in, refreshed, or signed out (an expired one too). */
    emit(event: string, next: SessionLike | null) {
      current = next;
      listener?.(event, next);
    },
  };
}

/** A tab's sessionStorage stand-in, empty. */
export function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k) => map.get(k) ?? null,
    key: (i) => [...map.keys()][i] ?? null,
    removeItem: (k) => void map.delete(k),
    setItem: (k, v) => void map.set(k, String(v)),
  };
}
