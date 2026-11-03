import { describe, expect, it } from "vitest";
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
    expect(r[0]!.weight).toBe(3);
  });
});

describe("research machine", () => {
  const started = researchReducer(initialResearch, { type: "START", at: 0 });
  it("ignores duplicate starts", () => {
    expect(researchReducer(started, { type: "START", at: 5 })).toBe(started);
  });
  it("ignores late results after stopping", () => {
    const stopped = researchReducer(researchReducer(started, { type: "STOP" }), {
      type: "STOPPED",
    });
    const late = researchReducer(stopped, { type: "DONE", runId: 1, result: {} as never });
    expect(late.status).toBe("briefReady");
  });
  it("timeout cannot overwrite a finished run", () => {
    const done = researchReducer(started, { type: "DONE", runId: 1, result: {} as never });
    expect(researchReducer(done, { type: "TIMEOUT", runId: 1 })).toBe(done);
  });
  it("events from an old run are ignored", () => {
    const s = researchReducer(started, {
      type: "EVENT",
      runId: 0,
      event: { type: "stage_done", stage: 0 },
    });
    expect(s).toBe(started);
  });
});

describe("mock fixtures", () => {
  it("are labelled mock and deterministic", () => {
    const input = { sessionId: "s", category: "laptop", rubric: [], mustHaves: [], exclusions: [] };
    expect(buildMockResult(input, "tie")).toEqual(buildMockResult(input, "tie"));
    expect(buildMockResult(input, "success").source).toBe("mock");
  });
});
