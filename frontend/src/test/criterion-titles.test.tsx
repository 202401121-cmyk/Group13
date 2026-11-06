/**
 * D-44: a criterion title is never lower-cased. Every title in the shared fixture must appear character for
 * character wherever the app shows it, in mock and in live mode, because only the title's writer knows that
 * "USB-C/Thunderbolt" and "iPhone" are spelled that way.
 */
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import titles from "../../../shared/fixtures/criterion_titles.json";
import { AnswersSoFar } from "@/components/interview/Interview";
import { BriefPanel } from "@/components/request/BriefPanel";
import { BriefSections } from "@/components/results/BriefSections";
import { ResultCard, TooCloseToCall } from "@/components/results/ResultCard";
import { viewStep } from "@/lib/interview/liveAdapter";
import { runTurn } from "@/lib/interview/mockService";
import { questionFor } from "@/lib/interview/questions";
import { initialInterview, interviewReducer } from "@/lib/interview/reducer";
import type { Criterion } from "@/lib/interview/types";
import { buildMockResult } from "@/lib/results/mockService";
import { weightRows } from "@/lib/results/rubric";
import { tieGroups } from "@/lib/results/view";
import { asked, stepOf } from "./helpers/interviewSteps";

const brief = {
  product: "Laptop",
  usedFor: "coding",
  budget: null,
  region: "India",
  mustHaves: [],
  exclusions: [],
  source: "model" as const,
};

const criterionFor = (title: string): Criterion => ({
  id: "the_criterion",
  title,
  definition: "What it means.",
});

/** The text a shopper reads, and what a screen reader says, for one piece of the screen. */
function shown(ui: ReactElement) {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(ui);
  const labels = [...host.querySelectorAll("[aria-label]")].map((e) =>
    e.getAttribute("aria-label"),
  );
  return { text: host.textContent ?? "", labels };
}

function turnWith(title: string, text: string) {
  const c = criterionFor(title);
  return runTurn({
    brief,
    criteria: [c],
    answers: {},
    turn: { criterionId: c.id, text, revise: false },
  });
}

function resultFor(title: string) {
  const rubric = [{ criterionId: "the_criterion", title, weight: 1 }];
  const input = { sessionId: "s", category: "laptop", rubric, mustHaves: [], exclusions: [] };
  return buildMockResult(input, "tie-first");
}

it("the fixture holds the titles the rule was written for", () => {
  expect(titles).toContain("USB-C/Thunderbolt");
  expect(titles).toContain("iPhone compatibility");
  expect(titles).toHaveLength(14);
});

describe.each(titles)("%s", (title) => {
  it("opens the question exactly as given", () => {
    expect(questionFor(criterionFor(title)).question).toBe(
      `${title}: how much does it matter to you?`,
    );
  });

  it("stays as given when the question is asked another way", () => {
    expect(turnWith(title, "not sure").step).toMatchObject({
      kind: "rephrase",
      question: `${title}: would it bother you if this were worse?`,
    });
  });

  it("stays as given when the question is repeated or the shopper is redirected", () => {
    for (const reply of ["what does that mean?", "what about the price"]) {
      expect(turnWith(title, reply).step.question).toContain(title);
    }
  });

  it("stays as given when the shopper goes back to change an answer", () => {
    const c = criterionFor(title);
    const open = { ...initialInterview, phase: "asking" as const, criteria: [c] };
    const s = interviewReducer(open, { type: "GO_BACK", criterionId: c.id });
    expect(s.step?.question).toContain(title);
  });

  it("the live adapter passes the backend's question through untouched", () => {
    const step = stepOf("question", [asked("cpu_performance", "pending")]);
    const text = `${title}: how much does it matter to you?`;
    const live = { ...step, question: { ...step.question!, text }, message: text };
    expect(viewStep(live)).toMatchObject({ question: text, note: text });
  });

  it("stays as given in the answers so far", () => {
    const c = criterionFor(title);
    const state = {
      ...initialInterview,
      phase: "asking" as const,
      criteria: [c],
      answers: { [c.id]: { status: "answered" as const, value: "High" } },
    };
    const { text, labels } = shown(<AnswersSoFar state={state} onChange={() => {}} />);
    expect(text).toContain(title);
    expect(labels).toContain(`Change ${title}`);
  });

  it("stays as given in the brief panel and on the brief page", () => {
    const row = { id: "the_criterion", title, status: "answered" as const, value: "High" };
    expect(shown(<BriefPanel brief={brief} criteria={[row]} />).text).toContain(title);

    const rows = weightRows([{ criterionId: "the_criterion", title, weight: 1 }]);
    const page = shown(<BriefSections brief={brief} rows={rows} onChange={() => {}} />);
    expect(page.text).toContain(title);
    expect(page.labels).toEqual(expect.arrayContaining([`Change ${title}`, `${title} weight`]));
  });

  it("stays as given on a result card and in what separates a tie", () => {
    const result = resultFor(title);
    const card = shown(<ResultCard product={result.products[0]!} tiedWith={[]} index={0} />);
    expect(card.text).toContain(title);
    const tie = shown(<TooCloseToCall tie={tieGroups(result.products)[0]!} />);
    expect(tie.text).toContain(`What separates them: ${title}`);
  });
});
