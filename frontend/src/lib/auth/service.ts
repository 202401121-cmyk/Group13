import { useEffect, useState } from "react";
import { apiConfig, type BearerSource } from "@/lib/api/client";
import { publicEnv } from "@/lib/config";
import { createMockAuthService } from "./mockService";
import { SIGN_IN_FAILED, type AuthService, type AuthState } from "./types";

let service: AuthService | null = null;

/**
 * A service whose code is fetched on first use, so every page carries only this small wrapper (D-45 budgets), and
 * mock mode never downloads the live one. Until it has loaded, the state is "loading". A chunk that fails to load is
 * a sign-in that can't be checked: the shopper goes on as a guest, and the next sign-in tries again.
 */
export function deferredAuthService(
  isMock: boolean,
  load: () => Promise<AuthService>,
): AuthService {
  let state: AuthState = { status: "loading" };
  let pending: Promise<AuthService> | null = null;
  const listeners = new Set<(s: AuthState) => void>();
  const tell = (next: AuthState) => {
    state = next;
    listeners.forEach((listener) => listener(next));
  };
  const real = () =>
    (pending ??= load().then(
      (svc) => {
        svc.subscribe(tell);
        svc.start();
        tell(svc.current());
        return svc;
      },
      (e: unknown) => {
        pending = null;
        tell({ status: "guest" });
        throw e;
      },
    ));
  const quietly = <T>(work: (svc: AuthService) => Promise<T>, otherwise: T) =>
    real().then(work, () => otherwise);
  return {
    isMock,
    current: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    start: () => void real().catch(() => undefined),
    signIn: (returnTo) =>
      real().then(
        (svc) => svc.signIn(returnTo),
        () => tell({ status: "guest", error: SIGN_IN_FAILED }),
      ),
    signOut: (then) => quietly((svc) => svc.signOut(then), undefined),
    accessToken: () => quietly((svc) => svc.accessToken(), null),
    refreshedToken: () => quietly((svc) => svc.refreshedToken(), null),
    dropSession: () => quietly((svc) => svc.dropSession(), undefined),
  };
}

/** Live mode with no Supabase settings: no sign-in at all, and every caller is a guest, as before US-30. */
export function unavailableAuthService(): AuthService {
  const state: AuthState = { status: "unavailable" };
  return {
    isMock: false,
    current: () => state,
    subscribe: () => () => undefined,
    start: () => undefined,
    signIn: async () => undefined,
    signOut: async () => undefined,
    accessToken: async () => null,
    refreshedToken: async () => null,
    dropSession: async () => undefined,
  };
}

/**
 * The mock in mock mode (small, and needed by every page at once); supabase-js in live mode, loaded on first use so
 * mock mode never downloads it; or no sign-in when its two public settings are missing.
 */
export function getAuthService(): AuthService {
  if (service) return service;
  const env = publicEnv();
  const url = env.VITE_SUPABASE_URL?.trim().replace(/\/+$/, "") ?? "";
  const publishableKey = env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim() ?? "";
  service =
    apiConfig(env).mode === "mock"
      ? createMockAuthService()
      : url && publishableKey
        ? deferredAuthService(false, () =>
            import("./liveService").then((m) => m.createLiveAuthService({ url, publishableKey })),
          )
        : unavailableAuthService();
  return service;
}

/** For tests: use this service instead. */
export function setAuthServiceForTests(next: AuthService | null): void {
  service = next;
}

/**
 * What the API client asks for on each call: the bearer token, and a refreshed one for one retry. It is read at the
 * moment of the call, never kept (S4-10: a signed-in shopper's token goes on every backend call).
 */
export const bearerSource: BearerSource = {
  token: () => getAuthService().accessToken(),
  refreshed: () => getAuthService().refreshedToken(),
};

/**
 * Whether the shopper is, or may be, signed in: decides how a run's progress is read (S4-10). While the session is
 * still being read at page load the answer is yes, so a signed-in shopper's first stream carries their token.
 */
export function maySendToken(): boolean {
  const status = getAuthService().current().status;
  return status === "signedIn" || status === "loading";
}

/** The sign-in state, read after hydration: "loading" on the server and in the first paint. */
export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  useEffect(() => {
    const auth = getAuthService();
    const stop = auth.subscribe(setState);
    auth.start();
    setState(auth.current());
    return stop;
  }, []);
  return state;
}
