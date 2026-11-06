import { describe, expect, it, vi } from "vitest";
import { TIMEOUTS } from "@/lib/api/client";
import { checkContract } from "@/lib/api/schemas";
import {
  SKIP_REPLY,
  createLiveInterviewService,
  criteriaFromSet,
  rubricItems,
} from "@/lib/interview/liveAdapter";
import type { InterviewStep } from "@/types/contracts/InterviewStep";
import { fakeBackend, hang, json, scripted } from "./helpers/fakeBackend";
import { SID, asked, criteria, rubric, stepOf } from "./helpers/interviewSteps";

const brief = {
  product: "Laptop",
  usedFor: "coding",
  budget: null,
  region: "India",
  mustHaves: [],
  exclusions: [],
  source: "model" as const,
};
const viewCriteria = criteriaFromSet(criteria).criteria;
const base = { sessionId: SID, brief, criteria: viewCriteria, answers: {} };

const STEPS: Record<InterviewStep["kind"], InterviewStep> = {
  question: stepOf("question", [asked("cpu_performance", "pending")]),
  answer_then_repeat: stepOf("answer_then_repeat", [asked("cpu_performance", "pending")], {
    message: "It's how fast it builds code. CPU performance: how much does it matter to you?",
  }),
  rephrase: stepOf("rephrase", [asked("cpu_performance", "pending")], {
    message: "Please pick one of the buttons, or type a number from 0 to 10.",
  }),
  redirect: stepOf("redirect", [asked("cpu_performance", "pending")], {
    message: "We'll use your budget later. CPU performance: how much does it matter to you?",
  }),
  finished: stepOf("finished", [
    asked("cpu_performance", "answered", "High"),
    asked("battery_life", "skipped", "Skip"),
  ]),
};

describe("fixtures", () => {
  it("every hand-built step passes the InterviewStep schema", async () => {
    for (const step of Object.values(STEPS)) {
      await expect(checkContract("InterviewStep", step)).resolves.toBeTruthy();
    }
  });
});

describe("criteria", () => {
  it("maps the CriteriaSet and calls the session's criteria route", async () => {
    const { client, calls } = fakeBackend(() => json(200, criteria));
    const r = await createLiveInterviewService(client).getCriteria(brief, SID);
    expect(calls[0]).toMatchObject({ method: "POST", path: `/api/presearch/${SID}/criteria` });
    expect(r.fallback).toBe(false);
    expect(r.criteria[0]).toEqual({
      id: "cpu_performance",
      title: "CPU performance",
      definition: criteria.criteria[0]!.definition,
      lowerIsBetter: false,
      valueForMoney: false,
    });
    expect(r.criteria.find((c) => c.id === "weight")?.lowerIsBetter).toBe(true);
    expect(r.criteria.filter((c) => c.valueForMoney).map((c) => c.id)).toEqual(["value_for_money"]);
    expect(r.criteria.every((c) => c.tip === undefined && c.dependsOn === undefined)).toBe(true);
  });

  it("says when the backend used its fallback list", async () => {
    const { client } = fakeBackend(() => json(200, { ...criteria, source: "fallback" }));
    await expect(createLiveInterviewService(client).getCriteria(brief, SID)).resolves.toMatchObject(
      {
        fallback: true,
      },
    );
  });
});

describe("steps", () => {
  for (const kind of Object.keys(STEPS) as InterviewStep["kind"][]) {
    it(`maps a '${kind}' step`, async () => {
      const step = STEPS[kind];
      const { client } = fakeBackend(() => json(200, step));
      const r = await createLiveInterviewService(client).turn(base);
      expect(r.step.kind).toBe(kind);
      expect(r.step.note).toBe(step.message);
      if (kind === "finished") {
        expect(r.step).toMatchObject({ criterionId: null, question: null, why: null, replies: [] });
        expect(r.rubric).toHaveLength(rubric.weights.length);
      } else {
        expect(r.step).toMatchObject({
          criterionId: "cpu_performance",
          question: step.question!.text,
          why: step.question!.why_asking,
          replies: ["Low", "Medium", "High"], // Skip has its own button
        });
        expect(r.rubric).toBeUndefined();
      }
    });
  }
});

describe("turns", () => {
  it("starts, replies and skips through the session's interview routes", async () => {
    const { client, calls } = fakeBackend(
      scripted(json(200, STEPS.question), json(200, STEPS.question), json(200, STEPS.finished)),
    );
    const svc = createLiveInterviewService(client);
    await svc.turn(base);
    await svc.turn({
      ...base,
      turn: { criterionId: "cpu_performance", text: "High", revise: false },
    });
    await svc.turn({ ...base, turn: { criterionId: "battery_life", text: null, revise: false } });
    expect(calls.map((c) => [c.path, c.body])).toEqual([
      [`/api/presearch/${SID}/interview/start`, undefined],
      [`/api/presearch/${SID}/interview/reply`, { reply_text: "High" }],
      [`/api/presearch/${SID}/interview/reply`, { reply_text: SKIP_REPLY }],
    ]);
  });

  it("answers come from the backend's history, including remembered ones", async () => {
    const step = stepOf(
      "question",
      [
        asked("cpu_performance", "answered", "High"),
        asked("battery_life", "skipped", "Skip"),
        asked("weight", "remembered"),
        asked("keyboard_comfort", "pending"),
      ],
      {
        key: "keyboard_comfort",
        remembered: [
          {
            text: "under 1.5 kg",
            criterion_key: "weight",
            weight: 8,
            similarity: 0.9,
            age_days: 3,
          },
        ],
      },
    );
    const { client } = fakeBackend(() => json(200, step));
    const r = await createLiveInterviewService(client).turn(base);
    expect(r.answers).toEqual({
      cpu_performance: { status: "answered", value: "High" },
      battery_life: { status: "skipped", value: null },
      weight: { status: "remembered", value: "under 1.5 kg" },
    });
  });

  it("going back reopens the question by its id, then sends the new answer", async () => {
    const before = stepOf(
      "question",
      [asked("cpu_performance", "answered", "High"), asked("battery_life", "answered", "Low")],
      { key: "keyboard_comfort" },
    );
    const reopened = stepOf("question", [
      asked("cpu_performance", "pending", "High"),
      asked("battery_life", "answered", "Low"),
    ]);
    // The new "Low" for CPU makes the backend re-plan: battery life must be asked again.
    const after = stepOf("question", [asked("cpu_performance", "answered", "Low")], {
      key: "battery_life",
    });
    const { client, calls } = fakeBackend(
      scripted(json(200, before), json(200, reopened), json(200, after)),
    );
    const svc = createLiveInterviewService(client);
    const first = await svc.turn(base);
    const r = await svc.turn({
      ...base,
      answers: first.answers,
      turn: { criterionId: "cpu_performance", text: "Low", revise: true },
    });
    expect(calls.slice(1).map((c) => [c.path, c.body])).toEqual([
      [`/api/presearch/${SID}/interview/back`, { question_id: "q_cpu_performance" }],
      [`/api/presearch/${SID}/interview/reply`, { reply_text: "Low" }],
    ]);
    expect(r.laterChanged).toBe(true);
    expect(r.step.criterionId).toBe("battery_life");
  });

  it("an ordinary reply never reports later changes", async () => {
    const { client } = fakeBackend(() => json(200, STEPS.question));
    const r = await createLiveInterviewService(client).turn({
      ...base,
      answers: { battery_life: { status: "answered", value: "Low" } },
      turn: { criterionId: "cpu_performance", text: "High", revise: false },
    });
    expect(r.laterChanged).toBe(false);
  });

  it("the finished rubric uses the backend's shares and labels", () => {
    const items = rubricItems(rubric, viewCriteria);
    expect(items.reduce((sum, i) => sum + i.weight, 0)).toBeCloseTo(1, 6);
    expect(items.find((i) => i.criterionId === "cpu_performance")).toEqual({
      criterionId: "cpu_performance",
      title: "CPU performance",
      weight: rubric.weights[0]!.normalized,
      origin: "answer",
    });
    expect(items.map((i) => i.origin)).toEqual([
      "answer",
      "answer",
      "answer",
      "standard",
      "remembered",
      "standard",
      "standard",
    ]);
  });

  it("rejects a step that breaks the contract", async () => {
    const broken = { ...STEPS.question, kind: "chat" };
    const { client } = fakeBackend(() => json(200, broken));
    await expect(createLiveInterviewService(client).turn(base)).rejects.toMatchObject({
      kind: "bad_response",
    });
  });

  it("passes on the backend's out-of-order message", async () => {
    const detail = "The interview hasn't started yet. Please start it first.";
    const { client } = fakeBackend(() => json(409, { detail }));
    await expect(
      createLiveInterviewService(client).turn({
        ...base,
        turn: { criterionId: "cpu_performance", text: "High", revise: false },
      }),
    ).rejects.toMatchObject({ kind: "conflict", message: detail });
  });

  it("an expired session asks for a new search", async () => {
    const { client } = fakeBackend(() =>
      json(404, { detail: "This session has expired. Please start a new search." }),
    );
    await expect(createLiveInterviewService(client).turn(base)).rejects.toMatchObject({
      kind: "expired",
    });
  });

  it("a session ID in the wrong shape is never sent", async () => {
    const { client, calls } = fakeBackend(() => json(200, STEPS.question));
    await expect(
      createLiveInterviewService(client).turn({ ...base, sessionId: "abc" }),
    ).rejects.toMatchObject({ kind: "expired" });
    expect(calls).toHaveLength(0);
  });

  it("a turn gives up after 12 seconds", async () => {
    vi.useFakeTimers();
    try {
      const { client } = fakeBackend(hang);
      const pending = createLiveInterviewService(client)
        .turn(base)
        .catch((e: unknown) => e);
      await vi.advanceTimersByTimeAsync(TIMEOUTS.step);
      await expect(pending).resolves.toMatchObject({ kind: "timeout" });
    } finally {
      vi.useRealTimers();
    }
  });
});
