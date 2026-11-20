/**
 * US-30 sign-in, without a network: the mock, and the live service around a fake supabase-js.
 *
 * Signed out by default; a failed sign-in says so; a session that can't be refreshed falls back to a guest; and (D-80)
 * a sign-in or a sign-out starts a new session ID, in both directions, while a token refresh changes nothing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMockAuthService, MOCK_SESSION_KEY, MOCK_USER } from "./mockService";
import { createLiveAuthService, ENDED_KEY, READY_WAIT_MS } from "./liveService";
import { RESET_KEY } from "./shared";
import { deferredAuthService, unavailableAuthService } from "./service";
import { endSearches, GUEST, noteWho, WHO_KEY } from "./sessionReset";
import { SESSION_ENDED, SIGN_IN_FAILED, returnPath, type AuthState } from "./types";
import { fakeSupabase, memoryStorage, session } from "@/test/helpers/fakeAuth";

const SEARCH_KEYS = [
  "shopsense.session.sess_0123456789abcdef01234567",
  "shopsense.request.v1",
  "shopsense.brief.confirmed.v1",
  "shopsense.brief.confirmed.session.v1",
];

function withSearch(storage: Storage = sessionStorage) {
  SEARCH_KEYS.forEach((key) => storage.setItem(key, "{}"));
}

function searchKeysLeft(storage: Storage = sessionStorage) {
  return SEARCH_KEYS.filter((key) => storage.getItem(key) !== null);
}

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});
afterEach(() => vi.useRealTimers());

// --- D-80: a new session ID at sign-in and sign-out ---------------------------------------------------------------

describe("who this tab searches as (D-80)", () => {
  it("guest to signed in ends every search kept in the tab, and keeps the auth keys", () => {
    const storage = memoryStorage();
    noteWho(GUEST, storage);
    withSearch(storage);
    storage.setItem("shopsense.auth.mock", "{}");
    expect(noteWho("user-1", storage)).toBe(true);
    expect(searchKeysLeft(storage)).toEqual([]);
    expect(storage.getItem("shopsense.auth.mock")).toBe("{}");
    expect(storage.getItem(WHO_KEY)).toBe("user-1");
  });

  it("signed in to guest ends them too", () => {
    const storage = memoryStorage();
    noteWho("user-1", storage);
    withSearch(storage);
    expect(noteWho(GUEST, storage)).toBe(true);
    expect(searchKeysLeft(storage)).toEqual([]);
  });

  it("the same shopper again (a refresh, a sign-in that was already in) ends nothing", () => {
    const storage = memoryStorage();
    noteWho("user-1", storage);
    withSearch(storage);
    expect(noteWho("user-1", storage)).toBe(false);
    expect(searchKeysLeft(storage)).toEqual(SEARCH_KEYS);
  });

  it("a fresh tab only notes who it is", () => {
    const storage = memoryStorage();
    withSearch(storage);
    expect(noteWho("user-1", storage)).toBe(false);
    expect(searchKeysLeft(storage)).toEqual(SEARCH_KEYS);
  });

  it("endSearches leaves anything that isn't ours", () => {
    const storage = memoryStorage();
    storage.setItem("someone.else", "x");
    withSearch(storage);
    endSearches(storage);
    expect(storage.getItem("someone.else")).toBe("x");
  });
});

describe("returnPath", () => {
  it.each([
    ["/my-searches", "/my-searches"],
    ["/privacy?x=1", "/privacy?x=1"],
    ["/s/sess_0123456789abcdef01234567/results", "/"], // that search ends at sign-in (D-80)
    ["/research", "/"],
    ["https://evil.example/", "/"],
    ["//evil.example/", "/"],
    ["/\\evil.example", "/"],
  ])("%s comes back to %s", (path, back) => expect(returnPath(path)).toBe(back));
});

// --- the mock ------------------------------------------------------------------------------------------------------

describe("mock sign-in", () => {
  it("is signed out by default", () => {
    const auth = createMockAuthService({ load: vi.fn() });
    auth.start();
    expect(auth.current()).toEqual({ status: "guest", error: undefined });
  });

  it("signs in as the fake shopper, ends the guest's searches and loads the page to come back to", async () => {
    const load = vi.fn();
    const auth = createMockAuthService({ load });
    auth.start();
    withSearch();
    await auth.signIn("/my-searches");
    expect(auth.current()).toEqual({ status: "signedIn", user: MOCK_USER });
    expect(load).toHaveBeenCalledWith("/my-searches");
    expect(searchKeysLeft()).toEqual([]);
    expect(await auth.accessToken()).toBe("mock-access-token");
  });

  it("signs out, ends the shopper's searches, and has no token after", async () => {
    const load = vi.fn();
    sessionStorage.setItem(MOCK_SESSION_KEY, JSON.stringify({ expiresAt: Date.now() + 60_000 }));
    const auth = createMockAuthService({ load });
    auth.start();
    withSearch();
    await auth.signOut("/");
    expect(auth.current().status).toBe("guest");
    expect(searchKeysLeft()).toEqual([]);
    expect(load).toHaveBeenCalledWith("/");
    expect(await auth.accessToken()).toBeNull();
  });

  it("an expired session falls back to a guest", () => {
    sessionStorage.setItem(MOCK_SESSION_KEY, JSON.stringify({ expiresAt: Date.now() - 1 }));
    const auth = createMockAuthService({ load: vi.fn() });
    auth.start();
    expect(auth.current().status).toBe("guest");
    expect(sessionStorage.getItem(MOCK_SESSION_KEY)).toBeNull();
  });

  it("shows our sentence for a failed sign-in, and clears it from the address", () => {
    window.history.replaceState(
      null,
      "",
      "/?error=access_denied&error_description=<b>nope</b>#keep=1",
    );
    const auth = createMockAuthService({ load: vi.fn() });
    auth.start();
    expect(auth.current()).toEqual({ status: "guest", error: SIGN_IN_FAILED });
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("#keep=1");
  });
});

// --- live, with a fake supabase-js -------------------------------------------------------------------------------

function live(initial: ReturnType<typeof session> | null = null) {
  const supabase = fakeSupabase(initial);
  const load = vi.fn();
  const auth = createLiveAuthService({
    url: "https://testproject.supabase.test",
    publishableKey: "sb_publishable_test",
    makeClient: async () => supabase.auth,
    load,
  });
  const seen: AuthState[] = [];
  auth.subscribe((s) => seen.push(s));
  return { auth, supabase, load, seen };
}

const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("live sign-in", () => {
  it("is signed out by default, with no token", async () => {
    const { auth } = live();
    auth.start();
    await settled();
    expect(auth.current()).toEqual({ status: "guest", error: undefined });
    expect(await auth.accessToken()).toBeNull();
  });

  it("reads a kept session, with the name Google gave", async () => {
    const { auth } = live(session("u1", "Asha Rao"));
    auth.start();
    await settled();
    expect(auth.current()).toEqual({ status: "signedIn", user: { id: "u1", name: "Asha Rao" } });
    expect(await auth.accessToken()).toBe("token-for-u1");
  });

  it("a call made before the session is read waits for it, so it carries the token", async () => {
    const { auth } = live(session("u1"));
    await expect(auth.accessToken()).resolves.toBe("token-for-u1"); // start() not called yet
  });

  it("an expired session that can't be refreshed falls back to a guest", async () => {
    const { auth, supabase } = live(session("u1"));
    auth.start();
    await settled();
    supabase.emit("SIGNED_OUT", null); // what supabase-js says when the refresh token is refused
    expect(auth.current().status).toBe("guest");
    expect(await auth.accessToken()).toBeNull();
  });

  it("a token refresh keeps the shopper and their searches", async () => {
    const { auth, supabase } = live(session("u1"));
    auth.start();
    await settled();
    withSearch();
    supabase.emit("TOKEN_REFRESHED", { ...session("u1"), access_token: "newer" });
    expect(auth.current().status).toBe("signedIn");
    expect(searchKeysLeft()).toEqual(SEARCH_KEYS);
    expect(await auth.accessToken()).toBe("newer");
  });

  it("signing in sends the shopper to Google and back to the page they were on", async () => {
    const { auth, supabase } = live();
    auth.start();
    await settled();
    await auth.signIn("/my-searches");
    await auth.signIn("/s/sess_0123456789abcdef01234567/results");
    expect(supabase.calls.signIn).toEqual([
      { redirectTo: `${window.location.origin}/my-searches` },
      { redirectTo: `${window.location.origin}/` },
    ]);
  });

  it("a sign-in that can't start says so", async () => {
    const { auth, supabase } = live();
    auth.start();
    await settled();
    supabase.errors.signIn = new Error("provider is not enabled");
    await auth.signIn("/");
    expect(auth.current()).toEqual({ status: "guest", error: SIGN_IN_FAILED });
  });

  it("a failed sign-in coming back shows our sentence", async () => {
    window.history.replaceState(
      null,
      "",
      "/my-searches?error=server_error&error_code=x&error_description=y",
    );
    const { auth } = live();
    auth.start();
    await settled();
    expect(auth.current()).toEqual({ status: "guest", error: SIGN_IN_FAILED });
    expect(window.location.pathname + window.location.search).toBe("/my-searches");
  });

  it("signing out ends the session (locally too, if the server can't be reached) and loads the page afresh", async () => {
    const { auth, supabase, load } = live(session("u1"));
    auth.start();
    await settled();
    withSearch();
    supabase.errors.signOut = new Error("network");
    await auth.signOut("/s/sess_0123456789abcdef01234567/results");
    expect(supabase.calls.signOut).toEqual([undefined, "local"]);
    expect(auth.current().status).toBe("guest");
    expect(searchKeysLeft()).toEqual([]);
    expect(load).toHaveBeenCalledWith("/");
  });

  it("D-80 in both directions: a sign-in after guest searches, then a sign-out, each end the tab's searches", async () => {
    const { auth, supabase } = live();
    auth.start();
    await settled();
    withSearch();
    supabase.emit("SIGNED_IN", session("u1"));
    expect(searchKeysLeft()).toEqual([]);
    withSearch();
    supabase.emit("SIGNED_OUT", null);
    expect(searchKeysLeft()).toEqual([]);
  });

  it("a refreshed token comes from supabase-js; none for a guest", async () => {
    const { auth, supabase } = live(session("u1"));
    auth.start();
    await settled();
    expect(await auth.refreshedToken()).toBe("token-for-u1-refreshed");
    expect(supabase.calls.refresh).toBe(1);
    supabase.emit("SIGNED_OUT", null);
    expect(await auth.refreshedToken()).toBeNull();
  });

  it("dropping a session the backend refused signs out in this browser only", async () => {
    const { auth, supabase } = live(session("u1"));
    auth.start();
    await settled();
    await auth.dropSession();
    expect(supabase.calls.signOut).toEqual(["local"]);
    expect(auth.current().status).toBe("guest");
  });
});

describe("a change of who is searching after the page has loaded (D-80)", () => {
  it("a sign-in in another tab loads this page afresh, so no search in memory carries on", async () => {
    window.history.replaceState(null, "", "/privacy");
    const { auth, supabase, load } = live();
    auth.start();
    await settled();
    expect(load).not.toHaveBeenCalled(); // the first look only notes who it is
    supabase.emit("SIGNED_IN", session("u1"));
    expect(load).toHaveBeenCalledWith("/privacy");
  });

  it("a session that ended on a search's own page goes home, as that search has ended", async () => {
    window.history.replaceState(null, "", "/s/sess_0123456789abcdef01234567/results");
    const { auth, supabase, load } = live(session("u1"));
    auth.start();
    await settled();
    supabase.emit("SIGNED_OUT", null);
    expect(load).toHaveBeenCalledWith("/");
  });

  it("a token refresh loads nothing", async () => {
    const { auth, supabase, load } = live(session("u1"));
    auth.start();
    await settled();
    supabase.emit("TOKEN_REFRESHED", session("u1"));
    expect(load).not.toHaveBeenCalled();
  });

  it("a sign-out started here loads its own page, once", async () => {
    const { auth, load } = live(session("u1"));
    auth.start();
    await settled();
    await auth.signOut("/privacy");
    expect(load.mock.calls).toEqual([["/privacy"]]);
  });
});

describe("the service, loaded on first use", () => {
  it("is loading until its code has come, then follows the real one", async () => {
    const { auth: real } = live(session("u1"));
    const auth = deferredAuthService(false, async () => real);
    expect(auth.current().status).toBe("loading");
    auth.start();
    await settled();
    await settled();
    expect(auth.current()).toEqual({ status: "signedIn", user: { id: "u1", name: null } });
    expect(await auth.accessToken()).toBe("token-for-u1");
  });

  it("whose code can't be had: a guest, no token, and a sign-in that says it didn't work", async () => {
    const auth = deferredAuthService(false, () => Promise.reject(new Error("chunk")));
    auth.start();
    await settled();
    expect(auth.current().status).toBe("guest");
    expect(await auth.accessToken()).toBeNull();
    await auth.signIn("/");
    expect(auth.current()).toEqual({ status: "guest", error: SIGN_IN_FAILED });
  });
});

describe("from the review: D-80 on the page's first look, and the edges of sign-in", () => {
  it("a tab whose shopper changed while it was closed loads afresh on its first look", async () => {
    window.history.replaceState(null, "", "/s/sess_0123456789abcdef01234567/questions");
    sessionStorage.setItem(WHO_KEY, GUEST); // this tab last searched as a guest
    withSearch();
    const { auth, load } = live(session("u1")); // and now the browser is signed in
    auth.start();
    await settled();
    expect(searchKeysLeft()).toEqual([]);
    expect(load).toHaveBeenCalledWith("/"); // that search's page has nothing to show any more
    expect(sessionStorage.getItem(RESET_KEY)).toBe("1");
  });

  it("the page a sign-in came back to needs no second load, and ends any search written meanwhile", async () => {
    sessionStorage.setItem(WHO_KEY, GUEST);
    sessionStorage.setItem(RESET_KEY, "1"); // set before going to Google
    withSearch(); // a reply that landed after the searches were ended
    const { auth, load } = live(session("u1"));
    auth.start();
    await settled();
    expect(load).not.toHaveBeenCalled();
    expect(searchKeysLeft()).toEqual([]);
    expect(sessionStorage.getItem(RESET_KEY)).toBeNull();
  });

  it("a sign-in marks the reset, and one that can't start takes it back", async () => {
    const { auth, supabase } = live();
    auth.start();
    await settled();
    await auth.signIn("/");
    expect(sessionStorage.getItem(RESET_KEY)).toBe("1");
    supabase.errors.signIn = new Error("provider is not enabled");
    await auth.signIn("/");
    expect(sessionStorage.getItem(RESET_KEY)).toBeNull();
  });

  it("a sign-out loads its page once, though supabase-js says SIGNED_OUT on the way", async () => {
    const { auth, load } = live(session("u1"));
    auth.start();
    await settled();
    await auth.signOut("/#how");
    expect(load.mock.calls).toEqual([["/"]]); // never a fragment: the page must load afresh
  });

  it("a code that didn't become a session says the sign-in failed, and leaves the address", async () => {
    window.history.replaceState(null, "", "/my-searches?code=abc&state=1");
    const { auth } = live();
    auth.start();
    await settled();
    expect(auth.current()).toEqual({ status: "guest", error: SIGN_IN_FAILED });
    expect(window.location.search).toBe("?state=1");
  });

  it("a session that can't be read is a guest, not loading for ever", async () => {
    const supabase = fakeSupabase();
    supabase.auth.getSession = () => Promise.reject(new Error("storage"));
    const auth = createLiveAuthService({
      url: "https://testproject.supabase.test",
      publishableKey: "sb_publishable_test",
      makeClient: async () => supabase.auth,
      load: vi.fn(),
    });
    auth.start();
    await settled();
    expect(auth.current().status).toBe("guest");
  });

  it("a session the backend refused says so on the page that loads next", async () => {
    const first = live(session("u1"));
    first.auth.start();
    await settled();
    await first.auth.dropSession();
    expect(sessionStorage.getItem(ENDED_KEY)).toBe("1");
    const next = live();
    next.auth.start();
    await settled();
    expect(next.auth.current()).toEqual({ status: "guest", error: SESSION_ENDED });
  });

  it("a call made while the session can't be read fails, never going as a guest's", async () => {
    vi.useFakeTimers();
    const auth = createLiveAuthService({
      url: "https://testproject.supabase.test",
      publishableKey: "sb_publishable_test",
      makeClient: () => new Promise(() => undefined),
      load: vi.fn(),
    });
    const token = auth.accessToken();
    const failed = expect(token).rejects.toMatchObject({ name: "SignInNotReady" });
    await vi.advanceTimersByTimeAsync(READY_WAIT_MS);
    await failed;
  });
});

describe("live mode with no Supabase settings", () => {
  it("has no sign-in and no token", async () => {
    const auth = unavailableAuthService();
    auth.start();
    expect(auth.current()).toEqual({ status: "unavailable" });
    expect(await auth.accessToken()).toBeNull();
  });
});
