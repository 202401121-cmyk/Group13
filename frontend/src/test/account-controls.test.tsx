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
  Link: ({
    to,
    search,
    children,
    className,
    role,
    onClick,
  }: {
    to: string;
    search?: Record<string, string>;
    children: ReactNode;
    className?: string;
    role?: string;
    onClick?: () => void;
  }) => (
    <a
      href={search ? `${to}?${new URLSearchParams(search).toString()}` : to}
      className={className}
      role={role}
      onClick={(e) => {
        e.preventDefault(); // no page to go to in the test
        onClick?.();
      }}
    >
      {children}
    </a>
  ),
  useRouterState: () => "/privacy?x=1",
}));

function link(name: string): HTMLAnchorElement | undefined {
  return [...document.querySelectorAll("a")].find((a) => (a.textContent ?? "").trim() === name);
}

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
  it("is signed out by default: Sign in opens the sign-in page, coming back to this page", async () => {
    fakeAuth({ status: "guest" });
    await render(<AccountControls layout="bar" />);
    expect(link("Sign in")?.getAttribute("href")).toBe("/sign-in?next=%2Fprivacy%3Fx%3D1");
    expect(button(/account menu/)).toBeUndefined();
  });

  it("signed in: the shopper's menu, with the name Google gave, their places and Sign out", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: null }));
    const auth = fakeAuth({
      status: "signedIn",
      user: { ...user("a1"), email: "asha@example.test" },
    });
    const host = await render(<AccountControls layout="bar" />);
    const menuButton = button("Asha Rao") ?? document.querySelector<HTMLButtonElement>("button");
    expect(menuButton?.getAttribute("aria-label")).toBe("Asha Rao, account menu");
    expect(menuButton?.getAttribute("aria-expanded")).toBe("false");
    act(() => menuButton!.click());
    expect(menuButton?.getAttribute("aria-expanded")).toBe("true");
    const menu = host.querySelector('[role="menu"]');
    expect(menu?.textContent).toContain("asha@example.test");
    const items = [...(menu?.querySelectorAll('[role="menuitem"]') ?? [])].map((i) =>
      i.textContent?.trim(),
    );
    expect(items).toEqual(["My searches", "Your profile", "Privacy", "Sign out"]);
    expect(document.activeElement?.textContent?.trim()).toBe("My searches"); // focus moved into the menu
    act(() => (menu!.querySelector('button[role="menuitem"]') as HTMLButtonElement).click());
    expect(auth.signOut).toHaveBeenCalledWith("/privacy?x=1");
  });

  it("the menu closes with Escape and gives focus back to its button", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: null }));
    fakeAuth({ status: "signedIn", user: user("e1") });
    const host = await render(<AccountControls layout="bar" />);
    const menuButton = document.querySelector<HTMLButtonElement>("button[aria-haspopup]")!;
    act(() => menuButton.click());
    const menu = host.querySelector<HTMLElement>('[role="menu"]')!;
    act(() => {
      menu.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    });
    expect(document.activeElement?.textContent?.trim()).toBe("Your profile");
    act(() => {
      menu.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
    });
    expect(document.activeElement?.textContent?.trim()).toBe("Sign out");
    act(() => {
      menu.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(host.querySelector('[role="menu"]')).toBeNull();
    expect(document.activeElement).toBe(menuButton);
  });

  it("Up opens the menu on its last item, Home goes to the first, and Tab closes it from the button", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: null }));
    fakeAuth({ status: "signedIn", user: user("t1") });
    const host = await render(<AccountControls layout="bar" />);
    const menuButton = document.querySelector<HTMLButtonElement>("button[aria-haspopup]")!;
    menuButton.focus();
    act(() => {
      menuButton.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
    });
    expect(document.activeElement?.textContent?.trim()).toBe("Sign out");
    const menu = host.querySelector<HTMLElement>('[role="menu"]')!;
    act(() => {
      menu.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    });
    expect(document.activeElement?.textContent?.trim()).toBe("My searches");
    act(() => {
      menu.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    });
    expect(host.querySelector('[role="menu"]')).toBeNull();
    // Focus is back on the button, so the browser's Tab moves on from there.
    expect(document.activeElement).toBe(menuButton);
  });

  it("choosing a place closes the menu and gives focus back to its button", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: null }));
    fakeAuth({ status: "signedIn", user: user("p1") });
    const host = await render(<AccountControls layout="bar" />);
    const menuButton = document.querySelector<HTMLButtonElement>("button[aria-haspopup]")!;
    act(() => menuButton.click());
    act(() => link("Your profile")!.click());
    expect(host.querySelector('[role="menu"]')).toBeNull();
    expect(document.activeElement).toBe(menuButton);
  });

  it("a click outside closes the menu; a click on the shopper's name inside keeps it open", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: null }));
    fakeAuth({ status: "signedIn", user: { ...user("o1"), email: "asha@example.test" } });
    const host = await render(<AccountControls layout="bar" />);
    const menuButton = document.querySelector<HTMLButtonElement>("button[aria-haspopup]")!;
    act(() => menuButton.click());
    const email = [...host.querySelectorAll('[role="menu"] p')].find(
      (p) => p.textContent === "asha@example.test",
    )!;
    const down = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    act(() => {
      email.dispatchEvent(new Event("pointerdown", { bubbles: true }));
      email.dispatchEvent(down);
    });
    expect(down.defaultPrevented).toBe(true); // focus stays in the menu
    expect(host.querySelector('[role="menu"]')).not.toBeNull();
    act(() => {
      document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    });
    expect(host.querySelector('[role="menu"]')).toBeNull();
  });

  it("the profile's display name wins, and the small-screen menu has My searches and the profile", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: "Asha R." }));
    fakeAuth({ status: "signedIn", user: user("b1") });
    const host = await render(<AccountControls layout="panel" />);
    expect(host.textContent).toContain("Asha R.");
    expect(link("My searches")?.getAttribute("href")).toBe("/my-searches");
    expect(link("Your profile")?.getAttribute("href")).toBe("/account");
    expect(button("Sign out")).toBeDefined();
  });

  it("with no name at all: Your account", async () => {
    fakeAccount(async () => ({ signedIn: true, displayName: null }));
    fakeAuth({ status: "signedIn", user: user("c1", null) });
    await render(<AccountControls layout="bar" />);
    expect(document.querySelector("button")?.getAttribute("aria-label")).toBe(
      "Your account, account menu",
    );
  });

  it("a session the backend refuses (401) falls back to a guest", async () => {
    fakeAccount(async () => {
      throw new AccountError("signedOut");
    });
    const auth = fakeAuth({ status: "signedIn", user: user("d1") });
    await render(<AccountControls layout="bar" />);
    expect(auth.dropSession).toHaveBeenCalled();
    expect(link("Sign in")).toBeDefined();
  });

  it("shows no control while the session is read", async () => {
    fakeAuth({ status: "loading" });
    await render(<AccountControls layout="bar" />);
    expect(document.querySelectorAll("button, a")).toHaveLength(0);
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
