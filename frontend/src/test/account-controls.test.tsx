/**
 * US-30 navigation, through the real components rendered with React itself: "Sign in", or the shopper's name with
 * "Sign out" and "My searches"; a failed sign-in's sentence; nothing while the session is read, and no sign-in at all
 * without Supabase settings.
 */
import { act, type ReactElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { AccountControls, SignInError } from "@/components/shopsense/AccountControls";
import { setAccountServiceForTests } from "@/lib/account/service";
import { AccountError, type AccountService } from "@/lib/account/types";
import { setAuthServiceForTests } from "@/lib/auth/service";
import { SIGN_IN_FAILED, type AuthService, type AuthState } from "@/lib/auth/types";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, className }: { to: string; children: ReactNode; className?: string }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
  useRouterState: () => "/privacy?x=1",
}));

let root: Root | null = null;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  setAuthServiceForTests(null);
  setAccountServiceForTests(null);
});

async function render(element: ReactElement) {
  const host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  await act(async () => root!.render(element));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0))); // the profile's name
  return host;
}

function button(name: string | RegExp): HTMLButtonElement | undefined {
  return [...document.querySelectorAll("button")].find((b) => {
    const text = (b.textContent ?? "").trim();
    return typeof name === "string" ? text === name : name.test(text);
  });
}

function fakeAuth(state: AuthState) {
  let current = state;
  const listeners = new Set<(s: AuthState) => void>();
  const auth = {
    isMock: true,
    current: () => current,
    subscribe: (l: (s: AuthState) => void) => {
      listeners.add(l);
      return () => void listeners.delete(l);
    },
    start: () => undefined,
    signIn: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
    accessToken: async () => null,
    refreshedToken: async () => null,
    dropSession: vi.fn(async () => {
      current = { status: "guest" };
      listeners.forEach((l) => l(current));
    }),
  } satisfies AuthService;
  setAuthServiceForTests(auth);
  return auth;
}

function fakeAccount(me: AccountService["me"]) {
  setAccountServiceForTests({
    isMock: true,
    me,
    searches: async () => ({ items: [], nextCursor: null }),
    search: async () => {
      throw new AccountError("notFound");
    },
  });
}

const user = (id: string, name: string | null = "Asha Rao") => ({ id, name });

describe("the navigation's sign-in", () => {
  it("is signed out by default: a Sign in button that comes back to this page", async () => {
    const auth = fakeAuth({ status: "guest" });
    await render(<AccountControls layout="bar" />);
    act(() => button("Sign in")!.click());
    expect(auth.signIn).toHaveBeenCalledWith("/privacy?x=1");
    expect(button(/Sign out/)).toBeUndefined();
  });

  it("signed in: the name Google gave, and Sign out", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: null }));
    const auth = fakeAuth({ status: "signedIn", user: user("a1") });
    const host = await render(<AccountControls layout="bar" />);
    expect(host.textContent).toContain("Asha Rao");
    act(() => button(/^Sign out/)!.click());
    expect(auth.signOut).toHaveBeenCalledWith("/privacy?x=1");
    expect(button("Sign in")).toBeUndefined();
  });

  it("the profile's display name wins, and the menu has My searches", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: "Asha R." }));
    fakeAuth({ status: "signedIn", user: user("b1") });
    const host = await render(<AccountControls layout="panel" />);
    expect(host.textContent).toContain("Asha R.");
    const link = [...host.querySelectorAll("a")].find((a) => a.textContent === "My searches");
    expect(link?.getAttribute("href")).toBe("/my-searches");
  });

  it("with no name at all: Your account", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: null }));
    fakeAuth({ status: "signedIn", user: user("c1", null) });
    const host = await render(<AccountControls layout="bar" />);
    expect(host.textContent).toContain("Your account");
  });

  it("a session the backend refuses (401) falls back to a guest", async () => {
    fakeAccount(async () => {
      throw new AccountError("signedOut");
    });
    const auth = fakeAuth({ status: "signedIn", user: user("d1") });
    await render(<AccountControls layout="bar" />);
    expect(auth.dropSession).toHaveBeenCalled();
    expect(button("Sign in")).toBeDefined();
  });

  it("shows no button while the session is read", async () => {
    fakeAuth({ status: "loading" });
    await render(<AccountControls layout="bar" />);
    expect(document.querySelectorAll("button")).toHaveLength(0);
  });

  it("has no sign-in at all without Supabase settings", async () => {
    fakeAuth({ status: "unavailable" });
    const host = await render(<AccountControls layout="bar" />);
    expect(host.innerHTML).toBe("");
  });
});

describe("a failed sign-in", () => {
  it("is said in our words, until it is closed", async () => {
    fakeAuth({ status: "guest", error: SIGN_IN_FAILED });
    await render(<SignInError />);
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(SIGN_IN_FAILED);
    act(() =>
      document.querySelector<HTMLButtonElement>('button[aria-label="Close this message"]')!.click(),
    );
    expect(document.querySelector('[role="alert"]')).toBeNull();
  });

  it("says nothing otherwise", async () => {
    fakeAuth({ status: "guest" });
    await render(<SignInError />);
    expect(document.querySelector('[role="alert"]')).toBeNull();
  });
});
