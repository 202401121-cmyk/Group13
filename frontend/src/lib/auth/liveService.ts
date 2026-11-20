/**
 * Live sign-in (US-30): supabase-js with the project's publishable key only (D-32), Google as the provider.
 *
 * - supabase-js is loaded on first use, in the browser only, so mock mode and the server never load it.
 * - It keeps the session itself (in this browser's storage, as supabase-js does) and refreshes the access token
 *   before it expires. Nothing here stores, logs or puts a token in an address.
 * - A session that can't be refreshed any more ends: supabase-js says so, and the shopper goes on as a guest.
 * - Sign-in uses the PKCE flow and comes back to the page the shopper was on. A sign-in that failed comes back with
 *   `error` in the address: the screen shows our own sentence for it.
 */
import { nameFrom, reloadHere, signInErrorFromAddress, stateHolder } from "./shared";
import {
  SESSION_ENDED,
  SIGN_IN_FAILED,
  SignInNotReady,
  returnPath,
  type AuthService,
  type AuthState,
} from "./types";

/** The part of a supabase-js session this needs. */
export interface SessionLike {
  access_token: string;
  user: {
    id: string;
    user_metadata?: unknown;
    email?: string | undefined;
    created_at?: string | undefined;
  };
}

interface SessionAnswer {
  data: { session: SessionLike | null };
  error: unknown;
}

/** The part of supabase-js's auth client this uses. Tests pass a fake. */
export interface SupabaseAuthLike {
  getSession(): Promise<SessionAnswer>;
  refreshSession(): Promise<SessionAnswer>;
  onAuthStateChange(callback: (event: string, session: SessionLike | null) => void): unknown;
  signInWithOAuth(args: {
    provider: "google";
    options: { redirectTo: string };
  }): Promise<{ error: unknown }>;
  signOut(args?: { scope?: "global" | "local" | "others" }): Promise<{ error: unknown }>;
}

export type MakeAuthClient = (url: string, publishableKey: string) => Promise<SupabaseAuthLike>;

/** supabase-js itself, loaded when first needed. */
export const supabaseAuthClient: MakeAuthClient = async (url, publishableKey) => {
  const { createClient } = await import("@supabase/supabase-js");
  const client = createClient(url, publishableKey, {
    auth: {
      flowType: "pkce",
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  });
  return client.auth as unknown as SupabaseAuthLike;
};

export interface LiveAuthOptions {
  url: string;
  publishableKey: string;
  makeClient?: MakeAuthClient;
  load?: (path: string) => void;
  win?: Window;
}

/**
 * How long a call waits for the session to be read at page load. Past it the call fails (SignInNotReady), never goes as
 * a guest's: a signed-in shopper's search must not be saved without its owner (D-80).
 */
export const READY_WAIT_MS = 10_000;

/** Set by dropSession, so the page loaded afresh says why the shopper is a guest now. */
export const ENDED_KEY = "shopsense.auth.ended";

function takeFlag(key: string): boolean {
  try {
    const had = sessionStorage.getItem(key) !== null;
    sessionStorage.removeItem(key);
    return had;
  } catch {
    return false;
  }
}

/** A PKCE sign-in comes back with ?code=; one supabase-js couldn't turn into a session failed. */
function takeCode(win: Window): boolean {
  const url = new URL(win.location.href);
  if (!url.searchParams.has("code")) return false;
  url.searchParams.delete("code");
  win.history.replaceState(win.history.state, "", url.pathname + url.search + url.hash);
  return true;
}

export function createLiveAuthService(options: LiveAuthOptions): AuthService {
  const makeClient = options.makeClient ?? supabaseAuthClient;
  const load = options.load ?? ((path: string) => window.location.assign(path));
  const holder = stateHolder({ status: "loading" }, reloadHere(load, options.win));
  let client: Promise<SupabaseAuthLike> | null = null;
  let firstError: string | undefined;

  const auth = () => (client ??= makeClient(options.url, options.publishableKey));
  let markReady: () => void = () => undefined;
  // Settled once the session has been read: a call made at page load waits for it, so a signed-in shopper's first
  // call carries their token.
  const ready = new Promise<void>((resolve) => (markReady = resolve));

  function apply(session: SessionLike | null) {
    const next: AuthState = session
      ? {
          status: "signedIn",
          user: {
            id: session.user.id,
            name: nameFrom(session.user.user_metadata),
            email: session.user.email ?? null,
            since: session.user.created_at ? Date.parse(session.user.created_at) || null : null,
          },
        }
      : { status: "guest", error: firstError };
    firstError = undefined; // said once
    holder.set(next);
    markReady();
  }

  function start() {
    if (client) return;
    const win = options.win ?? window;
    firstError = signInErrorFromAddress(win) ?? (takeFlag(ENDED_KEY) ? SESSION_ENDED : undefined);
    const hadCode = new URL(win.location.href).searchParams.has("code");
    auth().then(
      async (a) => {
        // Signed in, refreshed, signed out (here or in another tab), or a session that could not be refreshed.
        a.onAuthStateChange((_event, session) => apply(session));
        let session: SessionLike | null = null;
        try {
          session = (await a.getSession()).data.session;
        } catch {
          /* unreadable: a guest, below */
        }
        // A code that didn't become a session: the sign-in failed (an expired or reused code, say).
        if (!session && hadCode && takeCode(win)) firstError ??= SIGN_IN_FAILED;
        apply(session);
      },
      () => {
        holder.set({ status: "guest", error: firstError });
        markReady();
      },
    );
  }

  async function token(
    read: (a: SupabaseAuthLike) => Promise<SessionAnswer>,
  ): Promise<string | null> {
    start();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const waited = new Promise<void>((resolve) => (timer = setTimeout(resolve, READY_WAIT_MS)));
    await Promise.race([ready, waited]);
    clearTimeout(timer);
    if (holder.get().status === "loading") throw new SignInNotReady();
    if (holder.get().status !== "signedIn") return null;
    try {
      const { data } = await read(await auth());
      return data.session?.access_token ?? null;
    } catch {
      return null;
    }
  }

  return {
    isMock: false,
    current: holder.get,
    subscribe: holder.subscribe,
    start,
    async signIn(returnTo) {
      holder.leave(); // the page Google sends back to is a fresh one (D-80)
      try {
        const win = options.win ?? window;
        const { error } = await (
          await auth()
        ).signInWithOAuth({
          provider: "google",
          options: { redirectTo: win.location.origin + returnPath(returnTo) },
        });
        if (!error) return;
      } catch {
        /* said below */
      }
      holder.stay();
      holder.set({ status: "guest", error: SIGN_IN_FAILED });
    },
    async signOut(then) {
      holder.leave(); // first: supabase-js says SIGNED_OUT during signOut(), and this page loads only once
      try {
        const a = await auth();
        const { error } = await a.signOut();
        if (error) await a.signOut({ scope: "local" }); // the server didn't answer: sign out in this browser anyway
      } catch {
        /* nothing more can be done here; the page loads afresh as a guest below */
      }
      holder.set({ status: "guest" });
      load(returnPath(then));
    },
    accessToken: () => token((a) => a.getSession()),
    refreshedToken: () => token((a) => a.refreshSession()),
    async dropSession() {
      try {
        sessionStorage.setItem(ENDED_KEY, "1"); // the page loaded afresh says why
      } catch {
        /* storage unavailable */
      }
      try {
        await (await auth()).signOut({ scope: "local" });
      } catch {
        /* the state below is what the screens follow */
      }
      holder.set({ status: "guest" });
    },
  };
}
