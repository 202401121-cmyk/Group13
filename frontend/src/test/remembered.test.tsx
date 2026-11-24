/**
 * US-06 in the interview (S5-12 part A, DECIDED 1): each remembered answer shows as an answered turn labelled
 * "Remembered from <date>", with "Change" (its quick replies again) and "Forget" (forgotten for the session, then
 * deleted from memory by S5-9's route). Made-up requests only; live mode through the fake backend.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnswersSoFar } from "@/components/interview/Interview";
import {
  createLiveInterviewService,
  rememberedFrom,
  rememberedSince,
} from "@/lib/interview/liveAdapter";
import {
  createMockInterviewService,
  criteriaFor,
  forgottenIn,
  MEMORY_PREFERENCES,
  runForget,
  runTurn,
} from "@/lib/interview/mockService";
import {
  initialInterview,
  interviewReducer,
  keepDates,
  toInterviewSnapshot,
  type InterviewState,
} from "@/lib/interview/reducer";
import type { Answers, RememberedView, TurnInput } from "@/lib/interview/types";
import { rememberedFromText } from "@/lib/interview/view";
import type { Brief } from "@/lib/request/types";
import { rubricFromInterview } from "@/lib/results/rubric";
import { fakeBackend, json, scripted } from "./helpers/fakeBackend";
import { asked, sessionWith, SID, stepOf } from "./helpers/interviewSteps";

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
const WEIGHT_PREF = MEMORY_PREFERENCES["weight"]!.id;

/** The mock interview after three answers: the laptop's weight comes next, and is remembered. */
function remembered(): { input: TurnInput; answers: Answers } {
  let answers: Answers = {};
  for (const id of ["cpu", "memory", "battery"]) {
    answers = runTurn({
      brief,
      criteria,
      answers,
      turn: { criterionId: id, text: "High", revise: false },
    }).answers;
  }
  return { input: { brief, criteria, answers, sessionId: SID }, answers };
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("the mock remembers, and forgets for the session", () => {
  it("shows a remembered answer with its preference and the day it was remembered from", () => {
    const { answers } = remembered();
    expect(answers["weight"]).toEqual({ status: "remembered", value: "Very important" });
    const r = runTurn({ brief, criteria, answers });
    expect(r.remembered).toEqual([
      {
        preferenceId: WEIGHT_PREF,
        kind: "weight",
        text: "Weight: Very important",
        criterionId: "weight",
        since: "2026-11-14",
      },
    ]);
  });

  it("asks a forgotten answer's question instead, and never remembers it again in the session", () => {
    const { input } = remembered();
    const r = runForget(input, WEIGHT_PREF);
    expect(r.answers["weight"]).toBeUndefined();
    expect(r.step.criterionId).toBe("weight");
    expect(r.remembered).toEqual([]);
    const later = runTurn({ ...input, answers: r.answers, forgotten: ["weight"] });
    expect(later.answers["weight"]).toBeUndefined();
  });

  it("keeps a forgotten answer gone after a reload", async () => {
    const { input } = remembered();
    await createMockInterviewService(0).forget(input, WEIGHT_PREF);
    expect(forgottenIn(SID)).toEqual(["weight"]);
    // A reload: a new service, and the answers saved without the forgotten one.
    const { weight: _gone, ...kept } = input.answers;
    const after = await createMockInterviewService(0).turn({ ...input, answers: kept });
    expect(after.answers["weight"]).toBeUndefined();
    expect(after.step.criterionId).toBe("weight");
  });

  it("refuses to forget what this interview doesn't remember", () => {
    expect(() => runForget(remembered().input, "not-a-preference")).toThrow(
      "That isn't one of the remembered preferences in this interview.",
    );
  });

  it("a remembered answer changed updates the rubric: it is the shopper's answer now", () => {
    const { answers } = remembered();
    expect(
      rubricFromInterview({ criteria, answers }).find((i) => i.criterionId === "weight"),
    ).toMatchObject({ origin: "remembered" });
    const changed = runTurn({
      brief,
      criteria,
      answers,
      turn: { criterionId: "weight", text: "Low", revise: true },
    }).answers;
    expect(changed["weight"]).toEqual({ status: "answered", value: "Low" });
    const before = rubricFromInterview({ criteria, answers }).find(
      (i) => i.criterionId === "weight",
    );
    const after = rubricFromInterview({ criteria, answers: changed }).find(
      (i) => i.criterionId === "weight",
    );
    expect(after).toMatchObject({ origin: "answer" });
    expect(after!.weight).not.toBe(before!.weight);
  });
});

describe("live: what the backend remembers", () => {
  const memory = {
    preference_id: "3f1c2a7e-0000-4000-8000-000000000001",
    kind: "weight" as const,
    text: "Weight: high importance",
    criterion_key: "weight",
    weight: 8,
    similarity: 0.91,
    age_days: 9,
  };

  it("dates each remembered preference by its age", () => {
    const now = Date.parse("2026-11-23T10:00:00Z");
    expect(rememberedSince(9, now)).toBe("2026-11-14");
    const step = stepOf("question", [asked("weight", "remembered")], { remembered: [memory] });
    expect(rememberedFrom(step.state, now)).toEqual([
      {
        preferenceId: memory.preference_id,
        kind: "weight",
        text: "Weight: high importance",
        criterionId: "weight",
        since: "2026-11-14",
      },
    ]);
  });

  it("forgets through the interview's forget route, and asks the question next", async () => {
    const after = stepOf("question", [asked("cpu_performance", "answered", "High")], {
      key: "weight",
    });
    const { client, calls } = fakeBackend(scripted(json(200, after)));
    const r = await createLiveInterviewService(client).forget(
      { brief, criteria: [], answers: {}, sessionId: SID },
      memory.preference_id,
    );
    expect(calls.map((c) => [c.method, c.path, c.body])).toEqual([
      ["POST", `/api/presearch/${SID}/interview/forget`, { preference_id: memory.preference_id }],
    ]);
    expect(r.step.criterionId).toBe("weight");
    expect(r.remembered).toEqual([]);
  });

  it("changes a remembered answer by reopening its question, then replying: an answer from then on", async () => {
    const before = stepOf("question", [asked("weight", "remembered")], {
      key: "cpu_performance",
      remembered: [memory],
    });
    const reopened = stepOf("question", [asked("weight", "pending")], { key: "weight" });
    const answered = stepOf("finished", [asked("weight", "answered", "Low")]);
    const { client, calls } = fakeBackend(
      scripted(json(200, before), json(200, reopened), json(200, answered)),
    );
    const live = createLiveInterviewService(client);
    await live.turn({ brief, criteria: [], answers: {}, sessionId: SID });
    const r = await live.turn({
      brief,
      criteria: [],
      answers: { weight: { status: "remembered", value: "Weight: high importance" } },
      sessionId: SID,
      turn: { criterionId: "weight", questionId: "q_weight", text: "Low", revise: true },
    });
    expect(calls.map((c) => c.path.split("/").at(-1))).toEqual(["start", "back", "reply"]);
    expect(r.answers["weight"]).toEqual({ status: "answered", value: "Low" });
  });
});

describe("Forget: for the session, then from memory", () => {
  const view: RememberedView = {
    preferenceId: WEIGHT_PREF,
    kind: "weight",
    text: "Weight: Very important",
    criterionId: "weight",
    since: "2026-11-14",
  };

  async function forgetWith(deleteResult: () => Promise<void>) {
    vi.resetModules();
    const forget = vi.fn(deleteResult);
    vi.doMock("@/lib/account/memoryService", () => ({ getMemoryService: () => ({ forget }) }));
    const mock = createMockInterviewService(0);
    vi.doMock("@/lib/interview/service", () => ({ getInterviewService: () => mock }));
    const hook = await import("@/lib/interview/useInterview");
    const { input } = remembered();
    const result = await hook.forgetEverywhere(input, view, "Weight");
    vi.doUnmock("@/lib/account/memoryService");
    vi.doUnmock("@/lib/interview/service");
    return { result, forget };
  }

  it("deletes it from what is remembered, and says it is forgotten", async () => {
    const { result, forget } = await forgetWith(() => Promise.resolve());
    expect(forget).toHaveBeenCalledWith(WEIGHT_PREF);
    expect(result.notice).toBe("Forgotten: Weight. We won't use it again.");
    expect(result.step.criterionId).toBe("weight");
  });

  it("counts one already gone from memory as deleted", async () => {
    const { result } = await forgetWith(() => Promise.reject({ kind: "notFound" }));
    expect(result.notice).toBe("Forgotten: Weight. We won't use it again.");
  });

  it("keeps it forgotten for this search when the delete fails, and says where to remove it", async () => {
    const { result } = await forgetWith(() => Promise.reject({ kind: "unavailable" }));
    expect(result.notice).toBe(
      "Forgotten for this search: Weight. We couldn't remove it from what we remember, so you can do that on your profile.",
    );
    expect(result.answers["weight"]).toBeUndefined();
  });
});

describe("the reducer", () => {
  function asking(): InterviewState {
    const { answers } = remembered();
    const r = runTurn({ brief, criteria, answers });
    let s = interviewReducer(initialInterview, {
      type: "CRITERIA_LOADED",
      criteria,
      fallback: false,
    });
    s = interviewReducer(s, { type: "START" });
    return interviewReducer(s, { type: "TURN_OK", result: r });
  }

  it("forgets only what it remembers, one at a time, and tries the same forget again after a failure", () => {
    const s = asking();
    expect(s.remembered.map((r) => r.preferenceId)).toEqual([WEIGHT_PREF]);
    expect(interviewReducer(s, { type: "FORGET", preferenceId: "other" })).toBe(s);
    const sending = interviewReducer(s, { type: "FORGET", preferenceId: WEIGHT_PREF });
    expect(sending).toMatchObject({ phase: "sending", forgetting: WEIGHT_PREF, pending: null });
    const failed = interviewReducer(sending, { type: "TURN_FAILED", message: "No connection." });
    expect(interviewReducer(failed, { type: "RETRY" })).toMatchObject({
      phase: "sending",
      forgetting: WEIGHT_PREF,
    });
    const done = interviewReducer(sending, {
      type: "TURN_OK",
      result: runForget({ brief, criteria, answers: s.answers }, WEIGHT_PREF),
    });
    expect(done).toMatchObject({ phase: "asking", forgetting: null, remembered: [] });
  });
});

describe("on the screen", () => {
  function shown(state: Partial<InterviewState>) {
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(
      <AnswersSoFar
        state={{ ...initialInterview, phase: "asking", criteria, ...state }}
        onChange={() => {}}
        onForget={() => {}}
      />,
    );
    return host;
  }

  it("marks a remembered answer with its day, and offers Change and Forget, each named", () => {
    const { answers } = remembered();
    const host = shown({
      answers,
      remembered: runTurn({ brief, criteria, answers }).remembered!,
    });
    const row = host.querySelector("[data-remembered]")!;
    expect(row.textContent).toContain("Weight");
    expect(row.textContent).toContain("Very important");
    // The year shows once it isn't this year's.
    expect(row.querySelector("[data-remembered-from]")?.textContent).toMatch(
      /^Remembered from 14 Nov( 2026)?$/,
    );
    expect([...row.querySelectorAll("button")].map((b) => b.getAttribute("aria-label"))).toEqual([
      "Change Weight",
      "Forget Weight",
    ]);
  });

  it("lists a remembered must-have or leave-out apart, each with Forget", () => {
    const host = shown({
      remembered: [
        {
          preferenceId: "p2",
          kind: "hard_rule",
          text: "Must have: at least 16 GB RAM",
          criterionId: null,
          since: "2026-11-02",
        },
      ],
    });
    const section = host.querySelector('[aria-labelledby="also-remembered"]')!;
    expect(section.textContent).toContain("Must have: at least 16 GB RAM");
    expect(section.textContent).toMatch(/Remembered from 2 Nov( 2026)?/);
    expect(section.querySelector("button")?.getAttribute("aria-label")).toBe(
      "Forget Must have: at least 16 GB RAM",
    );
  });

  it("names the year of an older one", () => {
    expect(rememberedFromText("2025-12-30", new Date("2026-11-23T00:00:00Z"))).toBe(
      "Remembered from 30 Dec 2025",
    );
  });
});

describe("after the review", () => {
  function asking(): InterviewState {
    const { answers } = remembered();
    const r = runTurn({ brief, criteria, answers });
    let s = interviewReducer(initialInterview, {
      type: "CRITERIA_LOADED",
      criteria,
      fallback: false,
    });
    s = interviewReducer(s, { type: "START" });
    return interviewReducer(s, { type: "TURN_OK", result: r });
  }

  it("a reply after a failed Forget is sent as the reply, never as that forget again", () => {
    const failed = interviewReducer(
      interviewReducer(asking(), { type: "FORGET", preferenceId: WEIGHT_PREF }),
      { type: "TURN_FAILED", message: "No connection." },
    );
    const sent = interviewReducer(failed, { type: "SEND", text: "High" });
    expect(sent).toMatchObject({ phase: "sending", forgetting: null });
    expect(sent.pending).toMatchObject({ text: "High" });
    const back = interviewReducer(failed, { type: "GO_BACK", criterionId: "cpu" });
    expect(back.forgetting).toBeNull();
  });

  it("never forgets while a reply waits to be tried again: the reply goes first", () => {
    const sending = interviewReducer(asking(), { type: "SEND", text: "lighter is better" });
    const failed = interviewReducer(sending, { type: "TURN_FAILED", message: "No connection." });
    expect(interviewReducer(failed, { type: "FORGET", preferenceId: WEIGHT_PREF })).toBe(failed);
  });

  it("a forget sent from the finished brief, failed and reloaded, restores the brief", () => {
    const finished: InterviewState = {
      ...asking(),
      phase: "finished",
      step: {
        kind: "finished",
        criterionId: null,
        question: null,
        note: null,
        why: null,
        replies: [],
        terms: [],
      },
    };
    const sending = interviewReducer(finished, { type: "FORGET", preferenceId: WEIGHT_PREF });
    expect(sending.phase).toBe("sending");
    expect(toInterviewSnapshot(sending).phase).toBe("finished");
    const failed = interviewReducer(sending, { type: "TURN_FAILED", message: "No connection." });
    expect(toInterviewSnapshot(failed).phase).toBe("finished");
  });

  it("keeps the day first shown for a remembered preference, so the label never changes mid-interview", () => {
    const first = [{ ...asking().remembered[0]!, since: "2026-11-14" }];
    const later = [{ ...first[0]!, since: "2026-11-15" }];
    expect(keepDates(first, later)[0]!.since).toBe("2026-11-14");
  });

  it("dates a remembered preference in the shopper's own time zone", () => {
    const now = new Date(2026, 10, 23, 1, 30).getTime(); // 01:30 local on 23 Nov
    expect(rememberedSince(1, now)).toBe("2026-11-22");
  });

  it("a remembered weight the shopper changed is never listed again", () => {
    const { answers } = remembered();
    const remembered_ = runTurn({ brief, criteria, answers }).remembered!;
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(
      <AnswersSoFar
        state={{
          ...initialInterview,
          phase: "asking",
          criteria,
          answers: { ...answers, weight: { status: "answered", value: "Low" } },
          remembered: remembered_, // live: the backend still lists it
        }}
        onChange={() => {}}
        onForget={() => {}}
      />,
    );
    expect(host.querySelector("[data-remembered]")).toBeNull();
    expect(host.textContent).not.toContain("Also remembered");
  });

  it("live: a forget whose answer was lost has happened, and the step the backend is on stands", async () => {
    const memory = {
      preference_id: "3f1c2a7e-0000-4000-8000-000000000001",
      kind: "weight" as const,
      text: "Weight: high importance",
      criterion_key: "weight",
      weight: 8,
      similarity: 0.91,
      age_days: 9,
    };
    const now = stepOf("question", [asked("cpu_performance", "answered", "High")], {
      key: "weight",
    });
    // A body is read once, so each backend gets its own answer.
    const gone = () =>
      json(404, { detail: "That isn't one of the remembered preferences in this interview." });
    const { client } = fakeBackend(scripted(gone(), json(200, sessionWith(now))));
    const r = await createLiveInterviewService(client).forget(
      { brief, criteria: [], answers: {}, sessionId: SID },
      memory.preference_id,
    );
    expect(r.step.criterionId).toBe("weight");
    // Still remembered on the backend: the 404 was something else, and it says so.
    const still = stepOf("question", [asked("weight", "remembered")], { remembered: [memory] });
    const { client: other } = fakeBackend(scripted(gone(), json(200, sessionWith(still))));
    await expect(
      createLiveInterviewService(other).forget(
        { brief, criteria: [], answers: {}, sessionId: SID },
        memory.preference_id,
      ),
    ).rejects.toThrow("That isn't one of the remembered preferences in this interview.");
  });
});
