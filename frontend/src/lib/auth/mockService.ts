/**
 * Mock sign-in (VITE_API_MODE=mock): a fake shopper, Asha, with no network and no Google.
 *
 * "Sign in" signs in at once and loads the page to come back to, as the real redirect does. The fake session is kept
 * in this tab (MOCK_SESSION_KEY) with an expiry, so a reload keeps it, and a session past its expiry falls back to a
 * guest, as an expired Supabase session does. Its token is a fixed string: mock mode never calls the backend.
 */
import { reloadHere, signInErrorFromAddress, stateHolder } from "./shared";
import { returnPath, type AuthService, type AuthUser } from "./types";

export const MOCK_SESSION_KEY = "shopsense.auth.mock";
export const MOCK_USER: AuthUser = {
  id: "6f1c2b8e-3d4a-4f5b-9c7d-2e1a0b9c8d7e",
  name: "Asha",
  email: "asha@example.test",
  since: Date.parse("2026-11-03T09:00:00Z"),
};
export const MOCK_SESSION_MS = 60 * 60 * 1000; // an hour, as a Supabase access token
const MOCK_TOKEN = "mock-access-token";

interface MockSession {
  expiresAt: number;
}

function readSession(storage: Storage, now: number): MockSession | null {
  try {
    const raw = storage.getItem(MOCK_SESSION_KEY);
    const session = raw ? (JSON.parse(raw) as Partial<MockSession>) : null;
    if (!session || typeof session.expiresAt !== "number" || session.expiresAt <= now) {
      storage.removeItem(MOCK_SESSION_KEY);
      return null;
    }
    return { expiresAt: session.expiresAt };
  } catch {
    return null;
  }
}

export interface MockAuthOptions {
  storage?: Storage;
  now?: () => number;
  /** How a sign-in or a sign-out loads its page afresh. Tests pass a spy. */
  load?: (path: string) => void;
  win?: Window;
}

export function createMockAuthService(options: MockAuthOptions = {}): AuthService {
  const now = options.now ?? Date.now;
  const load = options.load ?? ((path: string) => window.location.assign(path));
  const holder = stateHolder({ status: "loading" }, reloadHere(load, options.win));
  let started = false;
  const storage = () => options.storage ?? sessionStorage;
  const signedIn = () => readSession(storage(), now()) !== null;

  return {
    isMock: true,
    current: holder.get,
    subscribe: holder.subscribe,
    start() {
      if (started) return;
      started = true;
      const error = signInErrorFromAddress(options.win ?? window);
      holder.set(signedIn() ? { status: "signedIn", user: MOCK_USER } : { status: "guest", error });
    },
    async signIn(returnTo) {
      storage().setItem(MOCK_SESSION_KEY, JSON.stringify({ expiresAt: now() + MOCK_SESSION_MS }));
      holder.leave();
      holder.set({ status: "signedIn", user: MOCK_USER });
      load(returnPath(returnTo));
    },
    async signOut(then) {
      storage().removeItem(MOCK_SESSION_KEY);
      holder.leave();
      holder.set({ status: "guest" });
      load(returnPath(then));
    },
    async accessToken() {
      return signedIn() ? MOCK_TOKEN : null;
    },
    async refreshedToken() {
      return signedIn() ? MOCK_TOKEN : null;
    },
    async dropSession() {
      storage().removeItem(MOCK_SESSION_KEY);
      holder.set({ status: "guest" });
    },
  };
}
