import { describe, expect, it, vi } from "vitest";
import { MESSAGES, TIMEOUTS } from "@/lib/api/client";
import { checkContract } from "@/lib/api/schemas";
import {
  SKIP_REPLY,
  createLiveInterviewService,
  criteriaFromSet,
  rubricItems,
  viewStep,
} from "@/lib/interview/liveAdapter";
import { ALREADY_ANSWERED } from "@/lib/interview/questions";
import { initialInterview, interviewReducer } from "@/lib/interview/reducer";
import type { InterviewStep } from "@/types/contracts/InterviewStep";
import { fakeBackend, hang, json, scripted } from "./helpers/fakeBackend";
import { SID, asked, criteria, rubric, sessionWith, stepOf } from "./helpers/interviewSteps";

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

/** The step as the screens hold it after the backend asked this criterion's question. */
const viewStepFor = (key: string) => viewStep(stepOf("question", [asked(key, "pending")], { key }));

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
      [
        `/api/presearch/${SID}/interview/reply`,
        { reply_text: "High", question_id: "q_cpu_performance" },
      ],
      [
        `/api/presearch/${SID}/interview/reply`,
        { reply_text: SKIP_REPLY, question_id: "q_battery_life" },
      ],
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
      [
        `/api/presearch/${SID}/interview/reply`,
        { reply_text: "Low", question_id: "q_cpu_performance" },
      ],
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

describe("question_id", () => {
  const reply = {
    criterionId: "cpu_performance",
    questionId: "q_cpu_performance",
    text: "High",
    revise: false,
  };

  it("every reply names the question from the step the shopper is looking at", async () => {
    const asking = stepOf("question", [asked("cpu_performance", "pending")]);
    const { client, calls } = fakeBackend(scripted(json(200, asking), json(200, STEPS.finished)));
    const svc = createLiveInterviewService(client);
    const first = await svc.turn(base);
    expect(first.step.questionId).toBe(asking.question!.id);

    const s = interviewReducer(
      { ...initialInterview, phase: "asking", criteria: viewCriteria, step: first.step },
      { type: "SEND", text: "High" },
    );
    await svc.turn({ ...base, turn: s.pending! });
    expect(calls[1]).toMatchObject({
      path: `/api/presearch/${SID}/interview/reply`,
      body: { reply_text: "High", question_id: asking.question!.id },
    });
  });

  it("a skip names its question too", async () => {
    const { client, calls } = fakeBackend(() => json(200, STEPS.finished));
    await createLiveInterviewService(client).turn({ ...base, turn: { ...reply, text: null } });
    expect(calls[0]?.body).toEqual({ reply_text: SKIP_REPLY, question_id: "q_cpu_performance" });
  });

  it("a tab reloaded mid-interview still sends the id the step was saved with", async () => {
    const { client, calls } = fakeBackend(() => json(200, STEPS.finished));
    await createLiveInterviewService(client).turn({
      ...base,
      turn: { ...reply, questionId: "q_not_what_the_naming_rule_gives" },
    });
    expect(calls[0]?.body).toMatchObject({ question_id: "q_not_what_the_naming_rule_gives" });
  });

  it("a retry after a timeout sends the same question_id", async () => {
    vi.useFakeTimers();
    try {
      const { client, calls } = fakeBackend(scripted(hang, json(200, STEPS.finished)));
      const svc = createLiveInterviewService(client);
      const step = viewStepFor("cpu_performance");
      let s = interviewReducer(
        { ...initialInterview, phase: "asking", criteria: viewCriteria, step },
        { type: "SEND", text: "High" },
      );
      const failed = svc.turn({ ...base, turn: s.pending! }).catch((e: unknown) => e);
      await vi.advanceTimersByTimeAsync(TIMEOUTS.step);
      await expect(failed).resolves.toMatchObject({ kind: "timeout" });

      s = interviewReducer(s, { type: "TURN_FAILED", message: "Try again" });
      s = interviewReducer(s, { type: "RETRY" });
      await svc.turn({ ...base, turn: s.pending! });
      expect(calls).toHaveLength(2);
      expect(calls[1]?.body).toEqual(calls[0]?.body);
      expect(calls[1]?.body).toMatchObject({ question_id: "q_cpu_performance" });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a reply the backend refuses as out of date (409)", () => {
  const conflict = () => json(409, { detail: "That isn't the question we're on." });
  const turn = {
    criterionId: "cpu_performance",
    questionId: "q_cpu_performance",
    text: "Low",
    revise: false,
  };
  const current = stepOf(
    "question",
    [asked("cpu_performance", "answered", "High"), asked("battery_life", "pending")],
    { key: "battery_life" },
  );

  it("reads the session again and shows the current question with the notice", async () => {
    const { client, calls } = fakeBackend(scripted(conflict(), json(200, sessionWith(current))));
    const r = await createLiveInterviewService(client).turn({ ...base, turn });
    expect(calls.map((c) => [c.method, c.path])).toEqual([
      ["POST", `/api/presearch/${SID}/interview/reply`],
      ["GET", `/api/presearch/${SID}`],
    ]);
    expect(r.notice).toBe(ALREADY_ANSWERED);
    expect(r.notice).toBe("This question was already answered. Here is where you are.");
    expect(r.step).toMatchObject({
      kind: "question",
      criterionId: "battery_life",
      questionId: "q_battery_life",
      question: current.question!.text,
    });
    expect(r.answers).toEqual({ cpu_performance: { status: "answered", value: "High" } });
    expect(r.laterChanged).toBe(false);
  });

  it("the screen moves on, and the notice goes with the next reply", async () => {
    const { client } = fakeBackend(scripted(conflict(), json(200, sessionWith(current))));
    const result = await createLiveInterviewService(client).turn({ ...base, turn });
    let s = interviewReducer(
      {
        ...initialInterview,
        phase: "sending",
        criteria: viewCriteria,
        step: viewStepFor("cpu_performance"),
        pending: turn,
      },
      { type: "TURN_OK", result },
    );
    expect(s).toMatchObject({
      phase: "asking",
      notice: ALREADY_ANSWERED,
      pending: null,
      error: null,
    });
    expect(s.step?.criterionId).toBe("battery_life");
    s = interviewReducer(s, { type: "SEND", text: "High" });
    expect(s.notice).toBeNull();
    expect(s.pending?.questionId).toBe("q_battery_life");
  });

  it("if the interview has finished, it says so and keeps the finished weights", async () => {
    const { client } = fakeBackend(scripted(conflict(), json(200, sessionWith(STEPS.finished))));
    const r = await createLiveInterviewService(client).turn({ ...base, turn });
    expect(r.step.kind).toBe("finished");
    expect(r.notice).toBe(ALREADY_ANSWERED);
    expect(r.rubric).toHaveLength(rubric.weights.length);
  });

  it("going back to a question the backend refuses recovers the same way", async () => {
    const { client, calls } = fakeBackend(scripted(conflict(), json(200, sessionWith(current))));
    const r = await createLiveInterviewService(client).turn({
      ...base,
      turn: { ...turn, revise: true },
    });
    expect(calls.map((c) => c.method)).toEqual(["POST", "GET"]);
    expect(calls[0]?.path).toBe(`/api/presearch/${SID}/interview/back`);
    expect(r.notice).toBe(ALREADY_ANSWERED);
    expect(r.laterChanged).toBe(false);
  });

  it("with no interview to show, the backend's own sentence stands", async () => {
    const detail = "The interview hasn't started yet. Please start it first.";
    const { client } = fakeBackend(scripted(json(409, { detail }), json(200, sessionWith(null))));
    await expect(createLiveInterviewService(client).turn({ ...base, turn })).rejects.toMatchObject({
      kind: "conflict",
      message: detail,
    });
  });

  it("an unreadable session is the usual clear error, never a guess", async () => {
    const broken = { ...sessionWith(current), interview_step: { kind: "chat" } };
    const { client } = fakeBackend(scripted(conflict(), json(200, broken)));
    await expect(createLiveInterviewService(client).turn({ ...base, turn })).rejects.toMatchObject({
      kind: "bad_response",
      message: MESSAGES.badResponse,
    });
  });

  it("any other failure is not treated as a conflict", async () => {
    const { client, calls } = fakeBackend(() => json(500, {}));
    await expect(createLiveInterviewService(client).turn({ ...base, turn })).rejects.toMatchObject({
      kind: "server",
    });
    expect(calls).toHaveLength(1);
  });
});
