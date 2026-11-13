/**
 * "What does this mean?" under a question (US-07): the live adapter's mapping, the mock's terms, the reducer
 * keeping them when a question is reopened, and the TermHelp control through the real components, rendered
 * with React itself. Opening an explanation is never an answer: it sends nothing and changes no state but its own.
 */
import { act, createRef, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { FocusCard } from "@/components/interview/Interview";
import { TermHelp } from "@/components/interview/TermHelp";
import { viewStep } from "@/lib/interview/liveAdapter";
import { criteriaFor, runTurn } from "@/lib/interview/mockService";
import { questionFor } from "@/lib/interview/questions";
import { initialInterview, interviewReducer, toInterviewSnapshot } from "@/lib/interview/reducer";
import * as service from "@/lib/interview/service";
import type { QuestionTerm, Step } from "@/lib/interview/types";
import type { Brief } from "@/lib/request/types";
import type { InterviewStep } from "@/types/contracts/InterviewStep";
import { asked, stepOf } from "./helpers/interviewSteps";

const CPU: QuestionTerm = {
  term: "CPU performance",
  explanation: "The main chip that does the work. A faster one builds code with less waiting.",
};
const RAM: QuestionTerm = {
  term: "RAM",
  explanation: "Short-term memory. More of it keeps many apps open without slowing down.",
};

const cpuCriterion = { id: "cpu", title: "CPU performance", definition: "How fast it is." };
const withTerms = (terms: QuestionTerm[], id = "q_cpu"): Step => ({
  ...questionFor(cpuCriterion, terms),
  questionId: id,
});

const brief = (product: string): Brief => ({
  product,
  usedFor: "coding",
  budget: null,
  region: null,
  mustHaves: [],
  exclusions: [],
  source: "model",
});

let root: Root | null = null;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function render(element: ReactElement) {
  const host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  act(() => root!.render(element));
}

function rerender(element: ReactElement) {
  act(() => root!.render(element));
}

function toggles(): HTMLButtonElement[] {
  return [...document.querySelectorAll("button")].filter((b) =>
    /^What does .* mean\?$/.test(b.textContent?.trim() ?? ""),
  );
}

function regionOf(button: HTMLButtonElement): HTMLElement {
  const id = button.getAttribute("aria-controls");
  const region = id ? document.getElementById(id) : null;
  if (!region) throw new Error("the toggle controls no element");
  return region;
}

function card(step: Step) {
  const handlers = { onReply: vi.fn(), onSkip: vi.fn(), onRetry: vi.fn() };
  return {
    handlers,
    element: (
      <FocusCard
        ref={createRef<HTMLElement>()}
        step={step}
        number={1}
        selected={null}
        busy={false}
        thinking={false}
        error={null}
        {...handlers}
      />
    ),
  };
}

describe("the live adapter", () => {
  it("passes the backend's terms through, as term and explanation", () => {
    const step = stepOf("question", [asked("cpu_performance", "pending")]);
    const withOne: InterviewStep = {
      ...step,
      question: {
        ...step.question!,
        terms: [
          {
            term: "CPU performance",
            criterion_key: "cpu_performance",
            explanation: CPU.explanation,
          },
        ],
      },
    };
    expect(viewStep(withOne).terms).toEqual([CPU]);
  });

  it("gives a question without terms an empty list, never an invented one", () => {
    const step = stepOf("question", [asked("cpu_performance", "pending")]);
    expect(viewStep(step).terms).toEqual([]);
    const { terms: _dropped, ...older } = step.question!;
    const noField = { ...step, question: older } as unknown as InterviewStep;
    expect(viewStep(noField).terms).toEqual([]);
  });

  it("a finished step has no terms", () => {
    expect(viewStep(stepOf("finished", [])).terms).toEqual([]);
  });
});

describe("the mock", () => {
  it("explains a word in the first laptop question, so it is easy to reach", () => {
    const { criteria } = criteriaFor(brief("Laptop"));
    const first = runTurn({ brief: brief("Laptop"), criteria, answers: {} }).step;
    expect(first.criterionId).toBe("cpu");
    expect(first.terms).toHaveLength(1);
    expect(first.terms[0]!.explanation.split(/\s+/).length).toBeLessThanOrEqual(40);
  });

  it("explains processor speed for a phone, and nothing for value for money", () => {
    const { criteria } = criteriaFor(brief("Phone"));
    const answers = Object.fromEntries(
      criteria
        .filter((c) => c.id !== "performance")
        .map((c) => [c.id, { status: "answered" as const, value: "High" }]),
    );
    const step = runTurn({ brief: brief("Phone"), criteria, answers }).step;
    expect(step.terms).toEqual([
      {
        term: "Processor speed",
        explanation:
          "How smoothly the phone runs apps, games and many open tabs without lag, stutter or heating up.",
      },
    ]);
    const onlyValue = Object.fromEntries(
      criteria
        .filter((c) => c.id !== "value")
        .map((c) => [c.id, { status: "answered" as const, value: "High" }]),
    );
    const value = runTurn({ brief: brief("Phone"), criteria, answers: onlyValue }).step;
    expect(value.criterionId).toBe("value");
    expect(value.terms).toEqual([]);
  });

  it("keeps the terms when the question is asked again or another way", () => {
    const { criteria } = criteriaFor(brief("Laptop"));
    for (const text of ["what does that mean?", "not sure", "what about the price"]) {
      const step = runTurn({
        brief: brief("Laptop"),
        criteria,
        answers: {},
        turn: { criterionId: "cpu", text, revise: false },
      }).step;
      expect(step.criterionId).toBe("cpu");
      expect(step.terms).toHaveLength(1);
    }
  });
});

describe("the reducer", () => {
  it("forgets the last criteria's explanations when a new set of criteria loads", () => {
    const { criteria, fallback } = criteriaFor(brief("Laptop"));
    let s = interviewReducer(initialInterview, { type: "CRITERIA_LOADED", criteria, fallback });
    s = interviewReducer(s, { type: "START" });
    s = interviewReducer(s, {
      type: "TURN_OK",
      result: runTurn({ brief: brief("Laptop"), criteria, answers: {} }),
    });
    expect(Object.keys(s.terms)).not.toHaveLength(0);
    s = interviewReducer(s, { type: "CRITERIA_LOADED", criteria, fallback });
    expect(s.terms).toEqual({});
  });

  it("keeps a question's terms when the shopper goes back to change it", () => {
    const { criteria, fallback } = criteriaFor(brief("Laptop"));
    let s = interviewReducer(initialInterview, { type: "CRITERIA_LOADED", criteria, fallback });
    s = interviewReducer(s, { type: "START" });
    s = interviewReducer(s, {
      type: "TURN_OK",
      result: runTurn({ brief: brief("Laptop"), criteria, answers: {} }),
    });
    const terms = s.step!.terms;
    expect(terms).toHaveLength(1);
    s = interviewReducer(s, { type: "SEND", text: "High" });
    s = interviewReducer(s, {
      type: "TURN_OK",
      result: runTurn({
        brief: brief("Laptop"),
        criteria,
        answers: s.answers,
        turn: s.pending!,
      }),
    });
    expect(s.step!.criterionId).toBe("memory");
    s = interviewReducer(s, { type: "GO_BACK", criterionId: "cpu" });
    expect(s.step!.terms).toEqual(terms);
    // The snapshot carries them, so a reloaded tab keeps them too.
    const restored = interviewReducer(initialInterview, {
      type: "RESTORE",
      state: toInterviewSnapshot(s),
    });
    expect(interviewReducer(restored, { type: "GO_BACK", criterionId: "cpu" }).step!.terms).toEqual(
      terms,
    );
  });

  it("a question reopened that never had terms gets none", () => {
    const open = { ...initialInterview, phase: "asking" as const, criteria: [cpuCriterion] };
    expect(interviewReducer(open, { type: "GO_BACK", criterionId: "cpu" }).step!.terms).toEqual([]);
  });
});

describe("TermHelp", () => {
  it("is not there when the question has no terms", () => {
    render(card(withTerms([])).element);
    expect(toggles()).toHaveLength(0);
    expect(document.querySelector('[role="region"]')).toBeNull();
  });

  it("starts closed: a link-like button, aria-expanded false, the explanation not shown", () => {
    render(card(withTerms([CPU])).element);
    const [button] = toggles();
    expect(button).toBeDefined();
    expect(button!.textContent?.trim()).toBe("What does this mean?");
    expect(button!.type).toBe("button");
    expect(button!.getAttribute("aria-expanded")).toBe("false");
    const region = regionOf(button!);
    expect(region.hidden).toBe(true);
    expect(region).not.toBeVisible();
  });

  it("opens on a click: expanded, with the term in bold and its explanation", () => {
    render(card(withTerms([CPU])).element);
    const [button] = toggles();
    act(() => button!.click());
    expect(button!.getAttribute("aria-expanded")).toBe("true");
    const region = regionOf(button!);
    expect(region.hidden).toBe(false);
    expect(region.getAttribute("role")).toBe("region");
    expect(region.getAttribute("aria-label")).toBe("What CPU performance means");
    expect(region.querySelector("strong")?.textContent).toBe("CPU performance");
    expect(region.textContent).toContain(CPU.explanation);
    act(() => button!.click());
    expect(button!.getAttribute("aria-expanded")).toBe("false");
    expect(regionOf(button!).hidden).toBe(true);
  });

  it("toggling sends nothing, answers nothing and keeps focus on the button", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const serviceSpy = vi.spyOn(service, "getInterviewService");
    const { element, handlers } = card(withTerms([CPU]));
    render(element);
    const [button] = toggles();
    button!.focus();
    act(() => button!.click());
    act(() => button!.click());
    act(() => button!.click());
    expect(document.activeElement).toBe(button);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(serviceSpy).not.toHaveBeenCalled();
    expect(handlers.onReply).not.toHaveBeenCalled();
    expect(handlers.onSkip).not.toHaveBeenCalled();
    expect(handlers.onRetry).not.toHaveBeenCalled();
  });

  it("never submits a form it sits in", () => {
    const onSubmit = vi.fn((e: { preventDefault: () => void }) => e.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <TermHelp terms={[CPU]} />
      </form>,
    );
    act(() => toggles()[0]!.click());
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("names each term when there is more than one, each with its own region", () => {
    render(card(withTerms([CPU, RAM])).element);
    const buttons = toggles();
    expect(buttons.map((b) => b.textContent?.trim())).toEqual([
      "What does CPU performance mean?",
      "What does RAM mean?",
    ]);
    const ids = buttons.map((b) => b.getAttribute("aria-controls"));
    expect(new Set(ids).size).toBe(2);
    act(() => buttons[1]!.click());
    expect(buttons[0]!.getAttribute("aria-expanded")).toBe("false");
    expect(regionOf(buttons[1]!).textContent).toContain(RAM.explanation);
  });

  it("closes again when the question changes", () => {
    const first = card(withTerms([CPU], "q_cpu"));
    render(first.element);
    act(() => toggles()[0]!.click());
    expect(toggles()[0]!.getAttribute("aria-expanded")).toBe("true");
    rerender(card(withTerms([CPU], "q_cpu_again")).element);
    expect(toggles()[0]!.getAttribute("aria-expanded")).toBe("false");
  });
});
