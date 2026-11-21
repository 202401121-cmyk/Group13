/**
 * US-11 "What we remember" and the account delete, through the real components rendered with React itself, on the
 * mock service with every call recorded. Undo restores an item without a call; Forget sends exactly one call after 5
 * seconds, or at once when the page is left; a 404 refreshes the list; a 503 offers to try again; the export is a
 * .json file with no embedding; and the delete dialog refuses anything but "delete" without a call.
 */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { EMPTY_MEMORY, MemorySection } from "@/components/account/MemorySection";
import DeleteAccountDialog, {
  DELETED_PATH,
  TYPE_DELETE,
} from "@/components/account/DeleteAccountDialog";
import {
  DELETED_KEY,
  EXPORT_FILE,
  MEMORY_MESSAGES,
  MemoryError,
  UNDO_MS,
  type MemoryService,
} from "@/lib/account/memory";
import {
  createMockMemoryService,
  MOCK_DELETE_UNFINISHED,
  MOCK_MEMORY_KEY,
  MOCK_NOTE,
} from "@/lib/account/memoryMock";
import { setMemoryServiceForTests } from "@/lib/account/memoryService";
import { setAuthServiceForTests } from "@/lib/auth/service";
import type { AuthService, AuthState } from "@/lib/auth/types";

const USER = "a0000000-0000-4000-8000-00000000000a";

interface Spy extends MemoryService {
  calls: { name: string; args: unknown[] }[];
  failNext(e: Error): void;
}

/** The mock service, recording every call; it can fail the next one. */
function spy(): Spy {
  const mock = createMockMemoryService(0);
  const calls: Spy["calls"] = [];
  let failure: Error | null = null;
  const record =
    <A extends unknown[], R>(name: string, run: (...args: A) => Promise<R>) =>
    async (...args: A): Promise<R> => {
      calls.push({ name, args });
      if (failure) {
        const e = failure;
        failure = null;
        throw e;
      }
      return run(...args);
    };
  const spied: Spy = {
    isMock: true,
    calls,
    failNext: (e) => {
      failure = e;
    },
    list: record("list", () => mock.list()),
    edit: record("edit", (id: string, edit) => mock.edit(id, edit)),
    forget: record("forget", (id: string) => mock.forget(id)),
    // Holding reads the sign-in only, nothing is sent; the held forget is sent through forget(), or noted as sent
    // as the page goes.
    holdForget: (id: string) => {
      calls.push({ name: "holdForget", args: [id] });
      return {
        send: () => spied.forget(id),
        sendAsPageGoes: () => {
          calls.push({ name: "sendAsPageGoes", args: [id] });
        },
      };
    },
    forgetAll: record("forgetAll", () => mock.forgetAll()),
    download: record("download", () => mock.download()),
    deleteAccount: record("deleteAccount", (typed: string) => mock.deleteAccount(typed)),
  };
  return spied;
}

function fakeAuth() {
  const signedOut: string[] = [];
  let dropped = 0;
  const state: AuthState = {
    status: "signedIn",
    user: { id: USER, name: "Asha", email: null, since: null },
  };
  const auth: AuthService = {
    isMock: true,
    current: () => state,
    subscribe: () => () => {},
    start: () => {},
    signIn: async () => {},
    signOut: async (then) => {
      signedOut.push(then);
    },
    accessToken: async () => "t",
    refreshedToken: async () => "t",
    dropSession: async () => {
      dropped++;
    },
  };
  return { auth, signedOut, dropped: () => dropped };
}

let root: Root | null = null;
let service: Spy;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  sessionStorage.clear();
  service = spy();
  setMemoryServiceForTests(service);
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  setMemoryServiceForTests(null);
  setAuthServiceForTests(null);
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** Lets every timer and promise due within `ms` run, inside React's act. */
async function tick(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    // A call's answer comes through several promises and the mock's own 0 ms wait: let each settle.
    for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(0);
  });
}

/** Lets a call's answer arrive: a few short steps of time, for what the answer sets off in turn. */
async function settle() {
  for (let i = 0; i < 5; i++) await tick(20);
}

/** A Blob's text: jsdom's Blob has no text(), so through a FileReader, on real timers. */
function readBlob(blob: Blob): Promise<string> {
  vi.useRealTimers();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("unreadable"));
    reader.readAsText(blob);
  });
}

async function render(element: ReactElement) {
  const host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  act(() => root!.render(element));
  await tick();
}

const showSection = () => render(<MemorySection userId={USER} />);
const rows = () => [
  ...document.querySelectorAll<HTMLLIElement>('ul[aria-labelledby="memory-title"] > li'),
];
const texts = () => rows().map((li) => li.querySelector("p")?.textContent ?? "");

function button(name: string | RegExp, within: ParentNode = document): HTMLButtonElement {
  const found = [...within.querySelectorAll("button")].find((b) => {
    const label = b.getAttribute("aria-label") ?? b.textContent ?? "";
    return typeof name === "string" ? label.trim() === name : name.test(label);
  });
  if (!found) throw new Error(`no button ${String(name)}`);
  return found;
}

function click(el: HTMLElement) {
  act(() => el.click());
}

/** Types into a field as a person would, so React sees the change. */
function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function submit(form: HTMLFormElement) {
  act(() => {
    form.requestSubmit();
  });
}

const calls = (name: string) => service.calls.filter((c) => c.name === name);

describe("the list", () => {
  it("shows each item in plain words with its category, kind and weight", async () => {
    await showSection();
    expect(texts()).toContain("Battery life: high importance");
    const battery = rows().find((li) => li.textContent?.includes("Battery life: high importance"))!;
    expect(battery.textContent).toContain("Priority");
    expect(battery.textContent).toContain("Laptop");
    expect(battery.textContent).toContain("Weight 8 of 10");
    const brand = rows().find((li) => li.textContent?.includes("Leave out: Zenith"))!;
    expect(brand.textContent).toContain("Every search");
    const off = rows().find((li) => li.textContent?.includes("IPX4"))!;
    expect(off.textContent).toContain("Switched off");
  });

  it("says plainly when nothing is remembered yet", async () => {
    sessionStorage.setItem(MOCK_MEMORY_KEY, "empty");
    service = spy();
    setMemoryServiceForTests(service);
    await showSection();
    expect(document.body.textContent).toContain(EMPTY_MEMORY);
    expect(() => button("Forget everything")).toThrow();
  });

  it("a 503 says the memory is unavailable right now, and Try again works", async () => {
    service.failNext(new MemoryError("unavailable"));
    await showSection();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      MEMORY_MESSAGES.unavailable,
    );
    click(button("Try again"));
    await tick();
    expect(rows().length).toBeGreaterThan(0);
    expect(calls("list")).toHaveLength(2);
  });

  it("Try again keeps focus on the section while the list loads", async () => {
    service.failNext(new MemoryError("unavailable"));
    await showSection();
    const again = button("Try again");
    again.focus();
    click(again);
    expect(document.activeElement?.id).toBe("memory-title");
    await tick();
    expect(rows().length).toBeGreaterThan(0);
  });

  it("a sign-in the backend no longer accepts signs the shopper out here", async () => {
    const { auth, dropped } = fakeAuth();
    setAuthServiceForTests(auth);
    service.failNext(new MemoryError("signedOut"));
    await showSection();
    expect(dropped()).toBe(1);
  });
});

describe("forget, with 5 seconds to undo", () => {
  it("Undo restores the item without a call", async () => {
    await showSection();
    const before = texts();
    click(button('Forget "Battery life: high importance"'));
    expect(texts()).not.toContain("Battery life: high importance");
    expect(document.body.textContent).toContain("Forgotten: Battery life: high importance");
    await tick(UNDO_MS - 1);
    click(button('Undo: keep "Battery life: high importance"'));
    await tick(UNDO_MS * 2);
    expect(texts()).toEqual(before);
    expect(calls("forget")).toEqual([]);
    expect(document.querySelector('[role="status"]')?.textContent).toBe(
      "Kept: Battery life: high importance",
    );
  });

  it("sends exactly one call when the 5 seconds are up, and the item is gone", async () => {
    await showSection();
    click(button('Forget "Battery life: high importance"'));
    await tick(UNDO_MS - 1);
    expect(calls("forget")).toEqual([]);
    await tick(1);
    await settle();
    expect(calls("forget")).toHaveLength(1);
    expect(texts()).not.toContain("Battery life: high importance");
    expect(() => button(/^Undo/)).toThrow();
    expect(document.querySelector('[role="status"]')?.textContent).toBe(
      "Forgotten: Battery life: high importance",
    );
  });

  it("leaving the page within the 5 seconds still forgets, with the sign-in read when Forget was pressed", async () => {
    await showSection();
    click(button('Forget "Battery life: high importance"'));
    expect(calls("holdForget")).toHaveLength(1);
    act(() => root?.unmount());
    root = null;
    expect(calls("sendAsPageGoes")).toHaveLength(1);
    await tick(UNDO_MS * 2);
    expect(calls("forget")).toEqual([]); // sent once, as the page went: the timer never sends it again
  });

  it("a page going away (pagehide) sends it too, and a kept item is never sent", async () => {
    await showSection();
    click(button('Forget "Battery life: high importance"'));
    click(button('Undo: keep "Battery life: high importance"'));
    click(button('Forget "Light enough to carry to college every day"'));
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(calls("sendAsPageGoes").map((c) => c.args[0])).toHaveLength(1);
    act(() => root?.unmount());
    root = null;
    expect(calls("sendAsPageGoes")).toHaveLength(1);
  });

  it("a 404 on a waiting forget leaves another item's open form as it was", async () => {
    sessionStorage.setItem(MOCK_MEMORY_KEY, "gone");
    service = spy();
    setMemoryServiceForTests(service);
    await showSection();
    const newest = texts()[0]!;
    click(button(`Forget "${newest}"`));
    click(button('Change "Light enough to carry to college every day"'));
    const words = document.querySelector<HTMLInputElement>('form input[type="text"]')!;
    type(words, "Half my typing");
    await tick(UNDO_MS);
    await settle();
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(MEMORY_MESSAGES.notFound);
    expect(document.querySelector<HTMLInputElement>('form input[type="text"]')?.value).toBe(
      "Half my typing",
    );
  });

  it("Forget everything that fails changes nothing, and the forgets waiting for Undo wait again", async () => {
    await act(async () => {
      await import("@/components/account/ForgetAllDialog"); // the lazy dialog, ready before it is opened
    });
    await showSection();
    const before = texts();
    click(button('Forget "Battery life: high importance"'));
    click(button("Forget everything"));
    await settle();
    service.failNext(new MemoryError("unavailable"));
    const confirm = [
      ...document.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button'),
    ].find((b) => b.textContent?.trim() === "Forget everything")!;
    click(confirm);
    await settle();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      "We couldn't forget everything. Nothing was changed.",
    );
    expect(texts()).toEqual(before.filter((t) => t !== "Battery life: high importance"));
    expect(button('Undo: keep "Battery life: high importance"')).toBeTruthy();
    await tick(UNDO_MS);
    await settle();
    expect(calls("forget")).toHaveLength(1); // the waiting forget went after all
  });

  it("a preference already gone (404) refreshes the list and says why", async () => {
    sessionStorage.setItem(MOCK_MEMORY_KEY, "gone");
    service = spy();
    setMemoryServiceForTests(service);
    await showSection();
    const newest = texts()[0]!;
    click(button(`Forget "${newest}"`));
    await tick(UNDO_MS);
    await settle();
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(MEMORY_MESSAGES.notFound);
    expect(calls("list")).toHaveLength(2);
    expect(texts()).not.toContain(newest);
  });

  it("any other failure puts the item back and says so", async () => {
    await showSection();
    service.failNext(new MemoryError("unavailable"));
    click(button('Forget "Battery life: high importance"'));
    await tick(UNDO_MS);
    await tick();
    expect(texts()).toContain("Battery life: high importance");
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      `We couldn't forget "Battery life: high importance".`,
    );
  });
});

describe("change", () => {
  const form = () => document.querySelector<HTMLFormElement>("form")!;
  const weightBox = () => form().querySelector<HTMLInputElement>('input[type="number"]')!;
  const wordsBox = () => form().querySelector<HTMLInputElement>('input[type="text"]')!;

  it("a priority's weight, in a number box: only the weight is sent, and the app's own words follow it", async () => {
    await showSection();
    click(button('Change "Battery life: high importance"'));
    expect(weightBox().value).toBe("8");
    type(weightBox(), "5");
    submit(form());
    await tick();
    expect(calls("edit")[0]!.args).toEqual([expect.any(String), { weight: 5 }]);
    expect(texts()).toContain("Battery life: medium importance");
    expect(document.querySelector("form")).toBeNull();
  });

  it("refuses a weight outside 0 to 10 and empty words, without a call", async () => {
    await showSection();
    click(button('Change "Battery life: high importance"'));
    type(weightBox(), "11");
    submit(form());
    expect(form().textContent).toContain("Use a number from 0 to 10.");
    type(weightBox(), "4");
    type(wordsBox(), "   ");
    submit(form());
    expect(form().textContent).toContain("Use 1 to 200 characters.");
    expect(calls("edit")).toEqual([]);
  });

  it("Escape closes the form without a call", async () => {
    await showSection();
    click(button('Change "Battery life: high importance"'));
    type(weightBox(), "2");
    act(() => {
      weightBox().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(document.querySelector("form")).toBeNull();
    expect(calls("edit")).toEqual([]);
  });

  it("new words that couldn't be matched yet show the backend's note", async () => {
    sessionStorage.setItem(MOCK_MEMORY_KEY, "unmatched");
    service = spy();
    setMemoryServiceForTests(service);
    await showSection();
    click(button('Change "Battery life: high importance"'));
    type(wordsBox(), "Lasts a whole day");
    submit(form());
    await tick();
    expect(calls("edit")[0]!.args[1]).toEqual({ text: "Lasts a whole day" });
    expect(document.body.textContent).toContain(MOCK_NOTE);
  });

  it("a preference changed meanwhile (409) refreshes the list and says why", async () => {
    sessionStorage.setItem(MOCK_MEMORY_KEY, "changed");
    service = spy();
    setMemoryServiceForTests(service);
    await showSection();
    click(button('Change "Battery life: high importance"'));
    type(weightBox(), "3");
    submit(form());
    await tick();
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(MEMORY_MESSAGES.changed);
    expect(calls("list")).toHaveLength(2);
    expect(document.querySelector("form")).toBeNull();
  });

  it("a switch that fails says so, and the must-have stays as it was", async () => {
    await showSection();
    service.failNext(new MemoryError("unavailable"));
    click(button('Switch off "Must have: at least 16 GB RAM"'));
    expect(button('Saving… "Must have: at least 16 GB RAM"').textContent).toBe("Saving…");
    await tick();
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(
      `We couldn't switch off "Must have: at least 16 GB RAM". ${MEMORY_MESSAGES.unavailable}`,
    );
    expect(button('Switch off "Must have: at least 16 GB RAM"')).toBeTruthy();
  });

  it("a preference gone while it was edited (404) closes the form and refreshes the list", async () => {
    await showSection();
    click(button('Change "Battery life: high importance"'));
    type(weightBox(), "4");
    service.failNext(new MemoryError("notFound"));
    submit(form());
    await tick();
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(MEMORY_MESSAGES.notFound);
    expect(document.querySelector("form")).toBeNull();
    expect(calls("list")).toHaveLength(2);
  });

  it("switches a must-have off and on", async () => {
    await showSection();
    click(button('Switch off "Must have: at least 16 GB RAM"'));
    await tick();
    expect(calls("edit")[0]!.args[1]).toEqual({ active: false });
    click(button('Switch on "Must have: at least 16 GB RAM"'));
    await tick();
    expect(calls("edit")[1]!.args[1]).toEqual({ active: true });
  });
});

describe("Download my data", () => {
  it("saves shopsense-memory.json, holding no embedding", async () => {
    let saved: Blob | null = null;
    let name = "";
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      saved = blob as Blob;
      return "blob:memory";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      name = this.download;
    });
    await showSection();
    click(button("Download my data"));
    await tick();
    expect(name).toBe(EXPORT_FILE);
    const text = await readBlob((saved as Blob | null)!);
    const file = JSON.parse(text) as { items: unknown[] };
    expect(file.items.length).toBe(rows().length);
    expect(text).not.toMatch(/embedding|user_id|email/);
  });

  it("sends a forget still waiting for its Undo first, so the file holds what is still remembered", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:memory");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await showSection();
    click(button('Forget "Battery life: high importance"'));
    click(button("Download my data"));
    await tick();
    expect(service.calls.map((c) => c.name)).toEqual(["list", "holdForget", "forget", "download"]);
  });
});

describe("the delete dialog", () => {
  async function openDialog() {
    const auth = fakeAuth();
    setAuthServiceForTests(auth.auth);
    await render(<DeleteAccountDialog onClose={() => {}} />);
    const box = document.querySelector<HTMLInputElement>("#confirm-delete")!;
    const form = box.closest("form")!;
    return { auth, box, form };
  }

  it("lists exactly what goes", async () => {
    await openDialog();
    const dialog = document.querySelector('[role="alertdialog"]')!;
    for (const what of [
      "every request and search saved with your account",
      "everything ShopSense remembers",
      "your profile",
      "your sign-in",
    ])
      expect(dialog.textContent).toContain(what);
  });

  it.each(["", "delet", "remove", "deleted", "yes delete"])(
    "refuses %j without a call",
    async (word) => {
      const { box, form } = await openDialog();
      type(box, word);
      submit(form);
      await tick();
      expect(document.querySelector("#confirm-delete-problem")?.textContent).toBe(TYPE_DELETE);
      expect(box.getAttribute("aria-invalid")).toBe("true");
      expect(calls("deleteAccount")).toEqual([]);
    },
  );

  it("deletes with the word, then signs out to the confirmation page", async () => {
    const { auth, box, form } = await openDialog();
    type(box, "delete");
    submit(form);
    await tick();
    expect(calls("deleteAccount")).toHaveLength(1);
    expect(auth.signedOut).toEqual([DELETED_PATH]);
    expect(sessionStorage.getItem(DELETED_KEY)).toBe("1"); // the confirmation page says "deleted" only then
  });

  it("when the deletion isn't confirmed yet, asking again finishes it", async () => {
    sessionStorage.setItem(MOCK_MEMORY_KEY, "delete-unfinished");
    service = spy();
    setMemoryServiceForTests(service);
    const { auth, box, form } = await openDialog();
    type(box, "delete");
    submit(form);
    await tick();
    expect(document.querySelector("#confirm-delete-problem")?.textContent).toBe(
      MOCK_DELETE_UNFINISHED,
    );
    expect(sessionStorage.getItem(DELETED_KEY)).toBeNull();
    submit(form);
    await tick();
    expect(auth.signedOut).toEqual([DELETED_PATH]);
  });

  it("says why when the backend can't delete it, and stays open, signed in", async () => {
    sessionStorage.setItem(MOCK_MEMORY_KEY, "delete-fails");
    service = spy();
    setMemoryServiceForTests(service);
    const { auth, box, form } = await openDialog();
    type(box, "delete");
    submit(form);
    await tick();
    expect(document.querySelector("#confirm-delete-problem")?.textContent).toContain(
      "We couldn't delete your account just now.",
    );
    expect(auth.signedOut).toEqual([]);
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull();
  });
});
