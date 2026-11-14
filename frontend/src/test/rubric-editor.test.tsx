/** The rubric editor (US-09, US-10) through the real component, rendered with React itself, on the mock service. */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { rubricSaved } from "@/lib/interview/rubricSaves";
import { RubricEditor, type RubricApi } from "@/components/rubric/RubricEditor";
import { ApiError, MESSAGES } from "@/lib/api/client";
import { createMockInterviewService } from "@/lib/interview/mockService";
import { ALL_ZERO_NOTE } from "@/lib/interview/rubricEdits";
import type { RubricEditView, RubricSeed } from "@/lib/interview/types";

const SID = "sess_0123456789abcdef01234567";
const RULE_WORDS = "at least 16 GB RAM";

const seed: RubricSeed = {
  brief: {
    product: "Laptop",
    usedFor: "coding",
    budget: null,
    region: "India",
    mustHaves: [RULE_WORDS],
    exclusions: ["Zenith"],
    source: "model",
  },
  items: [
    { criterionId: "cpu_performance", title: "CPU performance", weight: 8, origin: "answer" },
    { criterionId: "battery_life", title: "Battery life", weight: 5, origin: "standard" },
  ],
};

interface Spy extends RubricApi {
  sent: RubricEditView[][];
  failNext(e: Error): void;
  noteNext(note: string): void;
  holdNext(): () => void;
}

/** The mock service, recording what is saved; it can fail the next save, add a note to it, or hold it back. */
function spy(): Spy {
  const mock = createMockInterviewService(0);
  const sent: RubricEditView[][] = [];
  let failure: Error | null = null;
  let note: string | null = null;
  let hold: Promise<void> | null = null;
  return {
    sent,
    failNext: (e) => {
      failure = e;
    },
    noteNext: (n) => {
      note = n;
    },
    holdNext: () => {
      let release = () => {};
      hold = new Promise((r) => {
        release = r;
      });
      return () => release();
    },
    loadRubric: (id, s) => mock.loadRubric(id, s),
    parseExclusions: (text) => mock.parseExclusions(text),
    editRubric: async (id, edits) => {
      sent.push(edits);
      if (hold) {
        const waiting = hold;
        hold = null;
        await waiting;
      }
      if (failure) {
        const e = failure;
        failure = null;
        throw e;
      }
      const answer = await mock.editRubric(id, edits);
      if (note) {
        const n = note;
        note = null;
        return { ...answer, note: n };
      }
      return answer;
    },
  };
}

let root: Root | null = null;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

beforeEach(() => sessionStorage.clear());

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
});

function render(element: ReactElement) {
  act(() => root?.unmount());
  document.body.innerHTML = "";
  const host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  act(() => root!.render(element));
}

/** Lets the saves and loads that are under way finish. */
async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function fieldset(legend: string): HTMLFieldSetElement {
  const found = [...document.querySelectorAll("fieldset")].find(
    (f) => f.querySelector("legend")?.textContent?.trim() === legend,
  );
  if (!found) throw new Error(`no group for ${legend}`);
  return found;
}

function radio(group: HTMLElement, label: string): HTMLInputElement {
  const found = [...group.querySelectorAll("label")].find((l) => l.textContent?.trim() === label);
  const input = found?.querySelector("input") ?? (found && document.getElementById(found.htmlFor));
  if (!input) throw new Error(`no option ${label}`);
  return input as HTMLInputElement;
}

function byLabel(text: string): HTMLInputElement {
  const label = [...document.querySelectorAll("label")].find((l) => l.textContent?.trim() === text);
  const field = label && document.getElementById(label.htmlFor);
  if (!field) throw new Error(`no field labelled ${text}`);
  return field as HTMLInputElement;
}

function button(name: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find(
    (b) => b.getAttribute("aria-label") === name || b.textContent?.trim() === name,
  );
  if (!found) throw new Error(`no button ${name}`);
  return found;
}

/** Types into a field as a person would, so React sees the change. */
function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function leave(input: HTMLInputElement) {
  act(() => {
    input.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  });
}

function share(key: string): string {
  return document.querySelector(`[data-share="${key}"]`)?.textContent?.trim() ?? "";
}

function chips(): string[] {
  return [...document.querySelectorAll("[data-exclusion]")].map(
    (c) => c.getAttribute("data-exclusion") ?? "",
  );
}

/** Opens the editor as the brief page does: with the rubric the service loads first. */
async function open(service: RubricApi, s: RubricSeed = seed) {
  const initial = await service.loadRubric(SID, s);
  if (!initial) throw new Error("no rubric to edit");
  render(<RubricEditor sessionId={SID} seed={s} initial={initial} service={service} />);
}

/** Types words into the leave-out box and adds them, waiting for the save. */
async function addWords(words: string) {
  type(byLabel("Rule something out"), words);
  act(() => button("Add").click());
  await settle();
}

function alertText(): string {
  return document.querySelector('[role="alert"]')?.textContent?.trim() ?? "";
}

describe("RubricEditor", () => {
  it("each hard rule shows the shopper's words with Must-have and Preference", async () => {
    await open(spy(), seed);
    const group = fieldset(RULE_WORDS);
    expect(radio(group, "Must-have").checked).toBe(true);
    expect(radio(group, "Preference").checked).toBe(false);
  });

  it("switching a rule saves it, and a reload keeps it", async () => {
    const service = spy();
    await open(service, seed);
    act(() => radio(fieldset(RULE_WORDS), "Preference").click());
    await settle();
    expect(service.sent).toEqual([
      [{ kind: "switch_rule", ruleId: expect.any(String) as string, active: false }],
    ]);
    await open(spy(), seed); // a reload: a new page and service
    expect(radio(fieldset(RULE_WORDS), "Preference").checked).toBe(true);
  });

  it("a weight edit updates every share live and survives a rebuild", async () => {
    const service = spy();
    await open(service, seed);
    expect([share("cpu_performance"), share("battery_life")]).toEqual(["62%", "38%"]);
    const box = byLabel("CPU performance, from 0 to 10");
    type(box, "2");
    expect([share("cpu_performance"), share("battery_life")]).toEqual(["29%", "71%"]); // before any save
    expect(service.sent).toEqual([]);
    leave(box);
    await settle();
    expect(service.sent).toEqual([[{ kind: "set_weight", key: "cpu_performance", weight: 2 }]]);
    // A new answer rebuilds the rubric (battery life now 8): the shopper's own weight stays.
    const rebuilt = { ...seed, items: [seed.items[0]!, { ...seed.items[1]!, weight: 8 }] };
    await open(spy(), rebuilt);
    expect(byLabel("CPU performance, from 0 to 10").value).toBe("2");
    expect(byLabel("Battery life, from 0 to 10").value).toBe("8");
    expect(share("cpu_performance")).toBe("20%");
  });

  it('"no Xiaomi" becomes a chip', async () => {
    const service = spy();
    await open(service, seed);
    const box = byLabel("Rule something out");
    type(box, "no Xiaomi");
    act(() => button("Add").click());
    await settle();
    expect(chips()).toEqual(["Zenith", "Xiaomi"]);
    expect(service.sent).toEqual([
      [{ kind: "add_exclusion", exclusion: { kind: "brand", value: "Xiaomi", always: false } }],
    ]);
    expect(byLabel("Rule something out").value).toBe("");
  });

  it("words not understood are shown back", async () => {
    const service = spy();
    await open(service, seed);
    type(byLabel("Rule something out"), "no Zorblax");
    act(() => button("Add").click());
    await settle();
    expect(document.body.textContent).toContain("We couldn't read “Zorblax”");
    expect(service.sent).toEqual([]);
    expect(chips()).toEqual(["Zenith"]);
  });

  it("removing a chip saves it", async () => {
    const service = spy();
    await open(service, seed);
    act(() => button("Remove Zenith").click());
    expect(chips()).toEqual([]); // at once, before the save comes back
    await settle();
    expect(service.sent).toEqual([
      [{ kind: "remove_exclusion", exclusion: { kind: "brand", value: "Zenith", always: false } }],
    ]);
  });

  it("a failed save rolls back with a message", async () => {
    const service = spy();
    await open(service, seed);
    service.failNext(new ApiError("server", MESSAGES.server, 500));
    act(() => radio(fieldset(RULE_WORDS), "Preference").click());
    await settle();
    expect(radio(fieldset(RULE_WORDS), "Must-have").checked).toBe(true);
    expect(alertText()).toBe(`${MESSAGES.server} Your last change wasn't saved.`);
  });

  it("a 422 shows its message", async () => {
    const service = spy();
    await open(service, seed);
    service.failNext(new ApiError("invalid", "No criterion is called cpu_performance.", 422));
    const box = byLabel("CPU performance, from 0 to 10");
    type(box, "3");
    leave(box);
    await settle();
    expect(alertText()).toBe(
      "No criterion is called cpu_performance. Your last change wasn't saved.",
    );
    expect(byLabel("CPU performance, from 0 to 10").value).toBe("8"); // rolled back
  });

  it("every weight at 0 shows the note in a polite live region", async () => {
    await open(spy(), seed);
    for (const label of ["CPU performance, from 0 to 10", "Battery life, from 0 to 10"]) {
      const box = byLabel(label);
      type(box, "0");
      leave(box);
      await settle();
    }
    const note = document.querySelector("[data-rubric-note]");
    expect(note?.getAttribute("aria-live")).toBe("polite");
    expect(note?.textContent?.trim()).toBe(ALL_ZERO_NOTE);
  });

  it("Always saves, and a guest sees the sign-in note", async () => {
    const service = spy();
    await open(service, seed);
    await addWords("no Xiaomi");
    act(() => byLabel("Always leave out Xiaomi").click());
    await settle();
    const xiaomi = { kind: "brand", value: "Xiaomi" };
    expect(service.sent.at(-1)).toEqual([
      { kind: "remove_exclusion", exclusion: { ...xiaomi, always: false } },
      { kind: "add_exclusion", exclusion: { ...xiaomi, always: true } },
    ]);
    expect(byLabel("Always leave out Xiaomi").checked).toBe(true);
    expect(chips()).toEqual(["Zenith", "Xiaomi"]); // in its place, not moved to the end
    const noteId = byLabel("Always leave out Xiaomi").getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(noteId)?.textContent).toMatch(/once you can sign in/);
  });

  it("Always on a leave-out from the request says it can't be kept yet", async () => {
    await open(spy(), seed);
    act(() => byLabel("Always leave out Zenith").click());
    await settle();
    expect(byLabel("Always leave out Zenith").checked).toBe(false); // as the backend keeps it
    expect(alertText()).toBe(
      "“Always” can't be kept yet for “Zenith”, as it came from your request. It is still left out of this search.",
    );
  });

  it("the note shown is the one the backend's answer carries", async () => {
    const service = spy();
    await open(service, seed);
    service.noteNext("A sentence from the backend.");
    const box = byLabel("Battery life, from 0 to 10");
    type(box, "3");
    leave(box);
    await settle();
    expect(document.querySelector("[data-rubric-note]")?.textContent).toBe(
      "A sentence from the backend.",
    );
  });

  it("a save queued behind a failed one is dropped with the rollback", async () => {
    const service = spy();
    await open(service, seed);
    const release = service.holdNext();
    service.failNext(new ApiError("server", MESSAGES.server, 500));
    act(() => radio(fieldset(RULE_WORDS), "Preference").click());
    act(() => button("Remove Zenith").click()); // queued behind the first
    expect(chips()).toEqual([]);
    release();
    await settle();
    expect(service.sent).toHaveLength(1); // the second never went
    expect(radio(fieldset(RULE_WORDS), "Must-have").checked).toBe(true);
    expect(chips()).toEqual(["Zenith"]);
  });

  it("Start research can tell a save landed, and when it failed", async () => {
    const service = spy();
    await open(service, seed);
    act(() => radio(fieldset(RULE_WORDS), "Preference").click());
    await expect(rubricSaved(SID)).resolves.toBe(true);
    service.failNext(new ApiError("timeout", MESSAGES.timeout));
    act(() => radio(fieldset(RULE_WORDS), "Must-have").click());
    await expect(rubricSaved(SID)).resolves.toBe(false);
    await settle();
  });

  it("removing a chip moves the focus to the next one, then to the box", async () => {
    await open(spy(), seed);
    await addWords("no Xiaomi");
    act(() => button("Remove Zenith").click());
    await settle();
    expect(document.activeElement).toBe(button("Remove Xiaomi"));
    act(() => button("Remove Xiaomi").click());
    await settle();
    expect(document.activeElement).toBe(byLabel("Rule something out"));
  });

  it("words with nothing to leave out ask for a brand, a model or a feature", async () => {
    const service = spy();
    await open(service, seed);
    type(byLabel("Rule something out"), "no");
    act(() => button("Add").click());
    await settle();
    expect(document.body.textContent).toContain("Type a brand, a model or a feature to leave out");
    expect(service.sent).toEqual([]);
  });

  it("many leave-outs are saved 20 at a time, and 30 is the most", async () => {
    const service = spy();
    const many = (from: number, n: number) =>
      Array.from({ length: n }, (_, i) => ({
        kind: "model" as const,
        value: `Model ${from + i}`,
        always: false,
      }));
    let next = many(100, 25);
    service.parseExclusions = async () => ({ exclusions: next, notUnderstood: [] });
    await open(service, seed);
    await addWords("a long list");
    expect(service.sent.map((s) => s.length)).toEqual([20, 5]);
    next = many(200, 6);
    await addWords("six more");
    expect(chips()).toHaveLength(30);
    expect(document.body.textContent).toContain(
      "You can leave out up to 30 things, so “Model 204”",
    );
  });

  it("words whose leave-out failed to save come back to the box", async () => {
    const service = spy();
    await open(service, seed);
    service.failNext(new ApiError("server", MESSAGES.server, 500));
    await addWords("no Xiaomi");
    expect(chips()).toEqual(["Zenith"]);
    expect(byLabel("Rule something out").value).toBe("no Xiaomi");
  });

  it("a number that isn't 0 to 10 is not saved and says why", async () => {
    const service = spy();
    await open(service, seed);
    const box = byLabel("CPU performance, from 0 to 10");
    type(box, "12");
    leave(box);
    await settle();
    expect(service.sent).toEqual([]);
    expect(box.getAttribute("aria-invalid")).toBe("true");
    expect(document.body.textContent).toContain("Please enter a number from 0 to 10.");
  });
});
