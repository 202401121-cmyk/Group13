import { describe, expect, it } from "vitest";
import {
  initialInterview,
  interviewReducer,
  statusOf,
  counts,
  toInterviewSnapshot,
  type InterviewState,
} from "@/lib/interview/reducer";
import { criteriaFor, runTurn } from "@/lib/interview/mockService";
import { ALREADY_ANSWERED } from "@/lib/interview/questions";
import { noteContainsQuestion } from "@/lib/interview/view";
import type { Brief } from "@/lib/request/types";

const brief = (product: string | null): Brief => ({
  product,
  usedFor: "coding",
  budget: null,
  region: null,
  mustHaves: [],
  exclusions: [],
  source: "model",
});

/** Drive the reducer with the deterministic mock, synchronously. */
function send(s: InterviewState, text: string | null, b = brief("Laptop")) {
  const s1 = interviewReducer(s, { type: "SEND", text });
  expect(s1.phase).toBe("sending");
  const result = runTurn({
    brief: b,
    criteria: s1.criteria,
    answers: s1.answers,
    turn: s1.pending!,
  });
  return interviewReducer(s1, { type: "TURN_OK", result });
}

function started() {
  const { criteria, fallback } = criteriaFor(brief("Laptop"));
  let s = interviewReducer(initialInterview, { type: "CRITERIA_LOADED", criteria, fallback });
  s = interviewReducer(s, { type: "START" });
  return interviewReducer(s, {
    type: "TURN_OK",
    result: runTurn({ brief: brief("Laptop"), criteria, answers: {} }),
  });
}

describe("criteria", () => {
  it("laptop gets its own 6 criteria", () => {
    const r = criteriaFor(brief("Laptop"));
    expect(r.fallback).toBe(false);
    expect(r.criteria.map((c) => c.title)).toContain("CPU performance");
  });
  it("unknown products use the standard fallback list", () => {
    expect(criteriaFor(brief("Kettle")).fallback).toBe(true);
  });
});

describe("full interview scenario", () => {
  it("walks every step type through to finished", () => {
    let s = started();
    expect(s.step?.criterionId).toBe("cpu");

    s = send(s, "Very important");
    expect(s.answers["cpu"]).toEqual({ status: "answered", value: "Very important" });
    expect(s.step?.criterionId).toBe("memory");

    s = send(s, "How much do I need?");
    expect(s.step?.kind).toBe("answer_then_repeat");
    expect(s.answers["memory"]).toBeUndefined();

    s = send(s, "not sure");
    expect(s.step?.kind).toBe("rephrase");

    s = send(s, "what's the price?".replace("?", ""));
    expect(s.step?.kind).toBe("redirect");
    expect(s.step?.criterionId).toBe("memory");

    s = send(s, "Yes, a lot");
    expect(s.step?.criterionId).toBe("battery");

    s = send(s, null); // skip battery
    expect(statusOf(s, "battery")).toBe("skipped");
    expect(statusOf(s, "weight")).toBe("remembered");
    expect(s.step?.criterionId).toBe("display");

    // Change an earlier answer: memory depends on cpu, so it's re-asked.
    s = interviewReducer(s, { type: "GO_BACK", criterionId: "cpu" });
    s = send(s, "Not important");
    expect(s.laterChanged).toBe(true);
    expect(s.step?.criterionId).toBe("memory");

    s = send(s, "A little");
    s = send(s, "Very important"); // display
    s = send(s, "Somewhat important"); // value
    expect(s.phase).toBe("finished");
    expect(counts(s.answers)).toEqual({ answered: 4, skipped: 1, remembered: 1 });
  });
});

describe("sending rules", () => {
  it("ignores duplicate sends while pending", () => {
    const s1 = interviewReducer(started(), { type: "SEND", text: "Very important" });
    expect(interviewReducer(s1, { type: "SEND", text: "Not important" })).toBe(s1);
  });
  it("keeps the pending answer on failure and retries it", () => {
    let s = interviewReducer(started(), { type: "SEND", text: "Very important" });
    s = interviewReducer(s, { type: "TURN_FAILED", message: "x" });
    expect(statusOf(s, "cpu")).toBe("pending");
    s = interviewReducer(s, { type: "RETRY" });
    expect(s.phase).toBe("sending");
    expect(s.pending?.text).toBe("Very important");
  });
  it("keeps the step's question id with the pending answer, through a retry", () => {
    const asking = started();
    const step = { ...asking.step!, questionId: "q_cpu" };
    let s = interviewReducer({ ...asking, step }, { type: "SEND", text: "Very important" });
    expect(s.pending?.questionId).toBe("q_cpu");
    s = interviewReducer(s, { type: "TURN_FAILED", message: "x" });
    s = interviewReducer(s, { type: "RETRY" });
    expect(s.pending?.questionId).toBe("q_cpu");
  });
  it("a notice shows until the next reply or going back, and is never saved", () => {
    const sending = interviewReducer(started(), { type: "SEND", text: "test a conflict" });
    const result = runTurn({
      brief: brief("Laptop"),
      criteria: sending.criteria,
      answers: sending.answers,
      turn: sending.pending!,
    });
    expect(result.notice).toBe(ALREADY_ANSWERED);
    expect(result.answers).toEqual({});

    const s = interviewReducer(sending, { type: "TURN_OK", result });
    expect(s).toMatchObject({ phase: "asking", notice: ALREADY_ANSWERED });
    expect(toInterviewSnapshot(s)).not.toHaveProperty("notice");
    expect(interviewReducer(s, { type: "SEND", text: "High" }).notice).toBeNull();
    expect(interviewReducer(s, { type: "GO_BACK", criterionId: "cpu" }).notice).toBeNull();
  });
  it("caps typed answers at 500 characters", () => {
    const s = interviewReducer(started(), { type: "SEND", text: "a".repeat(600) });
    expect(s.pending?.text).toHaveLength(500);
  });
  it("does not repeat a question already in the note", () => {
    expect(
      noteContainsQuestion(
        "Sure. How important is weight to you?",
        "How important is weight to you?",
      ),
    ).toBe(true);
    expect(noteContainsQuestion("A note.", "How important is weight to you?")).toBe(false);
  });
});
