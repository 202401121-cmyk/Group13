import { describe, expect, it } from "vitest";
import table from "@contracts/examples/rough_levels.json";
import { normalizePercents, rubricFromInterview, weightRows } from "@/lib/results/rubric";
import { initialResearch, researchReducer } from "@/lib/results/machine";
import { buildMockResult } from "@/lib/results/mockService";

describe("weights", () => {
  it("largest remainder sums to exactly 100", () => {
    expect(normalizePercents([1, 1, 1])).toEqual([34, 33, 33]);
    expect(normalizePercents([3, 2, 2, 2, 2, 2]).reduce((a, b) => a + b)).toBe(100);
  });
  it("sorts by weight, stable on ties", () => {
    const rows = weightRows([
      { criterionId: "a", title: "A", weight: 2 },
      { criterionId: "b", title: "B", weight: 3 },
      { criterionId: "c", title: "C", weight: 2 },
    ]);
    expect(rows.map((r) => r.criterionId)).toEqual(["b", "a", "c"]);
  });
  it("typed or skipped answers use the standard weight", () => {
    const r = rubricFromInterview({
      criteria: [
        { id: "x", title: "X", definition: "" },
        { id: "y", title: "Y", definition: "" },
        { id: "z", title: "Z", definition: "" },
      ],
      answers: {
        x: { status: "answered", value: "Very important" },
        y: { status: "answered", value: "I game a lot" },
        z: { status: "skipped", value: null },
      },
    });
    expect(r.map((i) => i.origin)).toEqual(["answer", "standard", "standard"]);
    expect(r[0]!.weight).toBe(table.weights.high);
    expect(r[1]!.weight).toBe(table.weights.medium);
  });

  const weightFor = (value: string) =>
    rubricFromInterview({
      criteria: [{ id: "x", title: "X", definition: "" }],
      answers: { x: { status: "answered", value } },
    })[0]!;

  it.each([
    ["High", "high"],
    ["Medium", "medium"],
    ["Low", "low"],
    ["Good enough", "good_enough"],
  ] as const)("the %s button weighs what the shared table says", (value, level) => {
    expect(weightFor(value)).toMatchObject({ weight: table.weights[level], origin: "answer" });
  });

  it("typed words that happen to name something built in use the standard weight", () => {
    for (const value of ["constructor", "toString", "__proto__"]) {
      expect(weightFor(value), value).toMatchObject({
        weight: table.weights.medium,
        origin: "standard",
      });
    }
  });

  it.each([
    ["7.5", 7.5],
    ["0", 0],
    ["10", 10],
  ])("an exact %s weighs itself, and counts as an answer", (value, weight) => {
    expect(weightFor(value)).toMatchObject({ weight, origin: "answer" });
  });
});

describe("research machine", () => {
  const SEARCH = `srch_${"a".repeat(24)}`;
  const starting = researchReducer(initialResearch, { type: "START" });
  const started = researchReducer(starting, {
    type: "RUNNING",
    runId: 1,
    searchId: SEARCH,
    startedAt: 0,
    events: [],
  });
  const event = (id: number) => ({
    id,
    stage: "research",
    status: "started" as const,
    message: "m",
    sources: {},
  });
  const stopping = researchReducer(started, { type: "STOP" });

  it("ignores a second start", () => {
    expect(researchReducer(starting, { type: "START" })).toBe(starting);
    expect(researchReducer(started, { type: "START" })).toBe(started);
    expect(researchReducer(stopping, { type: "START" })).toBe(stopping);
  });
  it("a start gets a search ID before it counts as researching", () => {
    expect(starting.status).toBe("starting");
    expect(started).toMatchObject({ status: "researching", searchId: SEARCH, connection: "live" });
  });
  it("ignores late results after stopping", () => {
    const stopped = researchReducer(stopping, { type: "STOPPED", runId: 1 });
    expect(stopped.status).toBe("stopped");
    const late = researchReducer(stopped, { type: "DONE", runId: 1, result: {} as never });
    expect(late).toBe(stopped);
  });
  it("timeout cannot overwrite a finished run", () => {
    const done = researchReducer(started, { type: "DONE", runId: 1, result: {} as never });
    expect(researchReducer(done, { type: "TIMEOUT", runId: 1 })).toBe(done);
  });
  it("events from an old run are ignored", () => {
    const s = researchReducer(started, { type: "EVENTS", runId: 0, events: [event(1)] });
    expect(s).toBe(started);
  });
  it("a repeated event changes nothing", () => {
    const once = researchReducer(started, { type: "EVENTS", runId: 1, events: [event(1)] });
    expect(researchReducer(once, { type: "EVENTS", runId: 1, events: [event(1)] })).toBe(once);
  });
  it("a repeated event doesn't end the quiet notice", () => {
    const once = researchReducer(started, { type: "EVENTS", runId: 1, events: [event(1)] });
    const quiet = researchReducer(once, { type: "QUIET", runId: 1 });
    expect(researchReducer(quiet, { type: "EVENTS", runId: 1, events: [event(1)] })).toBe(quiet);
  });
  it("two events of one stage both count: they are told apart by ID", () => {
    const both = researchReducer(started, {
      type: "EVENTS",
      runId: 1,
      events: [event(1), { ...event(2), status: "done" }],
    });
    expect(both).toMatchObject({ events: [{ id: 1 }, { id: 2 }] });
  });
  it("events still count while a stop is on its way", () => {
    const s = researchReducer(stopping, { type: "EVENTS", runId: 1, events: [event(1)] });
    expect(s).toMatchObject({ status: "stopping", events: [{ id: 1 }] });
  });
  it("a stop that fails goes back to researching, and says so", () => {
    const s = researchReducer(stopping, { type: "STOP_FAILED", runId: 1 });
    expect(s).toMatchObject({ status: "researching", stopNote: "failed", searchId: SEARCH });
  });
  it("a result after a stop says the search had nearly finished", () => {
    const late = researchReducer(stopping, { type: "TOO_LATE", runId: 1 });
    expect(late).toMatchObject({ status: "researching", stopNote: "tooLate" });
    const done = researchReducer(late, { type: "DONE", runId: 1, result: {} as never });
    expect(done).toMatchObject({ status: "done", stopCameTooLate: true });
    const raced = researchReducer(stopping, { type: "DONE", runId: 1, result: {} as never });
    expect(raced).toMatchObject({ status: "done", stopCameTooLate: true });
    const plain = researchReducer(started, { type: "DONE", runId: 1, result: {} as never });
    expect(plain).toMatchObject({ status: "done", stopCameTooLate: false });
  });
  it("a stop that was accepted ends as stopped, even if the time limit comes first", () => {
    const accepted = researchReducer(stopping, { type: "STOP_ACCEPTED", runId: 1 });
    expect(researchReducer(accepted, { type: "TIMEOUT", runId: 1 }).status).toBe("stopped");
  });
  it("a stop with no answer yet isn't called stopped when the time limit comes", () => {
    expect(researchReducer(stopping, { type: "TIMEOUT", runId: 1 }).status).toBe("timedOut");
  });
  it("a late failed check can't overwrite a connection that has come back", () => {
    const quiet = researchReducer(started, { type: "QUIET", runId: 1 });
    const checking = researchReducer(quiet, { type: "CHECK", runId: 1 });
    const back = researchReducer(checking, { type: "EVENTS", runId: 1, events: [event(1)] });
    expect(researchReducer(back, { type: "CHECK_FAILED", runId: 1 })).toBe(back);
  });
  it("a quiet connection is still quiet after a stop that didn't work", () => {
    const quiet = researchReducer(started, { type: "QUIET", runId: 1 });
    const trying = researchReducer(quiet, { type: "STOP" });
    expect(researchReducer(trying, { type: "STOP_FAILED", runId: 1 })).toMatchObject({
      status: "researching",
      connection: "quiet",
      stopNote: "failed",
    });
    expect(researchReducer(trying, { type: "TOO_LATE", runId: 1 })).toMatchObject({
      connection: "quiet",
      stopNote: "tooLate",
    });
  });
  it("the quiet minute can end while a stop is on its way", () => {
    const trying = researchReducer(stopping, { type: "QUIET", runId: 1 });
    expect(researchReducer(trying, { type: "STOP_FAILED", runId: 1 })).toMatchObject({
      connection: "quiet",
    });
  });
  it("leaving ends the run on this page, so late answers are ignored", () => {
    const left = researchReducer(started, { type: "ABANDON" });
    expect(left).toMatchObject({ status: "briefReady", runId: 2 });
    expect(researchReducer(left, { type: "DONE", runId: 1, result: {} as never })).toBe(left);
    expect(researchReducer(left, { type: "START" }).status).toBe("starting");
  });
  it("a reload's attach can end in any outcome", () => {
    const attaching = researchReducer(initialResearch, { type: "ATTACH", searchId: SEARCH });
    expect(attaching).toMatchObject({ status: "attaching", searchId: SEARCH, runId: 1 });
    expect(researchReducer(attaching, { type: "STOPPED", runId: 1 }).status).toBe("stopped");
    expect(researchReducer(attaching, { type: "DONE", runId: 1, result: {} as never }).status).toBe(
      "done",
    );
    expect(
      researchReducer(attaching, { type: "FAIL", runId: 1, kind: "notFound", message: null }),
    ).toMatchObject({ status: "error", kind: "notFound", searchId: SEARCH });
  });
  it("the connection notice goes quiet, checking, unreachable, and clears on news", () => {
    const quiet = researchReducer(started, { type: "QUIET", runId: 1 });
    expect(quiet).toMatchObject({ connection: "quiet" });
    const checking = researchReducer(quiet, { type: "CHECK", runId: 1 });
    expect(checking).toMatchObject({ connection: "checking" });
    const failed = researchReducer(checking, { type: "CHECK_FAILED", runId: 1 });
    expect(failed).toMatchObject({ connection: "unreachable" });
    const heard = researchReducer(failed, { type: "EVENTS", runId: 1, events: [event(1)] });
    expect(heard).toMatchObject({ connection: "live" });
    const read = researchReducer(checking, { type: "CHECKED", runId: 1 });
    expect(read).toMatchObject({ connection: "live" });
  });
});

describe("mock fixtures", () => {
  it("are labelled mock and deterministic", () => {
    const input = { sessionId: "s", category: "laptop", rubric: [], mustHaves: [], exclusions: [] };
    expect(buildMockResult(input, "tie")).toEqual(buildMockResult(input, "tie"));
    expect(buildMockResult(input, "success").source).toBe("mock");
  });
});
