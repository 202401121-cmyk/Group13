/**
 * Every interview step kind as the shopper reads it (US-39, task #59). The live steps come from the backend's own
 * words (backend/app/interview/stage.py) through the real adapter, so the card shows what live mode sends: the
 * answer or redirect above the question, a rephrase with the same buttons, and skipped questions marked with
 * "Answer now".
 */
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswersSoFar, FocusCard } from "@/components/interview/Interview";
import { viewStep } from "@/lib/interview/liveAdapter";
import {
  DONE_MESSAGE,
  SKIPPED_NOTE,
  keptNote,
  criteriaFor,
  createMockInterviewService,
  rephraseMessage,
} from "@/lib/interview/mockService";
import { initialInterview, interviewReducer, type InterviewState } from "@/lib/interview/reducer";
import type { Step } from "@/lib/interview/types";
import { skippedCriteria, stepDisplay } from "@/lib/interview/view";
import type { Brief } from "@/lib/request/types";
import { asked, question, stepOf } from "./helpers/interviewSteps";

const Q = question("cpu_performance").text; // "CPU performance: how much does it matter to you?"
const live = (kind: Parameters<typeof stepOf>[0], message?: string) =>
  viewStep(stepOf(kind, [asked("cpu_performance", "pending")], message ? { message } : {}));

function show(ui: ReactElement): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(ui);
  return host;
}

const card = (step: Step) =>
  show(
    <FocusCard
      step={step}
      number={1}
      selected={null}
      busy={false}
      thinking={false}
      error={null}
      onReply={() => {}}
      onSkip={() => {}}
      onRetry={() => {}}
    />,
  );

describe("stepDisplay: the backend's words, where they belong", () => {
  it("a plain question is the heading alone, never also a note", () => {
    expect(stepDisplay(live("question"))).toEqual({
      note: null,
      label: null,
      heading: Q,
      question: null,
    });
  });

  it("a note before the question (a skip, a correction, a fallback) sits above it, as written", () => {
    expect(stepDisplay(live("question", `${SKIPPED_NOTE} ${Q}`))).toMatchObject({
      note: SKIPPED_NOTE,
      heading: Q,
    });
  });

  it("answer then repeat: the answer above, the same question as the heading", () => {
    const answer = "Good question. CPU performance is how fast it runs code.";
    expect(stepDisplay(live("answer_then_repeat", `${answer} ${Q}`))).toEqual({
      note: answer,
      label: null,
      heading: Q,
      question: null,
    });
  });

  it("redirect: the one line back to the question above it", () => {
    const redirect = "That's not something I can use here, so let's get back to the question.";
    expect(stepDisplay(live("redirect", `${redirect} ${Q}`))).toMatchObject({
      note: redirect,
      heading: Q,
    });
  });

  it("an answer too long to carry the question shows whole, with the question still the heading", () => {
    const long = `Good question. ${"It depends on the work. ".repeat(20)}`.trim();
    expect(stepDisplay(live("answer_then_repeat", long))).toMatchObject({ note: long, heading: Q });
  });

  it("rephrase: asked another way, in its own words, with the question kept for screen readers", () => {
    const words = rephraseMessage("CPU performance");
    expect(stepDisplay(live("rephrase", words))).toEqual({
      note: null,
      label: "Asked another way",
      heading: words,
      question: Q,
    });
  });

  it("finished carries no question", () => {
    expect(stepDisplay(live("finished", DONE_MESSAGE))).toMatchObject({ note: DONE_MESSAGE });
  });
});

describe("the question card", () => {
  it("says the question once, and why it is asked, without a toggle", () => {
    const shown = card(live("question"));
    expect(shown.querySelector("h2")).toHaveTextContent(Q);
    expect(shown.querySelector("[data-step-note]")).toBeNull();
    expect(shown.textContent?.split(Q)).toHaveLength(2);
    const why = question("cpu_performance").why_asking!;
    expect(shown.querySelector("[data-why]")).toHaveTextContent(`Why this question: ${why}`);
    expect(shown).not.toHaveTextContent("Why this question?");
  });

  it("answer then repeat shows the answer, then the question", () => {
    const answer = "Good question. CPU performance is how fast it runs code.";
    const shown = card(live("answer_then_repeat", `${answer} ${Q}`));
    expect(shown.querySelector("[data-step-note]")).toHaveTextContent(answer);
    expect(shown.querySelector("h2")).toHaveTextContent(Q);
    expect(shown.textContent?.split(Q)).toHaveLength(2);
  });

  it("a rephrase keeps the same buttons", () => {
    const shown = card(live("rephrase", rephraseMessage("CPU performance")));
    expect(shown).toHaveTextContent("Asked another way");
    expect(shown.querySelector("h2")).toHaveTextContent(rephraseMessage("CPU performance"));
    const buttons = (host: HTMLElement) =>
      [...host.querySelectorAll("button")].map((b) => b.textContent);
    expect(buttons(shown)).toEqual(buttons(card(live("question"))));
    for (const reply of ["Low", "Medium", "High", "Good enough"]) {
      expect(buttons(shown).some((b) => b?.endsWith(reply))).toBe(true);
    }
  });
});

describe("skipped questions (US-39: marked, to answer later)", () => {
  const { criteria } = criteriaFor({
    product: "Laptop",
    usedFor: "coding",
    budget: null,
    region: null,
    mustHaves: [],
    exclusions: [],
    source: "model",
  });
  const state: InterviewState = {
    ...initialInterview,
    phase: "asking",
    criteria,
    answers: {
      cpu: { status: "answered", value: "High" },
      memory: { status: "skipped", value: null },
    },
  };

  it("are listed in the criteria's order", () => {
    expect(skippedCriteria(state).map((c) => c.id)).toEqual(["memory"]);
  });

  it("each has an Answer now button in the answers panel", () => {
    const shown = show(<AnswersSoFar state={state} onChange={() => {}} />);
    const title = criteria.find((c) => c.id === "memory")!.title;
    const answer = shown.querySelector(`[aria-label="Answer now: ${title}"]`);
    expect(answer).toHaveTextContent("Answer now");
    expect(shown.querySelector(`[aria-label^="Change ${title}"]`)).toBeNull();
  });
});

describe("the mock reads as live does", () => {
  const brief: Brief = {
    product: "Laptop",
    usedFor: "coding",
    budget: null,
    region: null,
    mustHaves: [],
    exclusions: [],
    source: "model",
  };
  const { criteria } = criteriaFor(brief);

  async function walk(replies: string[]) {
    const service = createMockInterviewService(0);
    let s = interviewReducer(initialInterview, {
      type: "CRITERIA_LOADED",
      criteria,
      fallback: false,
    });
    s = interviewReducer(s, { type: "START" });
    s = interviewReducer(s, {
      type: "TURN_OK",
      result: await service.turn({ brief, criteria, answers: {} }),
    });
    const steps = [s.step!];
    for (const text of replies) {
      s = interviewReducer(s, { type: "SEND", text });
      const result = await service.turn({ brief, criteria, answers: s.answers, turn: s.pending! });
      s = interviewReducer(s, { type: "TURN_OK", result });
      steps.push(s.step!);
    }
    return { s, steps };
  }

  it("a question's message is the question", async () => {
    const { steps } = await walk([]);
    expect(steps[0]!.note).toBe(steps[0]!.question);
  });

  it("a rephrase keeps the question and its buttons, in the backend's words", async () => {
    const { steps } = await walk(["not sure"]);
    const [first, again] = steps;
    expect(again).toMatchObject({
      kind: "rephrase",
      criterionId: first!.criterionId,
      question: first!.question,
      replies: first!.replies,
      note: rephraseMessage(criteria[0]!.title),
    });
  });

  it("the second unclear reply skips the question, marks it, and moves on", async () => {
    const { s, steps } = await walk(["not sure", "not sure"]);
    const next = steps[2]!;
    expect(s.answers[criteria[0]!.id]).toEqual({ status: "skipped", value: null });
    expect(next.criterionId).not.toBe(criteria[0]!.id);
    expect(next.note).toBe(`${SKIPPED_NOTE} ${next.question}`);
    expect(stepDisplay(next)).toMatchObject({ note: SKIPPED_NOTE, heading: next.question });
  });

  it("an unclear reply then an off-topic one counts as two, as live", async () => {
    const { s, steps } = await walk(["not sure", "what about the price"]);
    expect(s.answers[criteria[0]!.id]).toEqual({ status: "skipped", value: null });
    expect(steps[2]!.note?.startsWith(SKIPPED_NOTE)).toBe(true);
  });

  it("a question back in between doesn't reset the count; an answer does", async () => {
    const asked = await walk(["not sure", "what does that mean?", "not sure"]);
    expect(asked.steps.map((x) => x.kind)).toEqual([
      "question",
      "rephrase",
      "answer_then_repeat",
      "question",
    ]);
    expect(asked.s.answers[criteria[0]!.id]?.status).toBe("skipped");

    const answered = await walk(["not sure", "High", "not sure"]);
    expect(answered.steps.map((x) => x.kind)).toEqual([
      "question",
      "rephrase",
      "question",
      "rephrase",
    ]);
  });

  it("finished says the backend's last words, after any skip note", async () => {
    const replies = criteria.filter((c) => c.id !== "weight").map(() => "High");
    const { s, steps } = await walk(replies);
    expect(s.phase).toBe("finished");
    expect(steps.at(-1)!.note).toBe(DONE_MESSAGE);
  });

  it("skipping the last question finishes with the skip note, then the backend's last words", async () => {
    const replies = criteria.filter((c) => c.id !== "weight").map(() => "High");
    replies.splice(-1, 1, "not sure", "not sure");
    const { s, steps } = await walk(replies);
    expect(s.phase).toBe("finished");
    expect(steps.at(-1)!.note).toBe(`${SKIPPED_NOTE} ${DONE_MESSAGE}`);
    expect(s.answers[criteria.at(-1)!.id]).toEqual({ status: "skipped", value: null });
  });

  it("a correction that can't be read twice keeps the earlier answer, and says so", async () => {
    const service = createMockInterviewService(0);
    const answered = { [criteria[0]!.id]: { status: "answered" as const, value: "High" } };
    const turn = (text: string) =>
      service.turn({
        brief,
        criteria,
        answers: answered,
        turn: { criterionId: criteria[0]!.id, text, revise: true },
      });
    expect((await turn("not sure")).step.kind).toBe("rephrase");
    const r = await turn("not sure");
    expect(r.answers[criteria[0]!.id]).toEqual({ status: "answered", value: "High" });
    expect(r.step.note?.startsWith(keptNote(criteria[0]!.title))).toBe(true);
  });

  it("going back to another question starts its count again, as live does", async () => {
    const service = createMockInterviewService(0);
    const [cpu, memory] = criteria;
    const turn = (id: string, text: string, answers = {}) =>
      service.turn({ brief, criteria, answers, turn: { criterionId: id, text, revise: false } });
    expect((await turn(memory!.id, "what about the price")).step.kind).toBe("redirect");
    await turn(cpu!.id, "High"); // went back to CPU and answered it
    expect((await turn(memory!.id, "not sure")).step.kind).toBe("rephrase");
  });
});
